"""写操作工具（**必须人工确认后才会执行**）。

三道闸门（决策 D3/D4 + A15 越权防护），顺序不能变：

1. **前置校验**：先确认这笔订单/SKU 属于当前用户、且当前状态允许这个动作——
   把"注定失败"或"越权"的请求拦在确认弹窗之前，别让用户白点一次；
2. **人工确认**：``interrupt()`` 暂停图，把"要做什么、什么后果、能不能撤回"交给用户拍板；
   **确认之前不会发出任何写请求**；
3. **幂等执行**：确认后带幂等键执行，防"连点确认"重复提交（mall 侧虽有幂等，但语义各不相同，
   例如重复加购是**累加数量**，那道防线挡不住）。

⚠️ 恢复（``Command(resume=...)``）时**整个工具函数会从头重跑**，所以：
- 写操作必须放在 ``interrupt()`` **之后**（否则确认前就生效了）；
- 恢复请求同样要带 token（前面几步校验会重新执行，这是好事：重新校验一遍状态）。
"""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.types import interrupt

from app.mall.client import MallClient
from app.mall.errors import MallError
from app.memory.idempotency import IdempotencyStore
from app.observability import get_logger
from app.tools.base import (
    ESCALATE_TOOL_NAME,
    NO_TOKEN_MESSAGE,
    clip,
    describe_error,
    money,
    nz,
    thread_of,
    token_of,
)
from app.tools.orders import find_own_order

logger = get_logger(__name__)

_NOT_OWN_ORDER = (
    "没有找到这笔订单，请让用户核对一下（订单要在「我的订单」里能看到，我才能操作）。"
    "（系统提示：也可能是该订单不属于当前用户。）"
)


def _approved(decision: Any) -> bool:
    """把确认结果统一成布尔值（前端可能回 dict，也可能回字符串）。"""
    if isinstance(decision, dict):
        return bool(decision.get("approved"))
    return str(decision).strip().lower() in {"approve", "approved", "yes", "true", "ok", "确认", "同意"}


def _identity(config: RunnableConfig) -> tuple[str | None, str | None]:
    return token_of(config), thread_of(config)


