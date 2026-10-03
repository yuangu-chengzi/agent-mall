"""优惠券只读工具（需登录）。

注意 ``/coupon/myList`` 与匿名接口 ``/coupon/list`` 的字段完全不同
（见 API_CONTRACT §3.5），
且服务端**不重算过期**、**不带状态筛选**，已使用/已过期的券也会返回。
"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.mall.client import MallClient
from app.mall.errors import MallError
from app.tools.base import NO_TOKEN_MESSAGE, clip, describe_error, nz, token_of

#: useStatus 语义（MemberCouponResVO.useStatus）
_USE_STATUS_TEXT = {0: "未使用", 1: "已使用", 2: "已过期"}
#: 最多展示多少张券
_TOP_N = 20


def build_coupon_tools(client: MallClient) -> list:
    """构造优惠券工具。闭包持有 client，便于测试注入打桩后的客户端。"""

    @tool
    async def my_coupons(config: RunnableConfig) -> str:
        """查询当前用户已领取的优惠券：券名、使用门槛、面额、有效期、使用状态。"""
        token = token_of(config)
        if not token:
            return NO_TOKEN_MESSAGE
        try:
            data = await client.get("/coupon/myList", token=token, params={"pageSize": _TOP_N}) or {}
        except MallError as exc:
            return describe_error(exc)

        records = data.get("records") or []
        if not records:
            return (
                "当前账号没有已领取的优惠券。可以告诉用户：在商城的领券中心可以领取"
                "（/coupon/list 是匿名接口，后续可加工具）。"
            )

        lines = [f"已领取优惠券共 {nz(data.get('total'), '未知')} 张（最多显示 {_TOP_N} 张）："]
        for coupon in records:
            status = _USE_STATUS_TEXT.get(coupon.get("useStatus"), nz(coupon.get("useStatus")))
            threshold = nz(coupon.get("min"))
            discount = nz(coupon.get("discount"))
            lines.append(
                f"- {clip(coupon.get('title'), 20)}：满 {threshold} 减 {discount}"
                f"（{status}），有效期至 {nz(coupon.get('expireTime'))}"
            )
        lines.append(
            "（提示：门槛与面额字段是 min / discount，单位由商城定义；已使用和已过期的券也会出现在这里，"
            "要按状态自行判断。）"
        )
        return "\n".join(lines)

    return [my_coupons]
