"""BFF 代理测试（``/api/mall/**``）。

关注三件事：
1. **白名单**：只代理商城用到的路径，不能变成访问 mall 任意接口的跳板；
2. **原样**：方法 / 查询串 / 请求体 / Authorization 原样过去，
   响应（含业务错误的 200 信封与图片二进制）原样回来；
3. **不误伤**：代理不挂 LLM 限流与访问闸门（用户购物不该被咨询配额拦住）。
"""

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.main import app
from app.mall.proxy import ProxyPathError, check_path
from tests.conftest import ok


@pytest.fixture
def seen() -> list[httpx.Request]:
    return []


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, seen: list[httpx.Request], fake_redis):
    """把 app.state.mall 换成打桩客户端，并记录它收到的请求。"""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.startswith("/upload/"):
            return httpx.Response(
                200, content=b"\x89PNG\r\n\x1a\nbinary", headers={"content-type": "image/png"}
            )
        if request.url.path == "/order/list":
            # 业务错误：mall 用 HTTP 200 + code 表达失败，代理不能把它改成 4xx
            return httpx.Response(200, json={"code": 5012, "msg": "订单不属于当前用户", "data": None})
        return httpx.Response(200, json=ok({"echo": request.url.path}))

    from app.mall.client import MallClient

    def factory(base_url: str, timeout: float = 10.0) -> MallClient:
        return MallClient(base_url, timeout=timeout, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(main_module, "MallClient", factory)
    with TestClient(app) as c:
        yield c


def test_check_path_allows_mall_prefixes() -> None:
    assert check_path("/goods/detail/1181000") == "/goods/detail/1181000"
    assert check_path("cart/list") == "/cart/list"  # 不带前导斜杠也能归一


@pytest.mark.parametrize(
    "path",
    [
        "/admin/goods/list",  # 不在白名单
        "/../etc/passwd",  # 试图跳目录
        "/goods//../order/list",
    ],
)
def test_check_path_rejects(path: str) -> None:
    with pytest.raises(ProxyPathError):
        check_path(path)


def test_proxy_forwards_method_query_and_token(client: TestClient, seen: list[httpx.Request]) -> None:
    res = client.get(
        "/api/mall/search/result",
        params={"keyword": "床垫", "pageNum": "1"},
        headers={"Authorization": "tok-abc"},
    )

    assert res.status_code == 200
    assert res.json()["code"] == 200
    request = seen[-1]
    assert request.method == "GET"
    # 中文关键词必须原样到达（多一次转义就会搜不到）
    assert request.url.params["keyword"] == "床垫"
    assert request.headers["authorization"] == "tok-abc"


def test_proxy_forwards_json_body(client: TestClient, seen: list[httpx.Request]) -> None:
    res = client.post(
        "/api/mall/cart",
        json={"goodsId": 1181000, "productId": 1, "number": 2},
        headers={"Authorization": "tok-abc", "Content-Type": "application/json"},
    )

    assert res.status_code == 200
    assert seen[-1].content == b'{"goodsId":1181000,"productId":1,"number":2}'


def test_proxy_keeps_upstream_business_error_shape(client: TestClient) -> None:
    """mall 的业务失败是 HTTP 200 + code!=200，代理层不能把它翻译成 4xx/5xx。"""
    res = client.get("/api/mall/order/list", headers={"Authorization": "tok-b"})

    assert res.status_code == 200
    assert res.json() == {"code": 5012, "msg": "订单不属于当前用户", "data": None}


def test_proxy_passes_binary_and_content_type(client: TestClient) -> None:
    """商品图片也走这个代理，必须是原始字节 + 原始 content-type。"""
    res = client.get("/api/mall/upload/x.webp")

    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content == b"\x89PNG\r\n\x1a\nbinary"


def test_proxy_rejects_unlisted_prefix(client: TestClient) -> None:
    res = client.get("/api/mall/admin/user/list")

    assert res.status_code == 404
    assert "不允许代理" in res.json()["detail"]["message"]


def test_proxy_is_not_behind_llm_gates(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """开了访问闸门后，商城代理仍然要能正常访问（闸门只保护花钱的接口）。"""
    app.state.settings = Settings(_env_file=None, access_token="secret-token")

    blocked = client.post("/api/chat", json={"session_id": "s", "message": "hi"})
    proxied = client.get("/api/mall/home/recommonGoodsList")

    assert blocked.status_code == 401
    assert proxied.status_code == 200
