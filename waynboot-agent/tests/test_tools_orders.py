"""订单工具测试（mock HTTP，不依赖 mall 进程）。

重点覆盖两件事：
1. **所有权的 fail-closed 校验**（上游 `/order/detail` 缺归属校验，见 docs/API_CONTRACT.md §5 A15）；
2. 双层 `data` 信封、`non_null` 省略字段这些契约细节。
"""

import httpx

from app.tools.orders import build_order_tools
from tests.conftest import fail, ok, token_config, tool_by_name
from tests.payloads import order_detail_payload, order_list_item, order_list_page

_TARGET_SN = "2024010112345678"


def _routes(*, orders: list[dict], detail: dict | None = None, page2: list[dict] | None = None):
    """按路径分发的打桩 handler：/order/list 与 /order/detail/**。"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/order/list":
            page_num = int(request.url.params.get("pageNum", 1))
            items = orders if page_num == 1 else (page2 or [])
            return httpx.Response(200, json=ok(order_list_page(items)))
        if path.startswith("/order/detail/"):
            if detail is None:
                return httpx.Response(500, text="本用例不该请求订单详情")
            return httpx.Response(200, json=ok(detail))
        return httpx.Response(404, text=f"未打桩的路径 {path}")

    return handler


# --------------------------------------------------------------------------------------
# my_orders
# --------------------------------------------------------------------------------------
async def test_my_orders_requires_login_token(make_client, mall_requests) -> None:
    """没有 token 时直接回"请登录"，不该向 mall 发任何请求。"""
    async with make_client() as client:
        my_orders = tool_by_name(build_order_tools(client), "my_orders")
        result = await my_orders.ainvoke({}, config=token_config(token=""))

    assert "未登录" in result
    assert mall_requests == []


async def test_my_orders_parses_double_nested_data(make_client) -> None:
    """订单列表是双层 data（信封的 data 里再有一个 data 数组）。"""
    handler = _routes(orders=[order_list_item(order_sn=_TARGET_SN)])
    async with make_client(handler) as client:
        my_orders = tool_by_name(build_order_tools(client), "my_orders")
        result = await my_orders.ainvoke({"showType": 2}, config=token_config())

    assert "待发货订单共 1 笔" in result
    assert f"订单号 {_TARGET_SN}" in result
    assert "订单id=1001" in result
    assert "实付 59.70 元" in result
    assert "小米手机 12x2" in result


async def test_my_orders_reports_empty_list(make_client) -> None:
    handler = _routes(orders=[])
    async with make_client(handler) as client:
        my_orders = tool_by_name(build_order_tools(client), "my_orders")
        result = await my_orders.ainvoke({"showType": 1}, config=token_config())

    assert "没有待付款订单" in result


async def test_my_orders_reports_expired_login(make_client) -> None:
    """mall 的登录失效是 HTTP 200 + code 401，工具要认出来并引导重新登录。"""
    async with make_client(
        lambda request: httpx.Response(200, json=fail(401, "没有权限，请联系管理员授权"))
    ) as client:
        my_orders = tool_by_name(build_order_tools(client), "my_orders")
        result = await my_orders.ainvoke({}, config=token_config())

    assert "登录状态已失效" in result


# --------------------------------------------------------------------------------------
# order_detail：越权防护 + 正常解析
# --------------------------------------------------------------------------------------
async def test_order_detail_refuses_order_not_in_users_list(make_client, mall_requests) -> None:
    """A15 越权防护：单号不在自己的订单里 → 只回"没找到"，**绝不请求详情接口**。"""
    handler = _routes(
        orders=[order_list_item(order_sn="2024010199999999")],
        detail=order_detail_payload(order_sn="别人的订单"),
    )
    async with make_client(handler) as client:
        order_detail = tool_by_name(build_order_tools(client), "order_detail")
        result = await order_detail.ainvoke({"orderSn": "2024010100000001"}, config=token_config())

    assert "没有找到" in result
    assert "张三" not in result  # 他人的收货信息一个字都不能出现
    assert all("/order/detail" not in str(request.url.path) for request in mall_requests)


async def test_order_detail_returns_shipping_info_and_passes_token(make_client, mall_requests) -> None:
    handler = _routes(
        orders=[order_list_item(order_sn=_TARGET_SN)], detail=order_detail_payload(order_sn=_TARGET_SN)
    )
    async with make_client(handler) as client:
        order_detail = tool_by_name(build_order_tools(client), "order_detail")
        result = await order_detail.ainvoke({"orderSn": _TARGET_SN}, config=token_config("tok-999"))

    assert "已发货" in result
    assert "SF1234567890" in result
    assert "顺丰速运" in result
    assert "实付 59.70 元" in result
    assert "张三" in result  # 自己的收货信息可以给
    assert "状态码 301" in result
    # 鉴权透传：所有权校验与详情请求都带上了用户 token
    assert mall_requests, "应当发起过请求"
    assert all(request.headers.get("Authorization") == "tok-999" for request in mall_requests)


async def test_order_detail_says_not_shipped_for_unpaid_order(make_client) -> None:
    """未付款订单：不带 shipSn（non_null 省略字段），要给出对应解释而不是空白。"""
    detail = order_detail_payload(order_sn=_TARGET_SN, order_status=101, status_text="未付款", shipped=False)
    handler = _routes(orders=[order_list_item(order_sn=_TARGET_SN)], detail=detail)
    async with make_client(handler) as client:
        order_detail = tool_by_name(build_order_tools(client), "order_detail")
        result = await order_detail.ainvoke({"orderSn": _TARGET_SN}, config=token_config())

    assert "尚未发货（订单还没付款）" in result


async def test_ownership_scan_stops_after_short_page(make_client, mall_requests) -> None:
    """列表不足一页说明已到底 → 不该继续翻页（避免无意义的 3 次请求）。"""
    handler = _routes(orders=[order_list_item(order_sn="other-sn")])
    async with make_client(handler) as client:
        order_detail = tool_by_name(build_order_tools(client), "order_detail")
        result = await order_detail.ainvoke({"orderSn": "want-sn"}, config=token_config())

    assert "没有找到" in result
    assert len(mall_requests) == 1


async def test_ownership_scan_pages_until_found(make_client) -> None:
    """第一页满 50 条未命中 → 继续翻第二页命中 → 再查详情。"""
    full_page = [order_list_item(order_sn=f"sn-{index}") for index in range(50)]
    handler = _routes(
        orders=full_page,
        page2=[order_list_item(order_sn=_TARGET_SN)],
        detail=order_detail_payload(order_sn=_TARGET_SN),
    )
    async with make_client(handler) as client:
        order_detail = tool_by_name(build_order_tools(client), "order_detail")
        result = await order_detail.ainvoke({"orderSn": _TARGET_SN}, config=token_config())

    assert "SF1234567890" in result


async def test_order_detail_requires_login_token(make_client, mall_requests) -> None:
    async with make_client() as client:
        order_detail = tool_by_name(build_order_tools(client), "order_detail")
        result = await order_detail.ainvoke({"orderSn": _TARGET_SN}, config=token_config(token=""))

    assert "未登录" in result
    assert mall_requests == []


# --------------------------------------------------------------------------------------
# logistics
# --------------------------------------------------------------------------------------
async def test_logistics_returns_tracking_number(make_client) -> None:
    handler = _routes(
        orders=[order_list_item(order_sn=_TARGET_SN)], detail=order_detail_payload(order_sn=_TARGET_SN)
    )
    async with make_client(handler) as client:
        logistics = tool_by_name(build_order_tools(client), "logistics")
        result = await logistics.ainvoke({"orderSn": _TARGET_SN}, config=token_config())

    assert "已发货" in result
    assert "SF1234567890" in result
    assert "没有物流轨迹接口" in result  # 不承诺给轨迹


async def test_logistics_explains_unpaid_order(make_client) -> None:
    detail = order_detail_payload(order_sn=_TARGET_SN, order_status=101, status_text="未付款", shipped=False)
    handler = _routes(orders=[order_list_item(order_sn=_TARGET_SN)], detail=detail)
    async with make_client(handler) as client:
        logistics = tool_by_name(build_order_tools(client), "logistics")
        result = await logistics.ainvoke({"orderSn": _TARGET_SN}, config=token_config())

    assert "还没付款" in result
    assert "48 小时" in result


async def test_logistics_refuses_order_not_owned(make_client, mall_requests) -> None:
    handler = _routes(orders=[order_list_item(order_sn="other-sn")], detail=order_detail_payload())
    async with make_client(handler) as client:
        logistics = tool_by_name(build_order_tools(client), "logistics")
        result = await logistics.ainvoke({"orderSn": _TARGET_SN}, config=token_config())

    assert "没有找到" in result
    assert all("/order/detail" not in str(request.url.path) for request in mall_requests)
