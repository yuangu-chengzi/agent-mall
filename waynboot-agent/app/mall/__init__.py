"""mall HTTP 客户端层：R 信封解包、鉴权透传、错误归一。"""

from app.mall.client import MallClient
from app.mall.errors import MallAuthError, MallBusinessError, MallError, MallUnavailableError

__all__ = ["MallClient", "MallError", "MallBusinessError", "MallAuthError", "MallUnavailableError"]
