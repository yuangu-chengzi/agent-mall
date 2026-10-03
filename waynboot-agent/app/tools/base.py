"""工具层公共设施：取 token、错误归一、输出裁剪。

六条约定（所有 mall 工具遵守）：

1. **工具只返回字符串**：失败不抛异常，而是返回一句可读的说明，交给 LLM 转述给用户。
   抛异常会让整个图跑挂，而"工具失败"本身是需要被解释的信息。
2. **凭证从运行配置取**（``config["configurable"]["token"]``），**不进会话状态**——
   避免密钥被写进 Redis 会话存储。
3. **只输出 LLM 回答问题真正需要的字段**：mall 的响应里有富文本详情、时间戳等噪声，
   全塞进上下文会挤爆窗口还降低回答质量。
4. **金额统一格式化**为两位小数，**缺字段一律给"未知"**
   （mall 的 `non_null` 会把 null 字段整个省略，见 API_CONTRACT §1.1）。
5. **不猜数据**：查不到就明确说查不到。
6. **越权防护**：涉及个人数据的工具先做所有权校验（见 orders.py）。
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.mall.errors import MallAuthError, MallBusinessError, MallError, MallUnavailableError

#: 未登录时的统一返回（LLM 会据此提示用户登录）
NO_TOKEN_MESSAGE = "（系统提示：当前用户未登录，查不到个人数据。请礼貌地请用户先登录后再试。）"

#: mall 服务不可用时的统一返回
UNAVAILABLE_MESSAGE = "（系统提示：商城服务暂时不可用。请告诉用户稍后再试，或建议转人工。）"


#: 转人工工具名：runner 靠"消息里有没有它"判断本轮是否触发了转人工
ESCALATE_TOOL_NAME = "escalate_to_human"


def token_of(config: RunnableConfig | None) -> str | None:
    """从运行配置里取用户的 Sa-Token（不写入会话状态）。"""
    if not config:
        return None
    configurable = config.get("configurable") or {}
    return configurable.get("token")


def thread_of(config: RunnableConfig | None) -> str | None:
    """从运行配置里取会话 ID（同时是 checkpointer 的 thread_id 与幂等键的一部分）。"""
    if not config:
        return None
    configurable = config.get("configurable") or {}
    return configurable.get("thread_id")


def describe_error(exc: MallError) -> str:
    """把 mall 异常翻译成给 LLM 的说明（括号内是系统提示，不是给用户看的原文）。"""
    if isinstance(exc, MallAuthError):
        return "（系统提示：用户登录状态已失效。请请用户重新登录后再试。）"
    if isinstance(exc, MallBusinessError):
        return f"（系统提示：查询被商城拒绝，原因「{exc.msg}」。请据此向用户解释，不要编造其它原因。）"
    if isinstance(exc, MallUnavailableError):
        return UNAVAILABLE_MESSAGE
    return "（系统提示：查询失败，原因未知。请建议用户稍后再试或转人工。）"


def clip(value: Any, limit: int = 60) -> str:
    """截断长文本（简介、地址等），避免挤爆上下文。"""
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def money(value: Any) -> str:
    """金额格式化为两位小数；缺字段/非法值返回"未知"。"""
    if value is None or value == "":
        return "未知"
    try:
        return f"{Decimal(str(value)):.2f}"
    except (InvalidOperation, ValueError):
        return str(value)


def nz(value: Any, default: str = "未知") -> str:
    """空值兜底（mall 的 non_null 会让字段整个缺失）。"""
    if value is None or value == "":
        return default
    return str(value)
