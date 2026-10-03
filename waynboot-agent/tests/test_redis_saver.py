"""Redis checkpointer 测试。

两层验证：

1. **单元层**（主）：用 ``fakeredis`` 在进程内跑，不依赖本机有没有 Redis（决策 D7）；
2. **集成层**（可选）：本机 Redis 在跑时，额外打一遍真实实例——证明"在真 Redis 上确实能用"，
   而不是只在我自己 mock 出来的世界里能用。Redis 不可达时自动跳过。
"""

import socket
from urllib.parse import urlparse

import pytest
from fakeredis import aioredis
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import Checkpoint

from app.config import get_settings
from app.memory.redis_saver import RedisCheckpointSaver

DEFAULT_REDIS_PORT = 6379


def _redis_reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _configured_redis_reachable() -> bool:
    """探活**配置里那个** Redis（而不是写死 6379）。

    这样把 ``AGENT_REDIS_URL`` 指向别处时集成用例会正确跳过，整套测试就能自证"不依赖外部服务"（决策 D7）。
    """
    url = urlparse(get_settings().redis_url)
    return _redis_reachable(url.hostname or "127.0.0.1", url.port or DEFAULT_REDIS_PORT)


requires_redis = pytest.mark.skipif(
    not _configured_redis_reachable(), reason="配置的 Redis 不可达（集成用例跳过）"
)


def make_checkpoint(checkpoint_id: str, messages: list | None = None) -> Checkpoint:
    """构造一个结构真实的 checkpoint（字段与官方 TypedDict 对齐）。"""
    return Checkpoint(
        v=1,
        id=checkpoint_id,
        ts="2026-09-25T12:00:00+00:00",
        channel_values={"messages": messages or []},
        channel_versions={"messages": int(checkpoint_id.split("-")[-1])},
        versions_seen={"agent": {"messages": 1}},
    )


@pytest.fixture
async def saver():
    """进程内 Redis 版 saver（TTL 设短一点便于断言）。"""
    client = aioredis.FakeRedis(decode_responses=False)
    instance = RedisCheckpointSaver(client, prefix="test:ckpt:", ttl_seconds=600, max_checkpoints=3)
    yield instance
    await instance.aclose()


def config(thread_id: str, checkpoint_id: str | None = None) -> dict:
    configurable = {"thread_id": thread_id}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


# --------------------------------------------------------------------------------------
# 基本读写
# --------------------------------------------------------------------------------------
async def test_get_returns_none_for_unknown_thread(saver) -> None:
    assert await saver.aget_tuple(config("nobody")) is None


async def test_put_then_get_latest_roundtrip(saver) -> None:
    messages = [HumanMessage(content="在吗"), AIMessage(content="在的")]
    await saver.aput(config("u1"), make_checkpoint("cp-1", messages), {"source": "loop"}, {})

    item = await saver.aget_tuple(config("u1"))

    assert item is not None
    assert item.checkpoint["id"] == "cp-1"
    assert item.metadata == {"source": "loop"}
    # 消息被完整反序列化回来（这是"记得住上文"的技术底座）
    restored = item.checkpoint["channel_values"]["messages"]
    assert isinstance(restored[0], HumanMessage)
    assert restored[1].content == "在的"


async def test_get_by_explicit_checkpoint_id(saver) -> None:
    await saver.aput(config("u2"), make_checkpoint("cp-1"), {}, {})
    await saver.aput(config("u2", "cp-1"), make_checkpoint("cp-2"), {}, {})

    latest = await saver.aget_tuple(config("u2"))
    older = await saver.aget_tuple(config("u2", "cp-1"))

    assert latest is not None and latest.checkpoint["id"] == "cp-2"
    assert older is not None and older.checkpoint["id"] == "cp-1"
    # 老 checkpoint 的 parent 指向链上的前一个
    assert latest.parent_config["configurable"]["checkpoint_id"] == "cp-1"


