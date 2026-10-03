"""自研 Redis checkpointer（只用普通命令，不依赖 RedisJSON/RediSearch）。

**为什么不用官方 `langgraph-checkpoint-redis`**：它的 `RedisSaver` / `AsyncShallowRedisSaver`
都用 `redisvl` 建二级索引，强依赖 **RedisJSON + RediSearch**；而本机（Windows）能拿到的 Redis
没有这两个模块（实测 `JSON.SET`/`FT._LIST` 均 unknown command）。详见 docs/DECISIONS.md D9。

**我们只需要"每个会话的最新状态 + TTL"**，不需要时间旅行与跨会话检索，所以自己实现很小：
骨架照官方 `InMemorySaver` 的语义（它是权威参考实现），只是把内存字典换成 Redis 命令。

Redis key 布局（均带 `waynboot:agent:ckpt:` 前缀与会话 TTL）::

    {p}{tid}:latest              STRING  → 最新 checkpoint_id
    {p}{tid}:index               ZSET    → member=checkpoint_id, score=写入时间(ms)，用于按时间倒序列出
    {p}{tid}:cp:{cp_id}          HASH    → checkpoint / metadata / parent / created_at
    {p}{tid}:writes:{cp_id}      HASH    → field={task_id}|{序号}，用于 interrupt 的 pending writes

值统一用 ``{"t": 序列化类型, "d": base64 数据}`` 存储：LangGraph 的 serde 是"类型 + 字节"
二元组，转成 base64 后整条记录就是一个可读、可用 redis-cli 直接看的 JSON——牺牲 33% 体积换可观测性。

**只保留最近 N 个 checkpoint**（默认 20）：会话越长体积越大，而历史版本对我们的客服场景没用。
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
)
from redis.asyncio import Redis

from app.config import Settings
from app.observability import get_logger

logger = get_logger(__name__)

#: 每个会话最多保留多少个 checkpoint（多余的从最旧开始删）
DEFAULT_MAX_CHECKPOINTS = 20


class RedisCheckpointSaver(BaseCheckpointSaver[str]):
    """把 LangGraph 的会话状态存在普通 Redis 上（只支持异步调用）。"""

    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "waynboot:agent:ckpt:",
        ttl_seconds: int = 86400,
        max_checkpoints: int = DEFAULT_MAX_CHECKPOINTS,
    ) -> None:
        super().__init__()
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds
        self._max_checkpoints = max_checkpoints

    @classmethod
    def from_settings(cls, settings: Settings) -> RedisCheckpointSaver:
        """按配置创建（复用 mall 的 Redis 实例，独立前缀）。"""
        client = Redis.from_url(settings.redis_url, decode_responses=False)
        return cls(
            client,
            prefix=f"{settings.redis_key_prefix}ckpt:",
            ttl_seconds=settings.session_ttl_seconds,
        )

    async def aclose(self) -> None:
        """关闭底层连接池。"""
        await self._redis.aclose()

    async def aping(self) -> bool:
        """探活：Redis 是否可达（供 /readyz 使用）。"""
        try:
            return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001 —— 探活失败就是"不可用"，不需要区分异常类型
            return False

    # ------------------------------------------------------------------ 读
    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """取指定 checkpoint；config 里没给 checkpoint_id 就取最新的那个。"""
        thread_id, checkpoint_ns = _thread_of(config)
        checkpoint_id = get_checkpoint_id(config)
        if not checkpoint_id:
            raw = await self._redis.get(self._latest_key(thread_id))
            if not raw:
                return None
            checkpoint_id = _as_str(raw)

        stored = await self._redis.hgetall(self._cp_key(thread_id, checkpoint_ns, checkpoint_id))
        if not stored:
            return None

        checkpoint: Checkpoint = self.serde.loads_typed(_unpack(stored[b"checkpoint"]))
        metadata: CheckpointMetadata = self.serde.loads_typed(_unpack(stored[b"metadata"]))
        parent_id = _as_str(stored.get(b"parent", b""))

        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            pending_writes=await self._load_writes(thread_id, checkpoint_ns, checkpoint_id),
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_id,
                    }
                }
                if parent_id
                else None
            ),
        )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """按时间倒序列出某会话的 checkpoint（``before`` 目前只支持按 id 截断）。"""
        if not config:
            return
        thread_id, checkpoint_ns = _thread_of(config)
        before_id = get_checkpoint_id(before) if before else None

        checkpoint_ids = await self._redis.zrevrange(self._index_key(thread_id), 0, -1)
        yielded = 0
        for raw_id in checkpoint_ids:
            checkpoint_id = _as_str(raw_id)
            if before_id and checkpoint_id >= before_id:
                continue
            item = await self.aget_tuple(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": checkpoint_id,
                    }
                }
            )
            if item is not None:
                if filter and not all(item.metadata.get(key) == value for key, value in filter.items()):
                    continue
                yield item
                yielded += 1
                if limit is not None and yielded >= limit:
                    return

    # ------------------------------------------------------------------ 写
    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """写入一个 checkpoint，并把"最新指针"与本会话的 TTL 一起刷新。"""
        thread_id, checkpoint_ns = _thread_of(config)
        checkpoint_id = checkpoint["id"]
        parent_id = get_checkpoint_id(config) or ""

        key = self._cp_key(thread_id, checkpoint_ns, checkpoint_id)
        pipe = self._redis.pipeline()
        pipe.hset(
            key,
            mapping={
                b"checkpoint": _pack(self.serde.dumps_typed(checkpoint)),
                b"metadata": _pack(self.serde.dumps_typed(metadata)),
                b"parent": parent_id,
                b"created_at": str(int(time.time() * 1000)),
            },
        )
        pipe.expire(key, self._ttl)
        pipe.zadd(self._index_key(thread_id), {checkpoint_id: time.time()})
        pipe.expire(self._index_key(thread_id), self._ttl)
        pipe.set(self._latest_key(thread_id), checkpoint_id, ex=self._ttl)
        await pipe.execute()

        await self._trim(thread_id)

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """写入待处理写入（``interrupt`` 恢复就靠它）。

        ``writes`` 是 ``(channel, value)`` 序列；``task_id`` 用于去重，
        官方实现按 ``(task_id, 序号)`` 覆盖写，这里保持一致。
        """
        thread_id, checkpoint_ns = _thread_of(config)
        checkpoint_id = get_checkpoint_id(config)
        if not checkpoint_id:
            raise ValueError("aput_writes 需要 config 里带 checkpoint_id")

        key = self._writes_key(thread_id, checkpoint_ns, checkpoint_id)
        mapping: dict[str, str] = {}
        now = time.time()
        for index, (channel, value) in enumerate(writes):
            mapping[f"{task_id}|{index:08d}"] = json.dumps(
                {
                    "task_id": task_id,
                    "channel": channel,
                    "value": _pack(self.serde.dumps_typed(value)),
                    "task_path": task_path,
                    "created_at": now,
                },
                ensure_ascii=False,
            )
        pipe = self._redis.pipeline()
        pipe.hset(key, mapping=mapping)
        pipe.expire(key, self._ttl)
        await pipe.execute()

    async def adelete_thread(self, thread_id: str) -> None:
        """删除整个会话（会议管理接口的"清空会话"）。"""
        pattern = f"{self._prefix}{thread_id}:*"
        pipe = self._redis.pipeline()
        async for key in self._redis.scan_iter(match=pattern, count=200):
            pipe.delete(key)
        await pipe.execute()

    # ------------------------------------------------------- 会话管理辅助方法
    async def aget_thread_summary(self, thread_id: str) -> dict[str, Any] | None:
        """会话概览：最新 checkpoint 时间、消息轮数、待处理写入数（供 ``GET /api/sessions/{id}``）。"""
        raw = await self._redis.get(self._latest_key(thread_id))
        if not raw:
            return None
        checkpoint_id = _as_str(raw)
        stored = await self._redis.hgetall(self._cp_key(thread_id, "", checkpoint_id))
        if not stored:
            return None

        checkpoint: Checkpoint = self.serde.loads_typed(_unpack(stored[b"checkpoint"]))
        messages = (checkpoint.get("channel_values") or {}).get("messages") or []
        writes = await self._load_writes(thread_id, "", checkpoint_id)
        return {
            "thread_id": thread_id,
            "checkpoint_id": checkpoint_id,
            "created_at": int(_as_str(stored.get(b"created_at", b"0")) or 0),
            "message_count": len(messages),
            "pending_writes": len(writes),
        }

    # ------------------------------------------------------------------ 内部
    async def _load_writes(
        self, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> list[tuple[str, str, Any]]:
        """读 pending writes，按 ``(task_id, 序号)`` 排序保证顺序稳定。"""
        raw = await self._redis.hgetall(self._writes_key(thread_id, checkpoint_ns, checkpoint_id))
        writes: list[tuple[str, str, Any]] = []
        for field in sorted(raw):
            record = json.loads(raw[field])
            writes.append(
                (record["task_id"], record["channel"], self.serde.loads_typed(_unpack(record["value"])))
            )
        return writes

    async def _trim(self, thread_id: str) -> None:
        """只保留最近 N 个 checkpoint，超出的连同 writes 一起删除。"""
        index_key = self._index_key(thread_id)
        total = await self._redis.zcard(index_key)
        if total <= self._max_checkpoints:
            return
        stale = await self._redis.zrange(index_key, 0, total - self._max_checkpoints - 1)
        pipe = self._redis.pipeline()
        for raw_id in stale:
            checkpoint_id = _as_str(raw_id)
            pipe.delete(self._cp_key(thread_id, "", checkpoint_id))
            pipe.delete(self._writes_key(thread_id, "", checkpoint_id))
            pipe.zrem(index_key, checkpoint_id)
        await pipe.execute()
        logger.debug("会话 %s 历史 checkpoint 超限，清理 %d 个", thread_id, len(stale))

    def _latest_key(self, thread_id: str) -> str:
        return f"{self._prefix}{thread_id}:latest"

    def _index_key(self, thread_id: str) -> str:
        return f"{self._prefix}{thread_id}:index"

    def _cp_key(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
        ns = f"{checkpoint_ns}:" if checkpoint_ns else ""
        return f"{self._prefix}{thread_id}:{ns}cp:{checkpoint_id}"

    def _writes_key(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
        ns = f"{checkpoint_ns}:" if checkpoint_ns else ""
        return f"{self._prefix}{thread_id}:{ns}writes:{checkpoint_id}"

    # ------------------------------------------------- 同步方法：本 saver 只支持异步
    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        raise NotImplementedError("RedisCheckpointSaver 只支持异步调用（aget_tuple / astream / ainvoke）")

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        raise NotImplementedError("RedisCheckpointSaver 只支持异步调用（alist）")

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        raise NotImplementedError("RedisCheckpointSaver 只支持异步调用（aput）")

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        raise NotImplementedError("RedisCheckpointSaver 只支持异步调用（aput_writes）")

    def delete_thread(self, thread_id: str) -> None:
        raise NotImplementedError("RedisCheckpointSaver 只支持异步调用（adelete_thread）")


def _thread_of(config: RunnableConfig) -> tuple[str, str]:
    """从运行配置里取 (thread_id, checkpoint_ns)。"""
    configurable = config["configurable"]
    return configurable["thread_id"], configurable.get("checkpoint_ns", "")


def _pack(serialized: tuple[str, bytes]) -> str:
    """LangGraph 的 (类型, 字节) → 可存 Redis 的 JSON 字符串。"""
    type_name, payload = serialized
    return json.dumps({"t": type_name, "d": base64.b64encode(payload).decode("ascii")})


def _unpack(raw: bytes | str) -> tuple[str, bytes]:
    """上面的逆操作。"""
    record = json.loads(raw)
    return record["t"], base64.b64decode(record["d"])


def _as_str(raw: bytes | str) -> str:
    return raw.decode("utf-8") if isinstance(raw, bytes) else raw
