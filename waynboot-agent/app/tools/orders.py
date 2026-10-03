"""订单类只读工具（需登录，凭证由运行配置透传）。

**越权防护（重要）**：mall 的 ``GET /order/detail/{orderSn}`` **不校验订单归属**
（`OrderQuerySupport.java:165-166` 只按单号查，见 docs/API_CONTRACT.md §5 A15），
任意登录用户凭单号就能读到他人订单的收货人、手机号、地址。
Agent 会被用户（甚至被提示注入）诱导去查任意单号，因此这里加了一道 **fail-closed 的所有权前置校验**：
先在当前用户自己的订单列表里找这个单号，找不到就一律回"没找到"，绝不把详情透出去。
"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.mall.client import MallClient
from app.mall.errors import MallError
from app.observability import get_logger
from app.tools.base import NO_TOKEN_MESSAGE, clip, describe_error, money, nz, token_of

logger = get_logger(__name__)

#: showType 语义（mall 侧 OrderUtil.orderStatus：
#: 0 全部 / 1→101 待付款 / 2→201 待发货 / 3→301 待收货 / 4→401 待评价）
_SHOW_TYPE_TEXT = {0: "全部", 1: "待付款", 2: "待发货", 3: "待收货", 4: "待评价"}

#: 所有权校验的扫描范围：当前用户最近 3 页 × 50 笔 = 150 笔
_OWNERSHIP_SCAN_PAGES = 3
_OWNERSHIP_SCAN_PAGE_SIZE = 50

_ORDER_NOT_FOUND = (
    "没有找到这笔订单，请让用户核对订单号（订单号可以在「我的订单」里查看）。"
    "（系统提示：也可能是该订单不属于当前用户，或用户订单太多、该单不在最近 150 笔内。）"
)


async def find_own_order(
    client: MallClient,
    token: str,
    *,
    order_sn: str | None = None,
    order_id: int | None = None,
) -> dict | None:
    """在**当前用户自己的订单列表**里找这笔订单，找到就返回列表项，否则 None。

    mall 的 `/order/detail/{orderSn}` 与退款/取消接口**都不校验订单归属**（见 API_CONTRACT §5），
    所以 Agent 侧必须自己兜住：只有出现在本人订单列表里的订单才允许查询/操作（fail-closed）。

    一旦某页返回条数不足一页，说明已经翻到底，提前结束，不做无谓请求。
    """
    for page_num in range(1, _OWNERSHIP_SCAN_PAGES + 1):
        data = await client.get(
            "/order/list",
            token=token,
            params={"showType": 0, "pageNum": page_num, "pageSize": _OWNERSHIP_SCAN_PAGE_SIZE},
        )
        orders = (data or {}).get("data") or []
        for order in orders:
            if order_sn is not None and order.get("orderSn") == order_sn:
                return order
            if order_id is not None and order.get("id") == order_id:
                return order
        if len(orders) < _OWNERSHIP_SCAN_PAGE_SIZE:
            break
    return None


def build_order_tools(client: MallClient) -> list:
    """构造订单工具。闭包持有 client，便于测试注入打桩后的客户端。"""

    @tool
    async def my_orders(config: RunnableConfig, showType: int = 0) -> str:
        """查询当前登录用户的订单列表。

        Args:
            showType: 订单类型筛选：0 全部、1 待付款、2 待发货、3 待收货、4 待评价。默认 0。
        """
        token = token_of(config)
        if not token:
            return NO_TOKEN_MESSAGE
        try:
            data = await client.get("/order/list", token=token, params={"showType": showType, "pageSize": 10})
        except MallError as exc:
            return describe_error(exc)

        # 注意双层 data：外层信封的 data 里再有一个 data 数组（见 API_CONTRACT §3.3）
        orders = (data or {}).get("data") or []
        label = _SHOW_TYPE_TEXT.get(showType, "全部")
        if not orders:
            return f"当前账号没有{label}订单。"

        lines = [f"{label}订单共 {len(orders)} 笔（最多显示 10 笔）："]
        for order in orders:
            goods = "、".join(
                f"{clip(g.get('goodsName'), 20)}x{g.get('number')}"
                for g in (order.get("goodsList") or [])[:3]
            )
            lines.append(
                f"- 订单号 {order.get('orderSn')}（订单id={order.get('id')}）"
                f"｜{nz(order.get('orderStatusText'))}"
                f"｜实付 {money(order.get('actualPrice'))} 元"
                f"｜{goods or '（无商品行）'}"
            )
        lines.append("（提示：要问详细进度或物流，用上面的订单号再调 order_detail 或 logistics。）")
        return "\n".join(lines)

    @tool
    async def order_detail(orderSn: str, config: RunnableConfig) -> str:
        """查询一笔订单的完整详情：状态、金额、收货信息、发货物流、退款/售后信息。

        Args:
            orderSn: 订单号（形如 2024010112345678），应来自 my_orders 的结果。
        """
        token = token_of(config)
        if not token:
            return NO_TOKEN_MESSAGE
        try:
            if await find_own_order(client, token, order_sn=orderSn) is None:
                return _ORDER_NOT_FOUND
            detail = await client.get(f"/order/detail/{orderSn}", token=token)
        except MallError as exc:
            return describe_error(exc)

        detail = detail or {}
        lines = [
            f"订单 {nz(detail.get('orderSn'))}：{nz(detail.get('orderStatusText'))}"
            f"（状态码 {nz(detail.get('orderStatus'))}）",
            f"实付 {money(detail.get('actualPrice'))} 元"
            f"（商品 {money(detail.get('goodsPrice'))} + 运费 {money(detail.get('freightPrice'))}"
            f" - 优惠 {money(detail.get('couponPrice'))}）",
        ]
        if detail.get("createTime"):
            lines.append(f"下单时间：{detail['createTime']}")
        if detail.get("payTime"):
            lines.append(f"支付时间：{detail['payTime']}（{nz(detail.get('payTypeText'), '支付方式未知')}）")
        if detail.get("shipSn"):
            lines.append(
                f"物流：{nz(detail.get('shipChannel'))}，单号 {detail['shipSn']}，"
                f"发货时间 {nz(detail.get('shipTime'))}"
            )
        elif str(detail.get("orderStatus")) == "101":
            lines.append("物流：尚未发货（订单还没付款）。")
        else:
            lines.append("物流：尚未发货（还没有快递单号）。")
        if detail.get("refundAmount") or detail.get("aftersaleStatus"):
            lines.append(
                f"退款/售后：售后状态 {nz(detail.get('aftersaleStatus'))}，"
                f"退款金额 {money(detail.get('refundAmount'))} 元，申请时间 {nz(detail.get('refundTime'))}"
            )
        goods = detail.get("orderGoodsVOList") or []
        if goods:
            lines.append(
                "商品明细："
                + "；".join(
                    f"{clip(g.get('goodsName'), 20)} x{g.get('number')}（{money(g.get('price'))} 元）"
                    for g in goods[:5]
                )
            )
        lines.append(
            f"收货信息：{nz(detail.get('consignee'))} {nz(detail.get('mobile'))} "
            f"{clip(detail.get('address'), 40)}"
        )
        return "\n".join(lines)

    @tool
    async def logistics(orderSn: str, config: RunnableConfig) -> str:
        """只查物流信息（快递公司、快递单号、发货时间），回答「我的快递到哪了」。

        Args:
            orderSn: 订单号，应来自 my_orders 的结果。
        """
        token = token_of(config)
        if not token:
            return NO_TOKEN_MESSAGE
        try:
            if await find_own_order(client, token, order_sn=orderSn) is None:
                return _ORDER_NOT_FOUND
            detail = await client.get(f"/order/detail/{orderSn}", token=token) or {}
        except MallError as exc:
            return describe_error(exc)

        status_text = nz(detail.get("orderStatusText"))
        if detail.get("shipSn"):
            return (
                f"订单 {nz(detail.get('orderSn'))} 已发货：{nz(detail.get('shipChannel'))}，"
                f"单号 {detail['shipSn']}，发货时间 {nz(detail.get('shipTime'))}。"
                f"当前订单状态：{status_text}。"
                "（说明：商城没有物流轨迹接口，只能给到发货信息；更细的轨迹建议用这个单号去快递公司官网查。）"
            )
        if str(detail.get("orderStatus")) == "101":
            return (
                f"订单 {nz(detail.get('orderSn'))} 还没付款，所以还没发货。"
                "付款后一般 48 小时内发出（节假日顺延）。"
            )
        return f"订单 {nz(detail.get('orderSn'))} 当前状态是「{status_text}」，暂时还没有快递单号。"

    return [my_orders, order_detail, logistics]
