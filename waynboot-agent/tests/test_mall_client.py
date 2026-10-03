"""mall 客户端测试：信封解包、错误归一、鉴权透传。

全部通过 ``httpx.MockTransport`` 打桩，不联网、不依赖 mall 进程。
"""

import json

import httpx
import pytest

from app.mall.client import MallClient
from app.mall.errors import MallAuthError, MallBusinessError, MallError, MallUnavailableError
from tests.conftest import fail, ok


async def test_get_unwraps_data(make_client) -> None:
    """成功信封只返回 data，调用方拿不到 code/msg（信封细节收口在客户端内部）。"""
    async with make_client(
        lambda request: httpx.Response(200, json=ok({"id": 7, "name": "测试商品"}))
    ) as client:
        assert await client.get("/goods/detail/7") == {"id": 7, "name": "测试商品"}


async def test_get_returns_none_when_data_absent(make_client) -> None:
    """data 为 null（如保存成功类接口）时返回 None，而不是报错。"""
    async with make_client(lambda request: httpx.Response(200, json=ok(None))) as client:
        assert await client.get("/cart/goodsCount") is None


async def test_request_url_and_params(make_client, mall_requests) -> None:
    """基地址拼接与查询参数传递符合预期（None 值不下传）。"""
    async with make_client() as client:
        await client.get("/order/list", params={"showType": 1, "pageNum": None, "pageSize": 5})

    request = mall_requests[-1]
    assert str(request.url).startswith("http://mall.test/order/list?")
    assert "showType=1" in str(request.url)
    assert "pageSize=5" in str(request.url)
    assert "pageNum" not in str(request.url)


async def test_post_sends_json_body(make_client, mall_requests) -> None:
    """写操作请求体按 JSON 发送（mall 侧用 @RequestBody 接收）。"""
    payload = {"goodsId": 1, "productId": 2, "number": 3}
    async with make_client() as client:
        await client.post("/cart", json=payload)

    assert json.loads(mall_requests[-1].content) == payload
    assert mall_requests[-1].method == "POST"


async def test_token_is_passed_through_verbatim(make_client, mall_requests) -> None:
    """鉴权透传（决策 D2）：原样携带，不改写、不加前缀。"""
    async with make_client() as client:
        await client.get("/order/list", token="abc.def.ghi")

    assert mall_requests[-1].headers["Authorization"] == "abc.def.ghi"


async def test_no_authorization_header_when_token_absent(make_client, mall_requests) -> None:
    """匿名接口不带 Authorization（避免把空串当 token 传过去）。"""
    async with make_client() as client:
        await client.get("/home/recommGoodsList")

    assert "Authorization" not in mall_requests[-1].headers


async def test_business_error_carries_code_and_msg(make_client) -> None:
    """业务拒绝：抛 MallBusinessError，msg 可直接作为话术素材。"""
    async with make_client(lambda request: httpx.Response(200, json=fail(5002, "订单不存在"))) as client:
        with pytest.raises(MallBusinessError) as excinfo:
            await client.get("/order/detail/NOT_EXIST")

    assert excinfo.value.code == 5002
    assert excinfo.value.user_message == "订单不存在"
    assert excinfo.value.path == "/order/detail/NOT_EXIST"


async def test_auth_code_in_envelope_raises_auth_error(make_client) -> None:
    """信封内 code=401 视为登录态失效（引导用户登录，而非报"系统错误"）。"""
    async with make_client(
        lambda request: httpx.Response(200, json=fail(401, "没有权限，请联系管理员授权"))
    ) as client:
        with pytest.raises(MallAuthError) as excinfo:
            await client.get("/order/list", token="expired")

    assert isinstance(excinfo.value, MallBusinessError)  # 便于上层一次捕获
    assert "登录" in excinfo.value.user_message


async def test_http_401_without_envelope_raises_auth_error(make_client) -> None:
    """HTTP 层直接 401（拦截器提前返回）同样归一为登录态失效。"""
    async with make_client(lambda request: httpx.Response(401, text="unauthorized")) as client:
        with pytest.raises(MallAuthError):
            await client.get("/order/list", token="expired")


async def test_http_500_raises_unavailable(make_client) -> None:
    """mall 自身故障 → 可降级错误（上层给"稍后再试/转人工"话术）。"""
    async with make_client(lambda request: httpx.Response(500, text="boom")) as client:
        with pytest.raises(MallUnavailableError):
            await client.get("/home/recommGoodsList")


async def test_non_json_body_raises_unavailable(make_client) -> None:
    """响应体不是 JSON（例如被网关替换为 HTML 错误页）→ 可降级错误。"""
    async with make_client(lambda request: httpx.Response(200, text="<html>502</html>")) as client:
        with pytest.raises(MallUnavailableError):
            await client.get("/home/recommGoodsList")


async def test_broken_envelope_raises_unavailable(make_client) -> None:
    """合法 JSON 但不符合 R 信封（无 code 字段）→ 视为契约被破坏。"""
    async with make_client(lambda request: httpx.Response(200, json={"result": "whatever"})) as client:
        with pytest.raises(MallUnavailableError):
            await client.get("/home/recommGoodsList")


async def test_network_error_raises_unavailable(make_client) -> None:
    """连不上 mall（未启动/端口不通）→ 可降级错误，不冒泡为未知异常。"""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with make_client(refuse) as client:
        with pytest.raises(MallUnavailableError):
            await client.get("/home/recommGoodsList")


async def test_timeout_raises_unavailable(make_client) -> None:
    """超时 → 可降级错误（附超时秒数由设置决定）。"""

    def hang(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with make_client(hang) as client:
        with pytest.raises(MallUnavailableError):
            await client.post("/cart", json={"goodsId": 1})


async def test_ping_true_on_success_envelope(make_client, mall_requests) -> None:
    """探活成功，且打的是真实存在的路径（上游把 recommend 拼成了 recommon，别"修正"它）。"""
    async with make_client() as client:
        assert await client.ping() is True

    assert mall_requests[-1].url.path == "/home/recommonGoodsList"


async def test_ping_false_on_any_mall_error(make_client) -> None:
    """探活失败时不抛异常，返回 False（供 /readyz 决定状态码）。"""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with make_client(refuse) as client:
        assert await client.ping() is False


async def test_base_url_trailing_slash_is_normalized(make_client) -> None:
    """基地址结尾多余的 / 不应产生 //path 这种双斜杠路径。"""
    async with make_client() as client:
        assert client.base_url == "http://mall.test"
    async with MallClient("http://mall.test/") as client:
        assert client.base_url == "http://mall.test"


def test_all_mall_errors_share_base_class() -> None:
    """三类异常同源，上层可一次性兜底捕获。"""
    assert issubclass(MallBusinessError, MallError)
    assert issubclass(MallAuthError, MallBusinessError)
    assert issubclass(MallUnavailableError, MallError)
