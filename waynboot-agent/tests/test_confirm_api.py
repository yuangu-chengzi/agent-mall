"""写操作 + 人工确认的端到端测试（P4 核心验收）。

覆盖的保证（对应决策 D3/D4）：

1. **确认之前一个字节都不写**（interrupt 之前的工具代码只读）；
2. 用户点"取消" → 同样不写；
3. **连点/重复确认 → 只写一次**（幂等键生效）；
4. 恢复时没带凭证 → 不写（恢复会重跑工具，凭证必须再传一次）；
5. 转人工会在响应里给出 ``escalate`` 标记；
6. 加购的请求体与 mall 契约一致（`goodsId/productId/number`）。
"""

import json

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import app.main as main_module
from app.main import app
from app.mall.client import MallClient
from tests.fakes import ScriptedLLM, tool_call
from tests.payloads import goods_detail_payload, order_list_item, order_list_page

ORDER_ID = 1001
ORDER_SN = "2024010112345678"


def _refundable_options() -> dict:
    return {
        "cancel": True,
        "delete": False,
        "pay": False,
        "comment": False,
        "confirm": False,
        "refund": True,
        "rebuy": False,
        "aftersale": False,
    }


def mal_handler(seen: list[httpx.Request]):
    """mall 的打桩路由（把真实请求记进 seen 供断言）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/order/list":
            item = order_list_item(order_sn=ORDER_SN, id=ORDER_ID, handleOption=_refundable_options())
            return httpx.Response(200, json=ok(order_list_page([item])))
        if path.startswith("/goods/detail/"):
            return httpx.Response(200, json=ok(goods_detail_payload()))
        if path.startswith("/order/refund/") or path == "/cart":
            # mall 的写接口用 R.success() 无参重载：data 是 null
            return httpx.Response(200, json=ok(None))
        return httpx.Response(404, text=f"未打桩 {request.method} {path}")

    return handler


def ok(data: object = None) -> dict:
    return {"code": 200, "msg": "操作成功", "data": data}


def _stub_mall(monkeypatch, seen: list[httpx.Request]) -> None:
    """把 lifespan 里的 MallClient 换成打桩客户端。"""

    def factory(base_url: str, timeout: float = 10.0) -> MallClient:
        return MallClient(base_url, timeout=timeout, transport=httpx.MockTransport(mal_handler(seen)))

    monkeypatch.setattr(main_module, "MallClient", factory)


def _stub_llm(monkeypatch, responses) -> ScriptedLLM:
    llm = ScriptedLLM(list(responses))
    monkeypatch.setattr(main_module, "build_llm", lambda settings: llm)
    return llm


def _writes(seen: list[httpx.Request], path_prefix: str) -> list[httpx.Request]:
    return [
        request for request in seen if request.method == "POST" and request.url.path.startswith(path_prefix)
    ]


def _refund_script() -> list:
    return [
        tool_call("request_refund", {"orderId": ORDER_ID}),
        AIMessage(content="退款申请已提交，平台审核后处理。"),
    ]


# --------------------------------------------------------------------------------------
# 退款：确认 → 执行
# --------------------------------------------------------------------------------------
def test_refund_waits_for_confirmation_then_executes(monkeypatch, fake_redis) -> None:
    seen: list[httpx.Request] = []
    _stub_mall(monkeypatch, seen)
    _stub_llm(monkeypatch, _refund_script())

    with TestClient(app) as client:
        first = client.post(
            "/api/chat",
            json={"session_id": "c1", "message": "帮我把这个订单退了"},
            headers={"Authorization": "tok-1"},
        )

        assert first.status_code == 200
        body = first.json()
        assert body["pending_action"] is not None
        assert body["pending_action"]["action"] == "request_refund"
        assert body["pending_action"]["reversible"] is False
        assert "确认" in body["pending_action"]["ask"]
        # 关键保证：确认之前没有任何写请求
        assert _writes(seen, "/order/refund") == []

        second = client.post(
            "/api/chat/confirm",
            json={"session_id": "c1", "approved": True},
            headers={"Authorization": "tok-1"},
        )

    assert second.status_code == 200
    confirmed = second.json()
    assert confirmed["pending_action"] is None
    assert confirmed["reply"] == "退款申请已提交，平台审核后处理。"
    writes = _writes(seen, "/order/refund")
    assert len(writes) == 1 and writes[0].url.path == f"/order/refund/{ORDER_ID}"
    assert writes[0].headers["Authorization"] == "tok-1"


def test_rejected_confirmation_writes_nothing(monkeypatch, fake_redis) -> None:
    seen: list[httpx.Request] = []
    _stub_mall(monkeypatch, seen)
    _stub_llm(
        monkeypatch,
        [tool_call("request_refund", {"orderId": ORDER_ID}), AIMessage(content="好的，这次没有提交退款。")],
    )

    with TestClient(app) as client:
        client.post(
            "/api/chat", json={"session_id": "c2", "message": "退了吧"}, headers={"Authorization": "tok-1"}
        )
        result = client.post(
            "/api/chat/confirm",
            json={"session_id": "c2", "approved": False},
            headers={"Authorization": "tok-1"},
        )

    assert result.status_code == 200
    assert _writes(seen, "/order/refund") == []


def test_repeated_confirmation_writes_only_once(monkeypatch, fake_redis) -> None:
    """连点确认的等价场景：用户在同一个会话里两次要求退同一笔单，各确认一次 → 只写一次。"""
    seen: list[httpx.Request] = []
    _stub_mall(monkeypatch, seen)
    _stub_llm(
        monkeypatch,
        [
            tool_call("request_refund", {"orderId": ORDER_ID}),
            AIMessage(content="已提交退款申请。"),
            tool_call("request_refund", {"orderId": ORDER_ID}, call_id="c2"),
            AIMessage(content="这笔刚才已经提交过了，没有重复提交。"),
        ],
    )

    with TestClient(app) as client:
        for session, message in (("c3", "退了吧"), ("c3", "再帮我退一次")):
            client.post(
                "/api/chat",
                json={"session_id": session, "message": message},
                headers={"Authorization": "tok"},
            )
            client.post(
                "/api/chat/confirm",
                json={"session_id": session, "approved": True},
                headers={"Authorization": "tok"},
            )

    assert len(_writes(seen, "/order/refund")) == 1


def test_confirm_without_credentials_writes_nothing(monkeypatch, fake_redis) -> None:
    """恢复时工具会重跑，凭证必须再传一次——没带就写不进去（这是保护，不是 bug）。"""
    seen: list[httpx.Request] = []
    _stub_mall(monkeypatch, seen)
    _stub_llm(
        monkeypatch,
        [tool_call("request_refund", {"orderId": ORDER_ID}), AIMessage(content="请先登录。")],
    )

    with TestClient(app) as client:
        client.post(
            "/api/chat", json={"session_id": "c4", "message": "退了吧"}, headers={"Authorization": "tok-1"}
        )
        result = client.post("/api/chat/confirm", json={"session_id": "c4", "approved": True})

    assert result.status_code == 200
    assert _writes(seen, "/order/refund") == []


# --------------------------------------------------------------------------------------
# 加购
# --------------------------------------------------------------------------------------
def test_add_to_cart_confirmation_body_matches_contract(monkeypatch, fake_redis) -> None:
    seen: list[httpx.Request] = []
    _stub_mall(monkeypatch, seen)
    _stub_llm(
        monkeypatch,
        [
            tool_call("add_to_cart", {"goodsId": 1024, "productId": 2049, "number": 2}),
            AIMessage(content="已经帮你加进购物车了。"),
        ],
    )

    with TestClient(app) as client:
        first = client.post(
            "/api/chat",
            json={"session_id": "c5", "message": "帮我把这个加入购物车"},
            headers={"Authorization": "tok-1"},
        )
        pending = first.json()["pending_action"]
        # 确认卡片要带上用户看得懂的信息
        assert pending["action"] == "add_to_cart"
        assert pending["reversible"] is True
        assert "x2" in pending["summary"] or "×2" in pending["summary"]
        assert _writes(seen, "/cart") == []

        client.post(
            "/api/chat/confirm",
            json={"session_id": "c5", "approved": True},
            headers={"Authorization": "tok-1"},
        )

    writes = _writes(seen, "/cart")
    assert len(writes) == 1
    assert json.loads(writes[0].content) == {"goodsId": 1024, "productId": 2049, "number": 2}


# --------------------------------------------------------------------------------------
# 转人工
# --------------------------------------------------------------------------------------
def test_escalation_flag_is_returned(monkeypatch, fake_redis) -> None:
    seen: list[httpx.Request] = []
    _stub_mall(monkeypatch, seen)
    _stub_llm(
        monkeypatch,
        [
            tool_call("escalate_to_human", {"reason": "用户要求人工核实退款进度"}),
            AIMessage(content="已经为你转接人工客服，请留意会话消息。"),
        ],
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"session_id": "c6", "message": "我要转人工"},
            headers={"Authorization": "tok-1"},
        )

    body = response.json()
    assert body["escalate"] is True
    assert body["pending_action"] is None
    # 转人工不该产生任何写请求
    assert all(request.method == "GET" for request in seen)


# --------------------------------------------------------------------------------------
# 流式接口也要能带出确认卡片
# --------------------------------------------------------------------------------------
def test_stream_done_event_carries_pending_action(monkeypatch, fake_redis) -> None:
    from tests.test_stream_api import parse_sse

    seen: list[httpx.Request] = []
    _stub_mall(monkeypatch, seen)
    _stub_llm(monkeypatch, _refund_script())

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/stream",
            json={"session_id": "c7", "message": "帮我退款"},
            headers={"Authorization": "tok-1"},
        )

    events = parse_sse(response.text)
    assert events[-1][0] == "done"
    done = events[-1][1]
    assert done["pending_action"]["action"] == "request_refund"
    assert done["degraded"] is False
    assert _writes(seen, "/order/refund") == []
