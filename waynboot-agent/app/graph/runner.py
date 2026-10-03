"""单轮对话执行：把图跑起来，并把失败翻译成用户能听懂的话术。

对应决策 D8「不静默失败」：LLM 挂了、超步数、没产出答复——三种情况都要**给用户一句话**，
而不是抛 500 让前端白屏。降级话术统一在这里，API 层与 SSE 层共用同一套。

**两种"非正常结束"要区分清楚**：

- ``degraded=True``：真的出问题了，reply 是兜底话术；
- ``pending_action`` 非空：图**故意**停在写操作的确认环节（``interrupt``），不是故障，
  前端应弹确认卡片，用户确认后调 ``/api/chat/confirm`` 恢复（见 ``resume_turn``）。
"""

import hashlib
import time
from dataclasses import dataclass, field, replace
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from app.graph.usage import TurnUsage, UsageCallback
from app.observability import get_logger
from app.tools.base import ESCALATE_TOOL_NAME

logger = get_logger(__name__)

#: LLM 未配置 / 调用失败时的兜底话术
FALLBACK_SERVICE_UNAVAILABLE = (
    "抱歉，客服系统有点忙不过来，请稍后再试；紧急问题可以回复「转人工」由人工客服处理。"
)
#: 超出步数上限（问题太绕、或工具反复失败）时的兜底话术
FALLBACK_TOO_COMPLEX = "抱歉，这个问题我反复查了几次还是没能确认，建议回复「转人工」让人工客服帮你看看。"
#: 图停在写操作确认环节时的兜底话术（正常情况下用工具在 interrupt 里给的 ask 文案）
FALLBACK_CONFIRM = "这一步需要你确认一下。"


@dataclass(frozen=True)
class TurnResult:
    """一轮对话的结果。"""

    reply: str
    degraded: bool = False
    reason: str | None = None
    #: 本轮是否触发了转人工（前端可据此落工单/提示已接入人工）
    escalate: bool = False
    #: 不为空表示图停在写操作确认环节，前端应弹确认卡片（决策 D3）
    pending_action: dict[str, Any] | None = field(default=None)
    #: 本轮 LLM 用量（模型不返回用量时为全 0）
    usage: TurnUsage = field(default_factory=TurnUsage)


async def run_turn(
    graph: Any,
    *,
    session_id: str,
    message: str,
    token: str | None = None,
    recursion_limit: int = 16,
) -> TurnResult:
    """执行一轮对话。

    :param graph: 已编译的图；为 ``None`` 表示 LLM 未配置 → 直接降级
    :param session_id: 会话 ID（同时作为 checkpointer 的 ``thread_id``）
    :param message: 用户这一轮的输入
    :param token: 用户的 Sa-Token，透传给工具层调用 mall（不写入会话状态）
    :param recursion_limit: 循环步数上限（一轮"决策+执行工具"消耗 2 步）
    """
    if graph is None:
        logger.warning("LLM 未配置，返回降级话术")
        return TurnResult(
            FALLBACK_SERVICE_UNAVAILABLE, degraded=True, reason="llm_unavailable", escalate=True
        )

    return await _invoke(
        graph,
        {"messages": [HumanMessage(content=message)]},
        _config(session_id, token, recursion_limit),
        recursion_limit=recursion_limit,
    )


async def resume_turn(
    graph: Any,
    *,
    session_id: str,
    approved: bool,
    note: str | None = None,
    token: str | None = None,
    recursion_limit: int = 16,
) -> TurnResult:
    """恢复一轮被 ``interrupt`` 暂停的对话（用户在确认卡片上点了确认/取消）。

    ⚠️ 恢复时**工具函数会从头重跑**，所以这里必须再带一次 token —— 前面几步的所有权与状态校验
    会重新执行一遍（这是好事：确认期间订单状态可能已经变了）。
    """
    if graph is None:
        logger.warning("LLM 未配置，无法恢复对话")
        return TurnResult(
            FALLBACK_SERVICE_UNAVAILABLE, degraded=True, reason="llm_unavailable", escalate=True
        )

    logger.info("恢复被中断的对话：session=%s approved=%s", session_id, approved)
    return await _invoke(
        graph,
        Command(resume={"approved": approved, "note": note}),
        _config(session_id, token, recursion_limit),
        recursion_limit=recursion_limit,
    )


