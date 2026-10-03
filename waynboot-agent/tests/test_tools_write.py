"""写操作工具的**前置校验**测试（不触发 interrupt 的路径）。

真正"确认后执行"的路径需要图的运行时上下文（`interrupt()` 只能在图里用），
那部分在 tests/test_confirm_api.py 里用真实图覆盖。

这些前置校验的价值：把"注定失败"或"越权"的请求**拦在确认弹窗之前**，
不然用户会白点一次确认、然后收到一句失败。
"""

import httpx
import pytest
from fakeredis import aioredis
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph.runner import detect_escalation
from app.memory.idempotency import IdempotencyStore
from app.tools.base import ESCALATE_TOOL_NAME
from app.tools.orders import find_own_order
from app.tools.write import build_write_tools
from tests.conftest import fail, ok, token_config
from tests.payloads import goods_detail_payload, order_list_item, order_list_page

ORDER_ID = 1001
ORDER_SN = "2024010112345678"


@pytest.fixture
async def idempotency():
    instance = IdempotencyStore(aioredis.FakeRedis(decode_responses=False), prefix="test:w:")
    yield instance
    await instance.aclose()


def _tools(client, idempotency):
    return {tool.name: tool for tool in build_write_tools(client, idempotency)}


# --------------------------------------------------------------------------------------
# 登录与所有权（A15/A16 越权防护：退款接口 mall 侧也不校验归属）
# --------------------------------------------------------------------------------------
async def test_write_tools_require_login(make_client, idempotency, mall_requests) -> None:
    """没登录时连 mall 都不碰。"""
    async with make_client() as client:
        tools = _tools(client, idempotency)
        refund = await tools["request_refund"].ainvoke({"orderId": ORDER_ID}, config=token_config(token=""))
        cart = await tools["add_to_cart"].ainvoke(
            {"goodsId": 1024, "productId": 2048}, config=token_config(token="")
        )

    assert "未登录" in refund
    assert "未登录" in cart
    assert mall_requests == []


async def test_refund_refuses_order_not_belonging_to_user(make_client, idempotency) -> None:
    """订单不在本人列表里 → 直接拒绝，不进入确认环节（mall 侧退款接口不校验归属）。"""
    others = [order_list_item(order_sn="2024010199999999", id=9999)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ok(order_list_page(others)))

    async with make_client(handler) as client:
        tools = _tools(client, idempotency)
        result = await tools["request_refund"].ainvoke({"orderId": ORDER_ID}, config=token_config())

    assert "没有找到这笔订单" in result


async def test_refund_refuses_when_status_not_refundable(make_client, idempotency) -> None:
    """mall 的 handleOption.refund=false（例如已发货）→ 提前说明，不让用户白点确认。"""
    item = order_list_item(
        order_sn=ORDER_SN, status_text="已发货", handleOption={**_default_options(), "refund": False}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ok(order_list_page([item])))

    async with make_client(handler) as client:
        tools = _tools(client, idempotency)
        result = await tools["request_refund"].ainvoke({"orderId": ORDER_ID}, config=token_config())

    assert "不支持申请退款" in result
    assert "已发货" in result


def _default_options() -> dict:
    return {
        "cancel": False,
        "delete": False,
        "pay": False,
        "comment": False,
        "confirm": False,
        "refund": True,
        "rebuy": False,
        "aftersale": False,
    }


async def test_refund_with_expired_token_writes_nothing(make_client, idempotency) -> None:
    """token 过期（不是"没带"）这条路径也必须在确认前就停住——用户思考期间 token 可能失效。

    学习者提问时发现的缺口：原来只测了"没带 token"（早返回分支），
    而"带了但过期"走的是 MallAuthError 分支，同样不能发写请求。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fail(401, "没有权限，请联系管理员授权"))

    async with make_client(handler) as client:
        tools = _tools(client, idempotency)
        result = await tools["request_refund"].ainvoke(
            {"orderId": ORDER_ID}, config=token_config("expired-tok")
        )

    assert "登录状态已失效" in result


async def test_find_own_order_pages_until_found(make_client) -> None:
    """所有权查询在同一页里按 id 匹配（退款用的是订单 id，不是订单号）。"""
    full_page = [order_list_item(order_sn=f"sn-{i}", id=i) for i in range(50)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ok(order_list_page(full_page)))

    async with make_client(handler) as client:
        found = await find_own_order(client, "tok", order_id=49)
        missing = await find_own_order(client, "tok", order_id=999)

    assert found is not None and found["orderSn"] == "sn-49"
    assert missing is None


# --------------------------------------------------------------------------------------
# 加购的前置校验（商品/SKU/库存）
# --------------------------------------------------------------------------------------
async def test_add_to_cart_rejects_unknown_sku(make_client, idempotency) -> None:
    payload = goods_detail_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ok(payload))

    async with make_client(handler) as client:
        tools = _tools(client, idempotency)
        result = await tools["add_to_cart"].ainvoke(
            {"goodsId": 1024, "productId": 999999}, config=token_config()
        )

    assert "没有找到商品" in result


async def test_add_to_cart_rejects_quantity_over_stock(make_client, idempotency) -> None:
    payload = goods_detail_payload()  # 默认两个 SKU，库存 12 / 3

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ok(payload))

    async with make_client(handler) as client:
        tools = _tools(client, idempotency)
        result = await tools["add_to_cart"].ainvoke(
            {"goodsId": 1024, "productId": 2049, "number": 5}, config=token_config()
        )

    assert "可售库存只有 3 件" in result


async def test_add_to_cart_rejects_non_positive_number(make_client, idempotency, mall_requests) -> None:
    async with make_client() as client:
        tools = _tools(client, idempotency)
        result = await tools["add_to_cart"].ainvoke(
            {"goodsId": 1024, "productId": 2048, "number": 0}, config=token_config()
        )

    assert "数量必须大于 0" in result
    assert mall_requests == []


async def test_add_to_cart_reports_mall_error(make_client, idempotency) -> None:
    async with make_client(lambda request: httpx.Response(200, json=fail(5302, "商品已经下架"))) as client:
        tools = _tools(client, idempotency)
        result = await tools["add_to_cart"].ainvoke(
            {"goodsId": 1024, "productId": 2048}, config=token_config()
        )

    assert "商品已经下架" in result


# --------------------------------------------------------------------------------------
# 转人工
# --------------------------------------------------------------------------------------
async def test_escalate_returns_handoff_instruction(make_client, idempotency) -> None:
    async with make_client() as client:
        tools = _tools(client, idempotency)
        result = await tools[ESCALATE_TOOL_NAME].ainvoke(
            {"reason": "用户要求人工核实"}, config=token_config()
        )

    assert "已记录转人工请求" in result
    assert "9:00-21:00" in result


def test_detect_escalation_from_messages() -> None:
    """转人工的判定：历史里出现了 escalate 工具的调用结果。"""
    plain = [HumanMessage(content="你好"), AIMessage(content="在的")]
    escalated = [
        HumanMessage(content="转人工"),
        AIMessage(content=""),
        ToolMessage(content="已记录", name=ESCALATE_TOOL_NAME, tool_call_id="c1"),
    ]

    assert detect_escalation(plain) is False
    assert detect_escalation(escalated) is True
