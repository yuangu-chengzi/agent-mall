"""写操作幂等存储测试（决策 D4）。"""

import pytest
from fakeredis import aioredis

from app.memory.idempotency import PENDING, IdempotencyStore


@pytest.fixture
async def store():
    instance = IdempotencyStore(
        aioredis.FakeRedis(decode_responses=False), prefix="test:idem:", ttl_seconds=600
    )
    yield instance
    await instance.aclose()


async def test_action_runs_only_once(store) -> None:
    """核心语义：同一幂等键，第二次直接返回上次结果，不再执行。"""
    calls = []

    async def action() -> str:
        calls.append(1)
        return "已提交退款申请"

    first, executed_first = await store.run_once("k1", action)
    second, executed_second = await store.run_once("k1", action)

    assert (first, executed_first) == ("已提交退款申请", True)
    assert (second, executed_second) == ("已提交退款申请", False)
    assert len(calls) == 1  # 真只执行了一次


async def test_different_keys_run_independently(store) -> None:
    async def action_a() -> str:
        return "A"

    async def action_b() -> str:
        return "B"

    assert await store.run_once("ka", action_a) == ("A", True)
    assert await store.run_once("kb", action_b) == ("B", True)


async def test_failure_releases_the_key_so_user_can_retry(store) -> None:
    """执行失败不留占位：否则一次网络抖动就把这个动作永久锁死了。"""
    attempts = []

    async def flaky() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("mall 抖了一下")
        return "第二次成功"

    with pytest.raises(RuntimeError):
        await store.run_once("retry", flaky)

    result, executed = await store.run_once("retry", flaky)

    assert (result, executed) == ("第二次成功", True)
    assert len(attempts) == 2


async def test_in_flight_placeholder_is_not_mistaken_for_a_result(store) -> None:
    """占位符不能被当成"上次结果"回给用户。"""
    await store._redis.set(f"{store._prefix}busy", PENDING, ex=60)

    result, executed = await store.run_once("busy", _never_called)

    assert executed is False
    assert "处理中" in result


async def test_result_expires(store) -> None:
    async def action() -> str:
        return "结果"

    await store.run_once("ttl", action)

    ttl = await store._redis.ttl(f"{store._prefix}ttl")
    assert 0 < ttl <= 600


def test_make_key_is_stable_and_distinct() -> None:
    """幂等键 = 会话 + 动作 + 目标 的哈希：同输入同结果，不同输入必不同。"""
    key_a = IdempotencyStore.make_key("s1", "refund", 1001)
    key_b = IdempotencyStore.make_key("s1", "refund", 1001)
    key_c = IdempotencyStore.make_key("s1", "refund", 1002)
    key_d = IdempotencyStore.make_key("s2", "refund", 1001)

    assert key_a == key_b
    assert len({key_a, key_c, key_d}) == 3
    assert len(key_a) == 32


async def _never_called() -> str:
    raise AssertionError("占位命中时不该执行动作")