def build_write_tools(client: MallClient, idempotency: IdempotencyStore) -> list:
    """构造写操作工具（闭包持有 mall 客户端与幂等存储）。"""

    @tool
    async def request_refund(orderId: int, config: RunnableConfig) -> str:
        """为当前用户的订单申请退款（**需要用户先确认**）。

        只会把订单置为「申请退款中」，**不会立刻退钱**；平台审核后才真正退款。
        只有「已付款未发货」的订单可以申请。

        Args:
            orderId: 订单 id（取 my_orders 结果里的「订单id=」，不是订单号）。
        """
        token, thread_id = _identity(config)
        if not token:
            return NO_TOKEN_MESSAGE

        try:
            order = await find_own_order(client, token, order_id=orderId)
        except MallError as exc:
            return describe_error(exc)
        if order is None:
            return _NOT_OWN_ORDER

        # handleOption 是 mall 用同一套状态机算出来的"当前可否退款"，比我们自己猜状态可靠
        if not (order.get("handleOption") or {}).get("refund"):
            return (
                f"订单 {nz(order.get('orderSn'))} 当前状态是「{nz(order.get('orderStatusText'))}」，"
                "不支持申请退款（只有已付款且未发货的订单可以）。"
            )

        goods = "、".join(
            f"{clip(item.get('goodsName'), 20)} x{item.get('number')}"
            for item in (order.get("goodsList") or [])[:3]
        )
        decision = interrupt(
            {
                "action": "request_refund",
                "ask": f"确认为订单 {nz(order.get('orderSn'))} 申请退款吗？",
                "summary": f"订单 {nz(order.get('orderSn'))}（{goods}）申请退款",
                "impact": "订单会进入「申请退款中」，之后不能再取消或确认收货；真正退款由平台审核后处理。",
                "reversible": False,
                "order_id": orderId,
                "order_sn": order.get("orderSn"),
                "session_id": thread_id,
            }
        )
        if not _approved(decision):
            return "用户取消了退款申请，没有做任何修改。"

        key = IdempotencyStore.make_key(thread_id, "refund", orderId)

        async def call() -> str:
            await client.post(f"/order/refund/{orderId}", token=token)
            return (
                f"已为订单 {nz(order.get('orderSn'))} 提交退款申请，订单现在是「申请退款中」，"
                "平台审核通过后款项会按原路退回。"
            )

        try:
            result, executed = await idempotency.run_once(key, call)
        except MallError as exc:
            return describe_error(exc)
        if not executed:
            return f"（这笔退款申请刚刚已经提交过了，没有重复提交。上次结果：{result}）"
        return result

    @tool
    async def add_to_cart(config: RunnableConfig, goodsId: int, productId: int, number: int = 1) -> str:
        """把商品加入当前用户的购物车（**需要用户先确认**）。

        注意：同一个 SKU 重复加购会**累加数量**；库存按可售库存校验。

        Args:
            goodsId: 商品 id（来自 search_goods 或 goods_detail）。
            productId: SKU id（来自 goods_detail 的 SKU 列表）。
            number: 数量，默认 1。
        """
        token, thread_id = _identity(config)
        if not token:
            return NO_TOKEN_MESSAGE
        if number <= 0:
            return "（系统提示：数量必须大于 0，请向用户确认要买几件。）"

        try:
            data = await client.get(f"/goods/detail/{goodsId}") or {}
        except MallError as exc:
            return describe_error(exc)

        info = data.get("info") or {}
        products = data.get("productList") or []
        product = next((item for item in products if item.get("id") == productId), None)
        if not info or product is None:
            return f"没有找到商品 id={goodsId} 下 id={productId} 的规格，请让用户重新选择规格。"

        stock = product.get("number")
        if isinstance(stock, int) and stock < number:
            return f"该规格当前可售库存只有 {stock} 件，买不了 {number} 件，请让用户调整数量。"

        spec = "-".join(str(x) for x in (product.get("specifications") or [])) or "默认规格"
        price = product.get("price")
        decision = interrupt(
            {
                "action": "add_to_cart",
                "ask": f"确认把「{clip(info.get('name'), 30)} {spec}」x{number} 加入购物车吗？",
                "summary": (
                    f"{clip(info.get('name'), 30)} {spec} x{number}"
                    f"（单价 {money(price)} 元，合计约 {money(_times(price, number))} 元）"
                ),
                "impact": "购物车会新增或累加这件商品（重复加购是累加数量）。",
                "reversible": True,
                "goods_id": goodsId,
                "product_id": productId,
                "number": number,
                "session_id": thread_id,
            }
        )
        if not _approved(decision):
            return "用户取消了加购，购物车没有变化。"

        key = IdempotencyStore.make_key(thread_id, "cart", goodsId, productId)

        async def call() -> str:
            await client.post(
                "/cart", token=token, json={"goodsId": goodsId, "productId": productId, "number": number}
            )
            return f"已把「{clip(info.get('name'), 30)} {spec}」x{number} 加入购物车。"

        try:
            result, executed = await idempotency.run_once(key, call)
        except MallError as exc:
            return describe_error(exc)
        if not executed:
            return f"（这件商品刚刚已经加过了，没有重复加购。上次结果：{result}）"
        return result

    @tool
    async def escalate_to_human(reason: str, config: RunnableConfig) -> str:
        """转人工客服：用户明确要求转人工、或你连续两次没能解决他的问题时调用。

        Args:
            reason: 转人工的原因，一句话（例如"用户要求人工核实退款进度"）。
        """
        logger.info(
            "转人工：session=%s user_token=%s reason=%s",
            thread_of(config),
            "有" if token_of(config) else "无",
            reason,
        )
        return (
            "（系统提示：已记录转人工请求。请告诉用户已为他转接人工客服，"
            "人工在线时间 9:00-21:00，请留意会话消息；不要承诺具体的等待时长。）"
        )

    return [request_refund, add_to_cart, escalate_to_human]


def _times(price: Any, number: int) -> Any:
    """单价 × 数量（仅用于给用户看个大概，算不出来就不显示）。"""
    try:
        return float(price) * number
    except (TypeError, ValueError):
        return None


__all__ = ["build_write_tools", "ESCALATE_TOOL_NAME"]