async def test_returns_updated_config_with_checkpoint_id(saver) -> None:
    """aput 必须回写"带 checkpoint_id 的新 config"——图靠它串起父子链。"""
    new_config = await saver.aput(config("u3"), make_checkpoint("cp-9"), {}, {})

    assert new_config["configurable"]["checkpoint_id"] == "cp-9"
    assert new_config["configurable"]["thread_id"] == "u3"


async def test_ttl_is_applied(saver) -> None:
    """会话状态要过期（不然 Redis 会被历史会话撑爆）。"""
    await saver.aput(config("u4"), make_checkpoint("cp-1"), {}, {})

    ttl = await saver._redis.ttl(saver._latest_key("u4"))
    assert 0 < ttl <= 600


# --------------------------------------------------------------------------------------
# pending writes（P4 的 interrupt 恢复靠它）
# --------------------------------------------------------------------------------------
async def test_put_writes_and_read_back_as_pending(saver) -> None:
    await saver.aput(config("u5"), make_checkpoint("cp-1"), {}, {})
    await saver.aput_writes(config("u5", "cp-1"), [("messages", HumanMessage(content="确认退款"))], "task-a")

    item = await saver.aget_tuple(config("u5"))

    assert item is not None
    assert item.pending_writes is not None
    task_id, channel, value = item.pending_writes[0]
    assert (task_id, channel) == ("task-a", "messages")
    assert value.content == "确认退款"


async def test_put_writes_requires_checkpoint_id(saver) -> None:
    with pytest.raises(ValueError, match="checkpoint_id"):
        await saver.aput_writes(config("u6"), [("messages", "x")], "task-a")


async def test_writes_are_ordered_by_task_and_index(saver) -> None:
    await saver.aput(config("u7"), make_checkpoint("cp-1"), {}, {})
    await saver.aput_writes(config("u7", "cp-1"), [("b", 1)], "task-2")
    await saver.aput_writes(config("u7", "cp-1"), [("a", 1), ("a", 2)], "task-1")

    item = await saver.aget_tuple(config("u7"))

    assert item is not None and item.pending_writes is not None
    assert [(task, channel) for task, channel, _ in item.pending_writes] == [
        ("task-1", "a"),
        ("task-1", "a"),
        ("task-2", "b"),
    ]


# --------------------------------------------------------------------------------------
# 历史列表 / 裁剪 / 删除
# --------------------------------------------------------------------------------------
async def test_list_returns_newest_first(saver) -> None:
    for index in range(3):
        await saver.aput(config("u8"), make_checkpoint(f"cp-{index + 1}"), {}, {})

    listed = [item.checkpoint["id"] async for item in saver.alist(config("u8"))]

    assert listed == ["cp-3", "cp-2", "cp-1"]


async def test_list_honours_limit(saver) -> None:
    for index in range(3):
        await saver.aput(config("u9"), make_checkpoint(f"cp-{index + 1}"), {}, {})

    listed = [item.checkpoint["id"] async for item in saver.alist(config("u9"), limit=2)]

    assert listed == ["cp-3", "cp-2"]


async def test_list_filters_by_metadata(saver) -> None:
    await saver.aput(config("u10"), make_checkpoint("cp-1"), {"source": "loop"}, {})
    await saver.aput(config("u10"), make_checkpoint("cp-2"), {"source": "update"}, {})

    listed = [item.checkpoint["id"] async for item in saver.alist(config("u10"), filter={"source": "update"})]

    assert listed == ["cp-2"]


async def test_trim_keeps_only_recent_checkpoints(saver) -> None:
    """max_checkpoints=3：写 5 个后只留最新 3 个，且旧 key 真被删掉（不只是从索引里摘掉）。"""
    for index in range(5):
        await saver.aput(config("u11"), make_checkpoint(f"cp-{index + 1}"), {}, {})

    listed = [item.checkpoint["id"] async for item in saver.alist(config("u11"))]
    total_keys = await saver._redis.zcard(saver._index_key("u11"))
    stale_exists = await saver._redis.exists(saver._cp_key("u11", "", "cp-1"))

    assert listed == ["cp-5", "cp-4", "cp-3"]
    assert total_keys == 3
    assert stale_exists == 0


