"""优惠券工具测试（mock HTTP，不依赖 mall 进程）。"""

import httpx

from app.tools.coupons import build_coupon_tools
from tests.conftest import ok, token_config, tool_by_name
from tests.payloads import coupon_page, coupon_record


async def test_my_coupons_lists_threshold_and_status(make_client, mall_requests) -> None:
    """门槛字段叫 min、状态靠 useStatus 映射成中文。"""
    records = [
        coupon_record(title="满 100 减 20", discount=20, threshold=100, use_status=0),
        coupon_record(title="新人 10 元券", discount=10, threshold=50, use_status=2),
    ]
    async with make_client(lambda request: httpx.Response(200, json=ok(coupon_page(records)))) as client:
        my_coupons = tool_by_name(build_coupon_tools(client), "my_coupons")
        result = await my_coupons.ainvoke({}, config=token_config())

    assert "满 100 减 20（未使用）" in result
    assert "满 50 减 10（已过期）" in result
    assert "有效期至 2026-12-31 23:59:59" in result
    assert mall_requests[-1].headers.get("Authorization") == "tok-test"


async def test_my_coupons_reports_empty(make_client) -> None:
    async with make_client(lambda request: httpx.Response(200, json=ok(coupon_page([])))) as client:
        my_coupons = tool_by_name(build_coupon_tools(client), "my_coupons")
        result = await my_coupons.ainvoke({}, config=token_config())

    assert "没有已领取的优惠券" in result


async def test_my_coupons_requires_login_token(make_client, mall_requests) -> None:
    async with make_client() as client:
        my_coupons = tool_by_name(build_coupon_tools(client), "my_coupons")
        result = await my_coupons.ainvoke({}, config=token_config(token=""))

    assert "未登录" in result
    assert mall_requests == []


async def test_my_coupons_degrades_when_mall_unavailable(make_client) -> None:
    async with make_client(lambda request: httpx.Response(500, text="boom")) as client:
        my_coupons = tool_by_name(build_coupon_tools(client), "my_coupons")
        result = await my_coupons.ainvoke({}, config=token_config())

    assert "暂时不可用" in result
