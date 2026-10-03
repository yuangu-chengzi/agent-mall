"""对话接口测试：注入假 LLM，覆盖正常返回、降级、参数校验与鉴权透传。"""

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import app.main as main_module
from app.graph.runner import TurnResult
from app.main import app
from tests.fakes import ScriptedLLM


def _stub_llm(monkeypatch, responses) -> ScriptedLLM:
    """把 lifespan 里的 build_llm 换成假 LLM（不联网、不需要 API Key）。"""
    llm = ScriptedLLM(list(responses))
    monkeypatch.setattr(main_module, "build_llm", lambda settings: llm)
    return llm


def test_chat_returns_reply(monkeypatch, fake_redis) -> None:
    _stub_llm(monkeypatch, [AIMessage(content="你好呀，我是 wayn 商城客服。")])

    with TestClient(app) as client:
        response = client.post("/api/chat", json={"session_id": "s1", "message": "在吗"})

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "s1"
    assert body["reply"] == "你好呀，我是 wayn 商城客服。"
    assert body["degraded"] is False


def test_chat_executes_tool_through_full_stack(monkeypatch, fake_redis) -> None:
    """走完整链路：HTTP → 图 → 工具 → 回灌 → HTTP 响应。"""
    from tests.fakes import tool_call

    _stub_llm(
        monkeypatch,
        [
            tool_call("calculator", {"expression": "19.9 * 3"}),
            AIMessage(content="三件一共 59.7 元。"),
        ],
    )

    with TestClient(app) as client:
        response = client.post("/api/chat", json={"session_id": "s2", "message": "三件 19.9 的多少钱"})

    assert response.status_code == 200
    assert response.json()["reply"] == "三件一共 59.7 元。"


def test_chat_degrades_when_llm_not_configured(monkeypatch, fake_redis) -> None:
    """没配 LLM Key 也要给出话术（200 + degraded），而不是 500。"""
    monkeypatch.setattr(main_module, "build_llm", lambda settings: None)

    with TestClient(app) as client:
        response = client.post("/api/chat", json={"session_id": "s3", "message": "在吗"})

    assert response.status_code == 200
    assert response.json()["degraded"] is True
    assert response.json()["reason"] == "llm_unavailable"


def test_chat_rejects_invalid_payload(monkeypatch, fake_redis) -> None:
    _stub_llm(monkeypatch, [])

    with TestClient(app) as client:
        assert client.post("/api/chat", json={"session_id": "s", "message": ""}).status_code == 422
        assert client.post("/api/chat", json={"message": "你好"}).status_code == 422


def test_chat_passes_authorization_and_limits(monkeypatch, fake_redis) -> None:
    """鉴权透传 + 步数上限都来自请求/配置，而不是写死在接口里。"""
    captured: dict[str, object] = {}

    async def fake_run_turn(graph, *, session_id, message, token, recursion_limit):
        captured.update(session_id=session_id, message=message, token=token, recursion_limit=recursion_limit)
        return TurnResult(reply="ok")

    monkeypatch.setattr(main_module, "run_turn", fake_run_turn)
    _stub_llm(monkeypatch, [])

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"session_id": "s9", "message": "你好"},
            headers={"Authorization": "tok-999"},
        )

    assert response.status_code == 200
    assert captured["token"] == "tok-999"
    assert captured["session_id"] == "s9"
    assert captured["message"] == "你好"
    assert captured["recursion_limit"] == 16  # Settings.graph_recursion_limit 默认值


def test_chat_rejects_session_id_with_colon(monkeypatch, fake_redis) -> None:
    """session_id 含冒号会被拒绝——它会拼进 Redis key 造成歧义。"""
    _stub_llm(monkeypatch, [])

    with TestClient(app) as client:
        response = client.post("/api/chat", json={"session_id": "bad:id", "message": "你好"})

    assert response.status_code == 422
