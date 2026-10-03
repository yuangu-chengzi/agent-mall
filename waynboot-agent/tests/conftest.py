"""测试公共夹具。

测试边界（决策 D7）：**不依赖真实 LLM、不依赖网络、不依赖 mall 进程**。
对 mall 的调用一律通过 ``httpx.MockTransport`` 注入（``MallClient`` 已预留 ``transport`` 参数），
因此不需要 monkeypatch 全局，也不会误连真实服务。
"""

from collections.abc import Callable
from types import SimpleNamespace

import httpx
import pytest
from fakeredis import aioredis

from app.config import Settings
from app.mall.client import MallClient
from app.memory import RedisCheckpointSaver
from app.memory.idempotency import IdempotencyStore
from app.memory.ratelimit import RateLimiter
from app.memory.usage import UsageTracker

#: MockTransport 的处理函数签名
Handler = Callable[[httpx.Request], httpx.Response]


def ok(data: object = None, msg: str = "操作成功") -> dict:
    """构造 mall 的成功信封。"""
    return {"code": 200, "msg": msg, "data": data}


def fail(code: int, msg: str) -> dict:
    """构造 mall 的失败信封。"""
    return {"code": code, "msg": msg, "data": None}


def token_config(token: str = "tok-test") -> dict:
    """构造带 token 的运行配置（模拟 ``run_turn`` 注入的 ``configurable``）。"""
    return {"configurable": {"thread_id": "test-session", "token": token}}


def tool_by_name(tools: list, name: str):
    """按名字取工具（工具集是按功能分组构造的，测试里按名取更直观）。"""
    mapping = {tool.name: tool for tool in tools}
    assert name in mapping, f"工具 {name} 不存在，现有：{sorted(mapping)}"
    return mapping[name]


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch):
    """把应用依赖的四个 Redis 存储全换成 fakeredis 版：会话记忆、写操作幂等、限流、用量统计。

    **为什么必须统一在这里兜住**（这坑踩过两次）：接口测试如果不装它，就会悄悄连上本机真 Redis——
    Redis 没起时测试以"降级话术"形式莫名失败；Redis 起着时又会**跨用例互相污染**
    （限流计数攒着攒着就把后面的用例顶成 429）。两种都违反决策 D7。

    限流阈值在这里给得很大：**凡是不测限流的用例，都不该被限流影响**；
    要测限流的用例自己装一个小阈值的限流器（见 tests/test_ratelimit.py）。
    """
    from app.main import app  # 延迟导入：避免测试收集期就构建 FastAPI 应用

    def patch(cls, instance):
        monkeypatch.setattr(cls, "from_settings", classmethod(lambda _cls, settings: instance))
        return instance

    client = aioredis.FakeRedis(decode_responses=False)
    saver = patch(RedisCheckpointSaver, RedisCheckpointSaver(client, prefix="test:ckpt:", ttl_seconds=600))
    idempotency = patch(IdempotencyStore, IdempotencyStore(client, prefix="test:idem:", ttl_seconds=600))
    rate_limiter = patch(RateLimiter, RateLimiter(client, prefix="test:rl:", qps=1000, daily=100000))
    usage = patch(UsageTracker, UsageTracker(client, prefix="test:usage:", retention_days=7))

    app.state.saver = saver
    app.state.idempotency = idempotency
    app.state.rate_limiter = rate_limiter
    app.state.usage = usage
    return SimpleNamespace(saver=saver, idempotency=idempotency, rate_limiter=rate_limiter, usage=usage)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """干净配置：忽略仓库里的 .env，只认本测试显式设置的环境变量。"""
    monkeypatch.setenv("AGENT_MALL_BASE_URL", "http://mall.test")
    monkeypatch.setenv("AGENT_MALL_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("AGENT_LLM_API_KEY", "test-key")
    return Settings(_env_file=None)


@pytest.fixture
def mall_requests() -> list[httpx.Request]:
    """记录 MallClient 实际发出的请求，供断言 header / params。"""
    return []


@pytest.fixture
def make_client(settings: Settings, mall_requests: list[httpx.Request]):
    """返回一个工厂：``make_client(handler)`` 得到打桩后的 MallClient（异步上下文管理器）。

    不传 handler 时默认返回成功信封 ``{"ok": true}``。
    """

    def _make(handler: Handler | None = None) -> MallClient:
        respond = handler or (lambda request: httpx.Response(200, json=ok({"ok": True})))

        def recording_handler(request: httpx.Request) -> httpx.Response:
            mall_requests.append(request)
            return respond(request)

        return MallClient(
            settings.mall_base_url,
            timeout=settings.mall_timeout_seconds,
            transport=httpx.MockTransport(recording_handler),
        )

    return _make
