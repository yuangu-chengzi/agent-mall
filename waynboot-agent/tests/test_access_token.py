"""Agent 自身访问闸门测试（``AGENT_ACCESS_TOKEN``）。

不启用时零影响（本地开发零配置）；启用后**花钱的接口**必须带 ``X-Agent-Token``——
因为"没有 token 就查不到数据"挡不住匿名刷 LLM 额度这件事。
"""

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import app.main as main_module
from app.config import Settings
from app.main import app
from tests.fakes import ScriptedLLM


def _stub_llm(monkeypatch, responses) -> ScriptedLLM:
    llm = ScriptedLLM(list(responses))
    monkeypatch.setattr(main_module, "build_llm", lambda settings: llm)
    return llm


def _chat(client: TestClient, headers: dict | None = None):
    return client.post("/api/chat", json={"session_id": "gate", "message": "在吗"}, headers=headers or {})


def test_gate_is_disabled_by_default(monkeypatch, fake_redis) -> None:
    """没配 AGENT_ACCESS_TOKEN → 请求照常（本地开发不该被拦）。"""
    _stub_llm(monkeypatch, [AIMessage(content="你好")])

    with TestClient(app) as client:
        assert _chat(client).status_code == 200


def test_gate_rejects_missing_or_wrong_token(monkeypatch, fake_redis) -> None:
    _stub_llm(monkeypatch, [AIMessage(content="你好")])

    with TestClient(app) as client:
        app.state.settings = Settings(_env_file=None, access_token="secret-token")
        missing = _chat(client)
        wrong = _chat(client, {"X-Agent-Token": "nope"})
        correct = _chat(client, {"X-Agent-Token": "secret-token"})
        sessions = client.get("/api/sessions/gate", headers={"X-Agent-Token": "nope"})
        usage = client.get("/api/usage", headers={"X-Agent-Token": "nope"})

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert sessions.status_code == 401
    assert usage.status_code == 401
    assert missing.json()["detail"]["message"].startswith("缺少或错误")
    assert correct.status_code == 200


def test_gate_leaves_probes_and_login_open(monkeypatch, fake_redis) -> None:
    """探针要给编排系统用、登录代理是用户入口 —— 它们不受这道闸门管。"""
    _stub_llm(monkeypatch, [])
    seen: list = []
    monkeypatch.setattr(main_module, "MallClient", _recording_mall_factory(seen))

    with TestClient(app) as client:
        app.state.settings = Settings(_env_file=None, access_token="secret-token")
        healthz = client.get("/healthz")
        readyz = client.get("/readyz")
        login = client.post("/api/login", json={"mobile": "13800000001", "yzm": "1234"})

    assert healthz.status_code == 200
    assert readyz.status_code in (200, 503)  # 探针只看依赖状态，不该是 401
    assert login.status_code == 200  # 登录代理照常可用


def _recording_mall_factory(seen: list):
    """把 lifespan 的 MallClient 换成打桩客户端（登录代理要能拿到 token）。"""
    import httpx

    from app.mall.client import MallClient
    from tests.conftest import ok

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ok("token-abc"))

    def factory(base_url: str, timeout: float = 10.0) -> MallClient:
        return MallClient(base_url, timeout=timeout, transport=httpx.MockTransport(handler))

    return factory
