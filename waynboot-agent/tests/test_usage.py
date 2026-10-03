"""token 用量统计测试。

三层各测一段：

1. **回调**：能从 LangChain 的 LLMResult 里累计用量（含"模型没返回用量"的情况）；
2. **累加器**：按身份 + 天累加，会过期，且统计失败不影响对话；
3. **接口**：``/api/usage`` 只能看到自己的账。

⚠️ 为什么不用 ScriptedLLM 跑"对话 → 统计"的端到端：伪造的假模型不是 LangChain Runnable，
调用它**不会触发回调**（用量恒为 0）。真模型路径是实测过的（见 docs/PLAN.md 进度日志）。
"""

from datetime import date, timedelta

import httpx
import pytest
from fakeredis import aioredis
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

import app.main as main_module
from app.graph.usage import TurnUsage, UsageCallback
from app.main import app
from app.memory.identity import caller_identity
from app.memory.usage import UsageTracker
from tests.fakes import ScriptedLLM


@pytest.fixture
async def tracker():
    instance = UsageTracker(
        aioredis.FakeRedis(decode_responses=False), prefix="test:usage:", retention_days=7
    )
    yield instance
    await instance.aclose()


# --------------------------------------------------------------------------------------
# 1. 回调
# --------------------------------------------------------------------------------------
async def test_callback_accumulates_across_calls() -> None:
    """一轮对话里 LLM 可能被调用多次（工具循环），用量必须累加而不是覆盖。"""
    callback = UsageCallback()

    await callback.on_llm_end(_llm_result(input_tokens=100, output_tokens=20))
    await callback.on_llm_end(_llm_result(input_tokens=150, output_tokens=30))

    assert callback.usage.calls == 2
    assert callback.usage.input_tokens == 250
    assert callback.usage.output_tokens == 50
    assert callback.usage.total_tokens == 300


async def test_callback_tolerates_missing_usage_metadata() -> None:
    """模型不返回用量时只记调用次数，不能崩。"""
    callback = UsageCallback()

    await callback.on_llm_end(_llm_result(input_tokens=None, output_tokens=None))

    assert callback.usage.calls == 1
    assert callback.usage.total_tokens == 0


def _llm_result(*, input_tokens: int | None, output_tokens: int | None) -> LLMResult:
    metadata = None
    if input_tokens is not None or output_tokens is not None:
        metadata = {
            "input_tokens": input_tokens or 0,
            "output_tokens": output_tokens or 0,
            "total_tokens": (input_tokens or 0) + (output_tokens or 0),
        }
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok", usage_metadata=metadata))]])


# --------------------------------------------------------------------------------------
# 2. 累加器
# --------------------------------------------------------------------------------------
async def test_tracker_accumulates_per_identity_and_day(tracker) -> None:
    await tracker.arecord("alice", TurnUsage(calls=1, input_tokens=100, output_tokens=20))
    await tracker.arecord("alice", TurnUsage(calls=2, input_tokens=50, output_tokens=10))
    await tracker.arecord("bob", TurnUsage(calls=1, input_tokens=999, output_tokens=1))

    alice = await tracker.aread("alice", days=1)
    bob = await tracker.aread("bob", days=1)

    assert alice["daily"][0]["date"] == date.today().isoformat()
    assert alice["daily"][0] == {
        "date": date.today().isoformat(),
        "calls": 3,
        "input_tokens": 150,
        "output_tokens": 30,
        "total_tokens": 180,
    }
    assert alice["total"]["calls"] == 3
    assert alice["total"]["total_tokens"] == 180
    assert bob["total"]["input_tokens"] == 999  # 各记各的


async def test_tracker_skips_zero_call_turns(tracker) -> None:
    """没真正调用 LLM 的轮次（降级话术）不产生记录。"""
    await tracker.arecord("alice", TurnUsage())

    result = await tracker.aread("alice", days=1)

    assert result["total"]["calls"] == 0


async def test_tracker_sets_retention_ttl(tracker) -> None:
    await tracker.arecord("alice", TurnUsage(calls=1))

    ttl = await tracker._redis.ttl(f"{tracker._prefix}alice:{date.today().strftime('%Y%m%d')}")

    assert 6 * 86400 < ttl <= 7 * 86400


async def test_aread_covers_previous_days(tracker) -> None:
    """往前看几天：昨天有记录、今天没有，total 也要算上。"""
    yesterday = date.today() - timedelta(days=1)
    await tracker._redis.hset(f"{tracker._prefix}alice:{yesterday.strftime('%Y%m%d')}", mapping={"calls": 5})

    result = await tracker.aread("alice", days=2)

    assert [item["date"] for item in result["daily"]] == [date.today().isoformat(), yesterday.isoformat()]
    assert result["total"]["calls"] == 5


async def test_record_failure_does_not_raise() -> None:
    """统计是观测手段，不该成为故障点：Redis 挂了也不许影响对话。"""
    tracker = UsageTracker(aioredis.FakeRedis(decode_responses=False), prefix="test:usage:")
    await tracker._redis.aclose()  # 关掉连接池，后续写入会失败

    await tracker.arecord("alice", TurnUsage(calls=1, input_tokens=1))  # 不抛异常即通过


# --------------------------------------------------------------------------------------
# 3. 接口：只能看自己的账
# --------------------------------------------------------------------------------------
async def test_usage_endpoint_scoped_to_caller(monkeypatch, fake_redis) -> None:
    """`/api/usage` 只能看到自己的账（身份规则与限流一致）。"""
    _stub_llm(monkeypatch, [])
    await fake_redis.usage.arecord(
        caller_identity("tok-A", None), TurnUsage(calls=2, input_tokens=300, output_tokens=40)
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            mine = await client.get("/api/usage", headers={"Authorization": "tok-A"})
            others = await client.get("/api/usage", headers={"Authorization": "tok-B"})

    assert mine.status_code == 200
    assert mine.json()["total"]["calls"] == 2
    assert mine.json()["total"]["input_tokens"] == 300
    assert others.json()["total"]["calls"] == 0  # 别人的账是空的


def _stub_llm(monkeypatch, responses) -> ScriptedLLM:
    llm = ScriptedLLM(list(responses))
    monkeypatch.setattr(main_module, "build_llm", lambda settings: llm)
    return llm
