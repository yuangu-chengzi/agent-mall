"""登录代理 + 内置页面测试。

Agent 不自己做鉴权，只把 mall 的「发验证码 / 验证码登录」转发出去，并把 mall 的失败原因原样透给前端。
"""

import httpx
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.mall.client import MallClient
from tests.conftest import fail, ok


def _stub_mall(monkeypatch, handler) -> list[httpx.Request]:
    """把 lifespan 里的 MallClient 换成打桩客户端，并返回请求记录。"""
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def factory(base_url: str, timeout: float = 10.0) -> MallClient:
        return MallClient(base_url, timeout=timeout, transport=httpx.MockTransport(recording))

    monkeypatch.setattr(main_module, "MallClient", factory)
    return seen


def test_root_serves_mall_page(fake_redis) -> None:
    """根路径给商城首页（H5 单页），客服页在 `/chat`。"""
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "wayn 商城" in body
    assert "/ui/js/app.js" in body  # 页面靠这个模块装配路由
    assert "/chat" in body  # 客服浮标指向聊天页


def test_chat_serves_agent_page(fake_redis) -> None:
    """聊天页仍然是"打开就能用"的单文件页面（登录 + SSE + 确认卡片）。"""
    with TestClient(app) as client:
        response = client.get("/chat")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "AI 客服" in body
    assert "/api/login" in body
    assert "/api/chat/stream" in body
    assert "/api/chat/confirm" in body  # 确认卡片要调它
    assert "pending_action" in body


def test_send_code_proxies_to_mall(monkeypatch, fake_redis) -> None:
    seen = _stub_mall(monkeypatch, lambda request: httpx.Response(200, json=ok(None)))

    with TestClient(app) as client:
        response = client.post("/api/login/code", json={"mobile": "13800000001"})

    assert response.status_code == 200
    assert response.json()["sent"] is True
    assert seen[-1].url.path == "/genMobileCode"
    assert b"13800000001" in seen[-1].content


def test_login_returns_token(monkeypatch, fake_redis) -> None:
    seen = _stub_mall(monkeypatch, lambda request: httpx.Response(200, json=ok("token-abc")))

    with TestClient(app) as client:
        response = client.post("/api/login", json={"mobile": "13800000001", "yzm": "1234"})

    assert response.status_code == 200
    assert response.json() == {"token": "token-abc", "mobile": "13800000001"}
    assert seen[-1].url.path == "/login"
    assert b"1234" in seen[-1].content


def test_login_surfaces_mall_rejection(monkeypatch, fake_redis) -> None:
    """验证码错就是验证码错：把 mall 的原话带给前端，不糊成"系统错误"。"""
    _stub_mall(monkeypatch, lambda request: httpx.Response(200, json=fail(5110, "验证码输入错误")))

    with TestClient(app) as client:
        response = client.post("/api/login", json={"mobile": "13800000001", "yzm": "0000"})

    assert response.status_code == 400
    assert response.json()["detail"]["message"] == "验证码输入错误"


def test_login_reports_unavailable_mall(monkeypatch, fake_redis) -> None:
    """mall 挂了要给一句可读的话，而不是 500 白屏。"""
    _stub_mall(monkeypatch, lambda request: httpx.Response(503, text="down"))

    with TestClient(app) as client:
        response = client.post("/api/login", json={"mobile": "13800000001", "yzm": "1234"})

    assert response.status_code == 400
    assert "登录失败" in response.json()["detail"]["message"]


def test_login_validates_mobile_format(monkeypatch, fake_redis) -> None:
    _stub_mall(monkeypatch, lambda request: httpx.Response(200, json=ok("x")))

    with TestClient(app) as client:
        assert client.post("/api/login", json={"mobile": "123", "yzm": "1234"}).status_code == 422
        assert client.post("/api/login/code", json={"mobile": "abc"}).status_code == 422