async def test_delete_thread_removes_everything(saver) -> None:
    await saver.aput(config("u12"), make_checkpoint("cp-1"), {}, {})
    await saver.aput_writes(config("u12", "cp-1"), [("messages", "x")], "task-a")

    await saver.adelete_thread("u12")

    assert await saver.aget_tuple(config("u12")) is None
    assert await saver.aget_thread_summary("u12") is None


async def test_threads_are_isolated(saver) -> None:
    await saver.aput(config("alice"), make_checkpoint("cp-1", [HumanMessage(content="我是 alice")]), {}, {})
    await saver.aput(config("bob"), make_checkpoint("cp-1", [HumanMessage(content="我是 bob")]), {}, {})

    alice = await saver.aget_thread_summary("alice")
    bob = await saver.aget_tuple(config("bob"))

    assert alice is not None and alice["message_count"] == 1
    assert bob is not None and bob.checkpoint["channel_values"]["messages"][0].content == "我是 bob"


# --------------------------------------------------------------------------------------
# 会话概览（供 GET /api/sessions/{id}）
# --------------------------------------------------------------------------------------
async def test_summary_reports_message_and_pending_write_counts(saver) -> None:
    messages = [HumanMessage(content="你好"), AIMessage(content="在的"), HumanMessage(content="退货")]
    await saver.aput(config("u13"), make_checkpoint("cp-1", messages), {}, {})
    await saver.aput_writes(config("u13", "cp-1"), [("messages", "x")], "task-a")

    summary = await saver.aget_thread_summary("u13")

    assert summary is not None
    assert summary["thread_id"] == "u13"
    assert summary["checkpoint_id"] == "cp-1"
    assert summary["message_count"] == 3
    assert summary["pending_writes"] == 1
    assert summary["created_at"] > 0


# --------------------------------------------------------------------------------------
# 同步方法必须明确报错（避免"静默走错路径"）
# --------------------------------------------------------------------------------------
def test_sync_methods_raise_with_clear_message(saver) -> None:
    with pytest.raises(NotImplementedError, match="只支持异步"):
        saver.get_tuple(config("u14"))


# --------------------------------------------------------------------------------------
# 集成层：真 Redis
# --------------------------------------------------------------------------------------
@requires_redis
async def test_roundtrip_against_real_redis() -> None:
    """本机真 Redis（8.10.2 社区构建）上跑一遍读写，证明不是"只在自己 mock 的世界里能用"。"""
    from app.config import get_settings
    from app.memory import RedisCheckpointSaver as Saver

    settings = get_settings()
    instance = Saver.from_settings(settings)
    thread_id = "itest-real-redis"
    try:
        await instance.adelete_thread(thread_id)
        await instance.aput(
            config(thread_id),
            make_checkpoint("cp-1", [HumanMessage(content="真 Redis 上的一句话")]),
            {"source": "integration"},
            {},
        )
        await instance.aput_writes(config(thread_id, "cp-1"), [("messages", "待确认")], "task-a")

        item = await instance.aget_tuple(config(thread_id))
        summary = await instance.aget_thread_summary(thread_id)

        assert item is not None
        assert item.checkpoint["channel_values"]["messages"][0].content == "真 Redis 上的一句话"
        assert item.pending_writes is not None and item.pending_writes[0][1] == "messages"
        assert summary is not None and summary["message_count"] == 1

        # key 前缀确实落在独立命名空间里（不与 mall 的业务 key 混）
        keys = [
            key async for key in instance._redis.scan_iter(match=f"{settings.redis_key_prefix}*", count=100)
        ]
        assert keys, "至少应能看到自己写的 key"
        assert all(key.decode().startswith(settings.redis_key_prefix) for key in keys)
    finally:
        await instance.adelete_thread(thread_id)
        await instance.aclose()