async def _invoke(graph: Any, payload: Any, config: dict[str, Any], *, recursion_limit: int) -> TurnResult:
    """跑一次图（新输入或恢复），把结果或失败统一翻译成 :class:`TurnResult`。"""
    usage_callback = UsageCallback()
    config = {**config, "callbacks": [*config.get("callbacks", []), usage_callback]}
    started = time.perf_counter()

    try:
        state = await graph.ainvoke(payload, config)
    except GraphRecursionError:
        # 被步数上限掐断不是崩溃，而是"该收手了"：明确告知并给出转人工出口
        logger.warning("对话超出步数上限 recursion_limit=%s，降级并引导转人工", recursion_limit)
        result = TurnResult(FALLBACK_TOO_COMPLEX, degraded=True, reason="too_many_steps", escalate=True)
    except Exception as exc:  # noqa: BLE001 —— 见下方说明
        # 这里刻意兜住所有异常：LLM 超时/网络故障/工具实现 bug 对用户而言都是"客服没答上来"，
        # 不能让它们变成 500 白屏。细节靠 logger.exception 落到日志里。
        logger.exception("对话执行失败：%s", exc)
        result = TurnResult(FALLBACK_SERVICE_UNAVAILABLE, degraded=True, reason="error", escalate=True)
    else:
        result = result_from_state(state)
    finally:
        logger.info(
            "本轮用量：llm_calls=%s input_tokens=%s output_tokens=%s 耗时=%.0fms",
            usage_callback.usage.calls,
            usage_callback.usage.input_tokens,
            usage_callback.usage.output_tokens,
            (time.perf_counter() - started) * 1000,
        )

    return replace(result, usage=usage_callback.usage)


def result_from_state(state: dict[str, Any]) -> TurnResult:
    """把图的最终状态翻译成 :class:`TurnResult`（一次性接口与流式接口共用）。"""
    messages: list[AnyMessage] = state.get("messages", [])
    escalate = detect_escalation(messages)

    interrupts = state.get("__interrupt__") or ()
    if interrupts:
        payload = getattr(interrupts[0], "value", None)
        pending = payload if isinstance(payload, dict) else {"raw": str(payload)}
        logger.info("图停在写操作确认：action=%s", pending.get("action"))
        return TurnResult(
            reply=pending.get("ask") or FALLBACK_CONFIRM,
            escalate=escalate,
            pending_action=pending,
        )

    reply = extract_reply(messages)
    if not reply:
        # 图跑完了却没有可展示的答复：通常是最后一步停在工具调用上
        logger.warning("图执行结束但没有产出答复，降级")
        return TurnResult(FALLBACK_TOO_COMPLEX, degraded=True, reason="no_reply", escalate=True)
    return TurnResult(reply, escalate=escalate)


def detect_escalation(messages: list[AnyMessage]) -> bool:
    """本轮是否调用了转人工工具。"""
    return any(
        isinstance(message, ToolMessage) and message.name == ESCALATE_TOOL_NAME for message in messages
    )


def extract_reply(messages: list[AnyMessage]) -> str:
    """取最后一条有文本内容的 AI 消息作为答复。"""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = text_of(message.content)
            if text:
                return text
    return ""


def text_of(content: Any) -> str:
    """兼容 ``content`` 的两种形态：纯字符串、或分块列表（不同模型/网关格式不一）。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return "".join(parts).strip()
    return ""


def resolve_thread_id(session_id: str, token: str | None) -> str:
    """把「调用方身份 + 会话 ID」派生成 checkpointer 的 thread_id。

    **为什么不能直接拿 session_id 当 thread_id**：它是前端生成的"会话名字"，只是标识符、
    不是凭证。如果直接用它当线程键，任何人只要猜到别人的 session_id，带上自己的 token
    就能读到别人的整段对话历史（实测过：换 token 不换 session_id，历史照读）。
    所以这里用身份做前缀：**同一个 session_id，不同的人属于不同线程**。

    匿名（没有 token）时退化为纯 session_id——那种会话碰不到个人数据（工具都要登录），
    且 id 本身不可枚举；真有需要可以在网关层强制登录。

    :param session_id: 前端生成的会话标识
    :param token: 调用方的 Sa-Token（可为空）
    """
    if not token:
        return f"anon:{session_id}"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return f"{digest}:{session_id}"


def _config(session_id: str, token: str | None, recursion_limit: int) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": resolve_thread_id(session_id, token), "token": token},
        "recursion_limit": recursion_limit,
    }
