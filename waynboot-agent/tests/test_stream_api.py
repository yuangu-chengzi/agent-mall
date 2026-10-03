"""SSE 流式接口 + 会话管理接口测试。

用 fakeredis 替掉真实 saver（决策 D7：测试不依赖外部服务）。
"""

import json

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import app.main as main_module
from app.main import app
from tests.fakes import ScriptedLLM, tool_call


def _stub_llm(monkeypatch, responses) -> ScriptedLLM:
    llm = ScriptedLLM(list(responses))
    monkeypatch.setattr(main_module, "build_llm", lambda settings: llm)
    return llm


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    """把 SSE 文本解析成 ``[(event, data), ...]``。"""
    events: list[tuple[str, dict]] = []
    for block in raw.strip().split("\n\n"):
        lines = [line for line in block.splitlines() if line]
        name = next((line[7:] for line in lines if line.startswith("event: ")), None)
        payload = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if name and payload:
            events.append((name, json.loads(payload)))
    return events


def test_stream_emits_tokens_then_done(monkeypatch, fake_redis) -> None:
    _stub_llm(monkeypatch, [AIMessage(content="你好，我是 wayn 商城客服。")])

    with TestClient(app) as client:
        response = client.post("/api/chat/stream", json={"session_id": "s1", "message": "在吗"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)

    assert events[-1][0] == "done"
    done = events[-1][1]
    assert done["reply"] == "你好，我是 wayn 商城客服。"
    assert done["degraded"] is False
    assert done["reason"] is None
    assert done["escalate"] is False
    assert done["pending_action"] is None
    streamed = "".join(data["text"] for name, data in events if name == "token")
    assert streamed == "你好，我是 wayn 商城客服。"


def test_stream_reports_tool_calls(monkeypatch, fake_redis) -> None:
    """工具调用要作为事件暴露给前端（用户能看到"正在查询订单…"这类提示）。"""
    _stub_llm(
        monkeypatch,
        [tool_call("calculator", {"expression": "19.9 * 3"}), AIMessage(content="一共 59.7 元。")],
    )

    with TestClient(app) as client:
        response = client.post("/api/chat/stream", json={"session_id": "s2", "message": "三件 19.9 多少"})

    events = parse_sse(response.text)
    tool_events = [data for name, data in events if name == "tool"]

    assert {"phase": "start", "name": "calculator"} in tool_events
    assert {"phase": "end", "name": "calculator"} in tool_events
    # 工具返回的原始文本不能混进正文（否则用户会在气泡里看到 "19.9 * 3 = 59.7"）
    streamed = "".join(data["text"] for name, data in events if name == "token")
    assert "一共 59.7 元。" in streamed
    assert "= 59.7" not in streamed


def test_stream_degrades_without_llm(monkeypatch, fake_redis) -> None:
    """没配 Key 时也要给一个 done 事件（而不是断流）。"""
    monkeypatch.setattr(main_module, "build_llm", lambda settings: None)

    with TestClient(app) as client:
        response = client.post("/api/chat/stream", json={"session_id": "s3", "message": "在吗"})

    events = parse_sse(response.text)

    assert len(events) == 1
    assert events[0][0] == "done"
    assert events[0][1]["degraded"] is True
    assert events[0][1]["reason"] == "llm_unavailable"


def test_stream_degrades_when_loop_never_ends(monkeypatch, fake_redis) -> None:
    """模型陷在工具循环里 → 被 recursion_limit 掐断 → done 带兜底话术。"""
    _stub_llm(
        monkeypatch,
        [tool_call("faq", {"question": "怎么退货"}, call_id=f"c{i}") for i in range(30)],
    )

    with TestClient(app) as client:
        response = client.post("/api/chat/stream", json={"session_id": "s4", "message": "一直问"})

    events = parse_sse(response.text)

    assert events[-1][0] == "done"
    assert events[-1][1]["degraded"] is True
    assert events[-1][1]["reason"] == "too_many_steps"
    assert "转人工" in events[-1][1]["reply"]


def test_session_endpoints_reflect_redis_state(monkeypatch, fake_redis) -> None:
    """会话管理接口读的就是 Redis 里的实际状态。"""
    _stub_llm(monkeypatch, [AIMessage(content="好的")])

    with TestClient(app) as client:
        assert client.get("/api/sessions/unknown").status_code == 404

        client.post("/api/chat", json={"session_id": "s5", "message": "你好"})

        summary = client.get("/api/sessions/s5")
        assert summary.status_code == 200
        body = summary.json()
        assert body["session_id"] == "s5"
        assert body["message_count"] == 2  # Human + AI
        assert body["pending_writes"] == 0
        assert body["created_at"] > 0

        assert client.delete("/api/sessions/s5").status_code == 204
        assert client.get("/api/sessions/s5").status_code == 404
        # 删除是幂等的
        assert client.delete("/api/sessions/s5").status_code == 204


def test_stream_keeps_context_across_turns(monkeypatch, fake_redis) -> None:
    """流式接口同样走会话记忆（两轮之间上下文不断）。"""
    llm = _stub_llm(
        monkeypatch,
        [AIMessage(content="第一轮答复"), AIMessage(content="第二轮答复")],
    )

    with TestClient(app) as client:
        client.post("/api/chat/stream", json={"session_id": "s6", "message": "第一句"})
        client.post("/api/chat/stream", json={"session_id": "s6", "message": "第二句"})

    assert [message.content for message in llm.calls[1][1:]] == ["第一句", "第一轮答复", "第二句"]


def test_session_endpoint_is_scoped_to_the_caller(monkeypatch, fake_redis) -> None:
    """A18：会话概览按身份派生——别人拿着同一个 session_id 查不到（404）。"""
    _stub_llm(monkeypatch, [AIMessage(content="好的")])

    with TestClient(app) as client:
        client.post(
            "/api/chat", json={"session_id": "scoped", "message": "你好"}, headers={"Authorization": "tok-A"}
        )
        mine = client.get("/api/sessions/scoped", headers={"Authorization": "tok-A"})
        others = client.get("/api/sessions/scoped", headers={"Authorization": "tok-B"})

    assert mine.status_code == 200
    assert others.status_code == 404
