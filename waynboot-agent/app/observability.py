"""日志与请求追踪。

设计要点：Agent 服务每次对话都会扇出若干次对 mall 的 HTTP 调用，
排查问题时必须能把「一次用户请求」产生的所有日志串起来，
因此用 :data:`request_id_var` 这个 contextvar 贯穿全链路，
日志格式里带上 ``[req=...]``，并在响应头回写 ``X-Request-Id``。
"""

import logging
import uuid
from contextvars import ContextVar

# 当前请求 ID；由 main.py 的中间件写入，业务代码只读。
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_LOG_FORMAT = "%(asctime)s %(levelname)-5s [%(name)s] [req=%(request_id)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _RequestIdFilter(logging.Filter):
    """把 contextvar 中的 request_id 注入每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging(level: str = "INFO") -> None:
    """初始化根 logger。重复调用安全（只配置一次句柄）。"""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn 自带 access 日志与我们的格式重复，交给根 logger 统一输出
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True


def get_logger(name: str) -> logging.Logger:
    """获取业务 logger。"""
    return logging.getLogger(name)


def new_request_id() -> str:
    """生成一个请求 ID（短随机串，足够单机排查用）。"""
    return uuid.uuid4().hex[:12]
