"""限流与配额测试（含接口层的 429）。"""

import pytest
from fakeredis import aioredis
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import app.main as main_module
from app.main import app
from app.memory.ratelimit import RateLimiter
from tests.fakes import ScriptedLLM


def _install_limiter(monkeypatch, limiter: RateLimiter) -> None:
    """把应用的限流器换成测试用的（阈值自己定，避免被默认值影响）。"""
    monkeypatch.setattr(main_module.RateLimiter, "from_settings", classmethod(lambda cls, settings: limiter))


@pytest.fixture
async def limiter():
    instance = RateLimiter(aioredis.FakeRedis(decode_responses=False), prefix="test:rl:", qps=2, daily=3)
    yield instance
    await instance.aclose()


async def test_qps_limit_blocks_burst(limiter) -> None:
    """同一身份连发：第 3 次被 QPS 拦下。"""
    decisions = [await limiter.check("user-a") for _ in range(3)]

    assert [d.allowed for d in decisions] == [True, True, False]
    assert decisions[-1].scope == "qps"
    assert decisions[-1].retry_after >= 1


async def test_daily_quota_blocks_after_limit(limiter) -> None:
    """日配额在小 QPS 下也会兜住（这里 qps=2、daily=3 → 第 4 次是日配额拦的）。"""
    # 先在第 1 秒用掉 2 次（同时把日计数打到 2）
    await limiter.check("user-b")
    await limiter.check("user-b")
    # 模拟"过了这一秒"：清掉 QPS 计数键
    await limiter._redis.delete(f"{limiter._prefix}qps:user-b")

    third = await limiter.check("user-b")
    fourth = await limiter.check("user-b")

    assert third.allowed is True  # 日计数 → 3，刚好用完
    assert fourth.allowed is False
    assert fourth.scope == "daily"
    assert fourth.retry_after > 60  # 日窗口


async def test_identities_are_isolated(limiter) -> None:
    assert (await limiter.check("alice")).allowed is True
    assert (await limiter.check("bob")).allowed is True


async def test_fail_open_when_redis_is_down() -> None:
    """Redis 挂了要放行：限流是保护措施，不该变成故障点。"""
    limiter = RateLimiter(aioredis.FakeRedis(decode_responses=False), qps=1, daily=1)
    await limiter._redis.aclose()  # 关掉连接池，后续操作会抛错

    decision = await limiter.check("someone")

    assert decision.allowed is True


def test_identity_hashes_token_and_falls_back_to_ip() -> None:
    """token 只以哈希形式进 key（不把凭证原文写进 Redis）；匿名按 IP。"""
    with_token = RateLimiter.identity("secret-token-value", "1.2.3.4")
    same_token = RateLimiter.identity("secret-token-value", "5.6.7.8")
    anonymous = RateLimiter.identity(None, "1.2.3.4")
    anonymous_v6 = RateLimiter.identity(None, "::1")

    assert with_token == same_token  # 同一 token 同一身份（与 IP 无关）
    assert "secret-token-value" not in with_token
    assert anonymous == "ip-1.2.3.4"
    assert anonymous_v6 == "ip-__1"  # IPv6 的冒号不能进 key


def _chat(client, session: str, message: str = "在吗"):
    """发一轮对话（固定带同一个 token，便于按身份计数）。"""
    return client.post(
        "/api/chat",
        json={"session_id": session, "message": message},
        headers={"Authorization": "tok"},
    )


# --------------------------------------------------------------------------------------
# 接口层
# --------------------------------------------------------------------------------------
def test_chat_returns_429_when_limit_exceeded(monkeypatch, fake_redis) -> None:
    """限流命中时返回 429 + 友好文案（而不是 500 或静默）。"""
    limiter = RateLimiter(aioredis.FakeRedis(decode_responses=False), qps=1, daily=100)
    _install_limiter(monkeypatch, limiter)
    llm = ScriptedLLM([AIMessage(content="你好")])
    monkeypatch.setattr(main_module, "build_llm", lambda settings: llm)

    with TestClient(app) as client:
        first = _chat(client, "r1")
        second = _chat(client, "r1")

    assert first.status_code == 200
    assert second.status_code == 429
    detail = second.json()["detail"]
    assert detail["reason"] == "qps"
    assert "快" in detail["message"]
    assert second.headers["Retry-After"]


def test_healthz_is_not_rate_limited(monkeypatch, fake_redis) -> None:
    """探针接口不能被限流影响，否则编排系统会误判服务不可用。"""
    limiter = RateLimiter(aioredis.FakeRedis(decode_responses=False), qps=1, daily=1)
    _install_limiter(monkeypatch, limiter)
    llm = ScriptedLLM([AIMessage(content="嗯")])
    monkeypatch.setattr(main_module, "build_llm", lambda settings: llm)

    with TestClient(app) as client:
        _chat(client, "r2")
        _chat(client, "r2")
        healthz = client.get("/healthz")
        readyz = client.get("/readyz")

    assert healthz.status_code == 200
    # /readyz 可能因为 mall 没起而返回 503（那是它该做的），但绝不能是限流的 429
    assert readyz.status_code != 429


def test_rate_limit_is_fail_open_on_redis_error(monkeypatch, fake_redis) -> None:
    """限流器内部报错时请求照常通过（fail-open），并留一条警告日志。"""
    limiter = RateLimiter(aioredis.FakeRedis(decode_responses=False), qps=1, daily=1)

    async def boom(identity: str):
        raise RuntimeError("redis 抖了")

    monkeypatch.setattr(limiter, "_check", boom)
    monkeypatch.setattr(main_module.RateLimiter, "from_settings", classmethod(lambda cls, settings: limiter))
    llm = ScriptedLLM([AIMessage(content="你好")])
    monkeypatch.setattr(main_module, "build_llm", lambda settings: llm)

    with TestClient(app) as client:
        response = _chat(client, "r3")

    assert response.status_code == 200
