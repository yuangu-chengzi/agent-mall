"""入口层测试：存活探针、就绪探针、请求 ID 贯穿。

用 TestClient（同步）+ 打桩 ``MallClient.ping``，不依赖真实 mall。
"""

from fastapi.testclient import TestClient

from app.main import app
from app.mall.client import MallClient


def _patch_ping(monkeypatch, reachable: bool) -> None:
    """把 mall 探活结果固定下来。"""

    async def fake_ping(self: MallClient) -> bool:
        return reachable

    monkeypatch.setattr(MallClient, "ping", fake_ping)


def test_healthz_is_ok_even_when_mall_is_down(monkeypatch, fake_redis) -> None:
    """存活探针不依赖任何外部服务：mall 挂了它也必须返回 200。"""
    _patch_ping(monkeypatch, reachable=False)
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_readyz_ok_when_mall_reachable(monkeypatch, fake_redis) -> None:
    """mall 可达 → 200 + reachable=true（联调时先看这个）。"""
    _patch_ping(monkeypatch, reachable=True)
    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mall"]["reachable"] is True
    assert body["mall"]["base_url"]


def test_readyz_degraded_when_mall_unreachable(monkeypatch, fake_redis) -> None:
    """mall 不可达 → 503，让编排系统知道本服务还不能接流量。"""
    _patch_ping(monkeypatch, reachable=False)
    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_request_id_is_generated_and_echoed(monkeypatch, fake_redis) -> None:
    """未带 X-Request-Id 时自动生成并回写；带则原样透传（便于跨服务串联日志）。"""
    _patch_ping(monkeypatch, reachable=True)
    with TestClient(app) as client:
        generated = client.get("/healthz")
        forwarded = client.get("/healthz", headers={"X-Request-Id": "trace-abc"})

    assert generated.headers["X-Request-Id"]
    assert forwarded.headers["X-Request-Id"] == "trace-abc"
