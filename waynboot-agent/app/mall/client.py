"""mall（waynboot-mobile-api）HTTP 客户端。

只做三件事（其余交给上层）：

1. **鉴权透传**：把调用方传入的 Authorization 原样带给 mall（决策 D2，见 docs/DECISIONS.md）；
2. **信封解包**：把 ``R{code, msg, data}`` 拆成 ``data``，失败则抛异常；
3. **错误归一**：把「未登录 / 业务拒绝 / 服务不可用」归一成 :mod:`app.mall.errors` 中的三类异常，
   使上层（工具层、图节点）只需按类型决定话术，不必解析 HTTP 细节。

接口清单与字段契约见 docs/API_CONTRACT.md。
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.mall.errors import MallAuthError, MallBusinessError, MallError, MallUnavailableError
from app.observability import get_logger

logger = get_logger(__name__)

#: mall 统一信封中表示成功的 code（com.wayn.util.enums.ReturnCodeEnum.SUCCESS）
SUCCESS_CODE = 200
#: 表示未登录/无权限的 code（mall 的 401=没有权限、403=认证失败，两者都按"需登录"处理）
AUTH_CODES = frozenset({401, 403})


class MallClient:
    """mall 的异步 HTTP 客户端。

    生命周期与进程一致（FastAPI lifespan 中创建与关闭），底层连接池由 httpx 维护。
    线程/协程安全：``httpx.AsyncClient`` 可在多个协程间共享。
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        :param base_url: mall 基地址，例如 ``http://localhost:82``
        :param timeout: 单次请求超时（秒），同时作用于连接、读取、写入
        :param transport: 自定义传输层（仅测试用；生产留空走真实网络）
        """
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout),
            transport=transport,
            follow_redirects=False,
            # ⚠️ 显式关掉"读系统代理"：httpx 默认 trust_env=True，会去读
            # Windows 注册表/环境变量里的代理（本机就有个 127.0.0.1:33210 的加速器）。
            # 那样连 http://localhost:82 都会被塞进代理，代理自然够不着本机服务 →
            # 表现为 /readyz 说 mall 不可达、图片接口 502，而直接 curl 却是好的（踩过）。
            # mall 是**同机/内网**的兄弟服务，本来就不该走代理。
            # 注意：LLM 客户端不在此列——它要访问公网，可能真的需要代理。
            trust_env=False,
        )

    @property
    def base_url(self) -> str:
        """mall 基地址。"""
        return self._base_url

    async def get(self, path: str, *, token: str | None = None, params: dict[str, Any] | None = None) -> Any:
        """GET 请求，返回解包后的 ``data``。"""
        return await self.request("GET", path, token=token, params=params)

    async def post(
        self,
        path: str,
        *,
        token: str | None = None,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """POST 请求，返回解包后的 ``data``。"""
        return await self.request("POST", path, token=token, params=params, json=json)

    async def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        """发送请求并解包信封；失败时抛出 :class:`~app.mall.errors.MallError` 子类。"""
        started = time.perf_counter()
        try:
            data = await self._send(method, path, token=token, params=params, json=json)
        except MallError as exc:
            level = logging.WARNING if isinstance(exc, MallUnavailableError) else logging.INFO
            logger.log(level, "%s %s 失败：%s（耗时 %.0fms）", method, path, exc, _elapsed_ms(started))
            raise
        logger.debug("%s %s 成功（耗时 %.0fms）", method, path, _elapsed_ms(started))
        return data

    #: 探活用的匿名接口。⚠️ 路径是 `/home/recommonGoodsList`——上游把 recommend 拼成了 recommon
    #: （见 docs/API_CONTRACT.md §3.1）；按正确拼写请求会被当成静态资源兜底成 code=500，
    #: 于是探活永远返回"不可达"（真机联调时踩到过）。
    PING_PATH = "/home/recommonGoodsList"

    async def ping(self) -> bool:
        """探活：请求一个匿名接口，能拿到成功信封即视为可用（供 /readyz 使用）。"""
        try:
            await self.get(self.PING_PATH)
        except MallError:
            return False
        return True

    async def raw_request(
        self,
        method: str,
        path: str,
        *,
        params: Any | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """**原样**转发一次请求，返回未解包的 :class:`httpx.Response`。

        给 BFF 代理用（``app/mall/proxy.py``）：前端要看到和直连 mall 完全一致的响应
        （业务错误也是 HTTP 200 + ``code`` 字段），所以这一层绝不能解信封。
        复用同一个连接池，避免为代理再养一个客户端。
        """
        try:
            return await self._client.request(method, path, params=params, content=content, headers=headers)
        except httpx.HTTPError as exc:
            raise MallUnavailableError(f"{method} {path} 网络异常：{exc.__class__.__name__}: {exc}") from exc

    async def aclose(self) -> None:
        """关闭底层连接池。"""
        await self._client.aclose()

    async def __aenter__(self) -> MallClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _send(
        self,
        method: str,
        path: str,
        *,
        token: str | None,
        params: dict[str, Any] | None,
        json: Any | None,
    ) -> Any:
        """真实发请求 + 解包。所有异常在此归一。"""
        headers = {}
        if token:
            # 裸 token 与 Bearer 均可：mall 侧有归一化过滤器（见 API_CONTRACT 1.2）
            headers["Authorization"] = token

        if params:
            # httpx 会把 None 渲染成 "key="（空串）而不是丢弃；工具层的可选参数（keyword/showType…）
            # 未填时必须是"不传该参数"，否则 mall 侧会收到空串。这里显式剔除。
            params = {key: value for key, value in params.items() if value is not None}

        try:
            response = await self._client.request(method, path, params=params, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise MallUnavailableError(f"{method} {path} 网络异常：{exc.__class__.__name__}: {exc}") from exc

        if response.status_code in AUTH_CODES:
            raise MallAuthError(response.status_code, path=path)
        if response.status_code >= 400:
            raise MallUnavailableError(f"{method} {path} 返回 HTTP {response.status_code}")

        try:
            envelope = response.json()
        except ValueError as exc:
            raise MallUnavailableError(f"{method} {path} 响应不是合法 JSON") from exc
        if not isinstance(envelope, dict) or "code" not in envelope:
            raise MallUnavailableError(f"{method} {path} 响应不符合 R 信封约定")

        code = envelope.get("code")
        if code != SUCCESS_CODE:
            msg = envelope.get("msg") or "操作失败"
            if code in AUTH_CODES:
                raise MallAuthError(code, msg, path=path)
            raise MallBusinessError(code, msg, path=path)
        return envelope.get("data")


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
