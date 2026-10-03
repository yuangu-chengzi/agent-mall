"""mall 反向代理（BFF）。

**为什么需要它**：商城页面由 Agent 服务（8300）托管，而业务接口在 mall（82）。
浏览器直连 mall 会撞跨域（CORS），要么给 mall 开 CORS 放行 8300，要么由 Agent 代理一层。
选代理的理由：① mall 零改动；② 浏览器只认一个源，cookie/预览/调试都简单；
③ 不用把 mall 直接暴露给浏览器。

**注意**：这里**不做鉴权判断**，只是把 ``Authorization`` 原样转给 mall ——
谁有权限由 mall 自己说了算（和工具层同一条原则，见 DECISIONS D2）。
"""

from __future__ import annotations

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.mall.client import MallClient
from app.mall.errors import MallError
from app.observability import get_logger

logger = get_logger(__name__)

#: 允许代理的路径前缀。刻意做成白名单：Agent 不该变成"能访问 mall 任意路径"的跳板。
#: `upload` 是商品图片的静态资源目录（MvcConfig 里映射到本地上传目录），前端取图也要走这里。
ALLOWED_PREFIXES = frozenset(
    {
        "home",
        "category",
        "goods",
        "search",
        "searchHistory",
        "cart",
        "address",
        "coupon",
        "order",
        "pay",
        "user",
        "login",
        "logout",
        "genMobileCode",
        "upload",
        "diamond",
        "comment",
        "seckill",
    }
)

#: 只转发这几个头，其余（Host、Accept-Encoding、Cookie…）交给 httpx 自己处理。
FORWARD_HEADERS = ("authorization", "content-type", "accept")

#: 允许的 HTTP 方法
ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH"})


class ProxyPathError(ValueError):
    """路径不在白名单内（或试图越权跳转）。"""


def check_path(path: str) -> str:
    """校验并归一化转发路径，返回以 ``/`` 开头的路径。"""
    clean = f"/{path.lstrip('/')}"
    if ".." in clean or "//" in clean:
        raise ProxyPathError("路径不合法")
    prefix = clean.lstrip("/").split("/", 1)[0]
    if prefix not in ALLOWED_PREFIXES:
        raise ProxyPathError(f"不允许代理该路径：/{prefix}")
    return clean


async def forward(request: Request, mall: MallClient, path: str) -> Response:
    """把请求原样转发给 mall，并把响应原样带回。

    不解包 R 信封、不改状态码——前端看到的和直连 mall 完全一致，
    这样"接代理"不会引入第二套语义（前端只认 mall 的 code 字段）。
    """
    try:
        clean = check_path(path)
    except ProxyPathError as exc:
        return JSONResponse(status_code=404, content={"detail": {"message": str(exc)}})

    method = request.method.upper()
    if method not in ALLOWED_METHODS:
        return JSONResponse(status_code=405, content={"detail": {"message": f"不支持的方法 {method}"}})

    headers = {key: value for key, value in request.headers.items() if key.lower() in FORWARD_HEADERS}
    # 原样带上查询串：httpx 直接把 multi_items 拼回 URL，不做二次编码，
    # 中文关键词不会被二次转义（手拼字符串容易在这里出错）。
    params = list(request.query_params.multi_items())
    body = await request.body() if method in {"POST", "PUT", "PATCH", "DELETE"} else None

    try:
        upstream = await mall.raw_request(method, clean, params=params, content=body or None, headers=headers)
    except MallError as exc:
        logger.warning("代理失败：%s %s → %s", method, clean, exc)
        return JSONResponse(status_code=502, content={"detail": {"message": f"商城服务不可用：{exc}"}})

    logger.debug("代理 %s %s → HTTP %s", method, clean, upstream.status_code)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        # 直接沿用上游的 Content-Type（图片要保 content-type，JSON 要保 charset）
        headers={"Content-Type": upstream.headers.get("content-type", "application/json")},
    )
