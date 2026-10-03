"""SSE 流式输出：把图的执行过程翻译成一串事件。

事件协议（前端按 ``event`` 分发；每个事件的 ``data`` 都是一行 JSON）::

    event: token   data: {"text": "你好"}                     模型逐字输出的一段文本
    event: tool    data: {"phase":"start","name":"my_orders"} 开始调用某工具
    event: tool    data: {"phase":"end","name":"my_orders"}   工具执行结束
    event: done    data: {"reply":"...","degraded":false,"reason":null}

**降级策略与一次性接口完全一致**（决策 D8）：超步数、异常、未配置 LLM 都通过 ``done`` 事件
带上兜底话术与 ``degraded`` 标记，而不是让连接莫名其妙断开。
"""

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from app.graph.runner import (
    FALLBACK_CONFIRM,
    FALLBACK_SERVICE_UNAVAILABLE,
    FALLBACK_TOO_COMPLEX,
    detect_escalation,
    extract_reply,
    resolve_thread_id,
    text_of,
)
from app.graph.usage import TurnUsage, UsageCallback
from app.observability import get_logger

logger = get_logger(__name__)

#: 流式模式：messages 给"逐字文本"，updates 给"哪个节点跑了"（用来报告工具调用）
STREAM_MODES = ["messages", "updates"]


async def stream_turn(
    graph: Any,
    *,
    session_id: str,
    message: str,
    token: str | None = None,
    recursion_limit: int = 16,
) -> AsyncIterator[dict[str, Any]]:
    """执行一轮对话并逐段产出事件（供 SSE 层转成 ``text/event-stream``）。"""
    if graph is None:
        logger.warning("LLM 未配置，流式接口返回降级话术")
        yield _done(FALLBACK_SERVICE_UNAVAILABLE, degraded=True, reason="llm_unavailable", escalate=True)
        return

    usage_callback = UsageCallback()
    config: dict[str, Any] = {
        "configurable": {"thread_id": resolve_thread_id(session_id, token), "token": token},
        "recursion_limit": recursion_limit,
        "callbacks": [usage_callback],
    }
    payload = {"messages": [HumanMessage(content=message)]}

    try:
        async for mode, chunk in graph.astream(payload, config, stream_mode=STREAM_MODES):
            if mode == "messages":
                async for event in _messages_events(chunk):
                    yield event
            elif mode == "updates":
                async for event in _updates_events(chunk):
                    yield event
    except GraphRecursionError:
        logger.warning("流式对话超出步数上限 recursion_limit=%s", recursion_limit)
        yield _done(FALLBACK_TOO_COMPLEX, degraded=True, reason="too_many_steps", escalate=True)
        return
    except Exception as exc:  # noqa: BLE001 —— 与一次性接口同样的兜底理由
        logger.exception("流式对话执行失败：%s", exc)
        yield _done(FALLBACK_SERVICE_UNAVAILABLE, degraded=True, reason="error", escalate=True)
        return

    yield await _final_event(graph, config, usage_callback.usage)


async def _final_event(graph: Any, config: dict[str, Any], usage: TurnUsage) -> dict[str, Any]:
    """流结束后从最终状态构造 ``done`` 事件。

    比"把逐字片段拼起来"可靠：不受分块边界影响，而且能顺手拿到两个关键信息——
    ① 是否停在写操作确认（``interrupt``）② 是否触发了转人工。
    """
    try:
        state = await graph.aget_state(config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("读取最终状态失败：%s", exc)
        return _done(FALLBACK_TOO_COMPLEX, degraded=True, reason="no_reply", escalate=True, usage=usage)

    values = (state.values if state else {}) or {}
    messages = values.get("messages", [])
    escalate = detect_escalation(messages)

    interrupts = getattr(state, "interrupts", ()) or ()
    if interrupts:
        payload = getattr(interrupts[0], "value", None)
        pending = payload if isinstance(payload, dict) else {"raw": str(payload)}
        logger.info("流式对话停在写操作确认：action=%s", pending.get("action"))
        return _done(
            pending.get("ask") or FALLBACK_CONFIRM, escalate=escalate, pending_action=pending, usage=usage
        )

    reply = extract_reply(messages)
    if not reply:
        return _done(FALLBACK_TOO_COMPLEX, degraded=True, reason="no_reply", escalate=True, usage=usage)
    return _done(reply, escalate=escalate, usage=usage)


async def _messages_events(chunk: Any) -> AsyncIterator[dict[str, Any]]:
    """``stream_mode="messages"`` 的载荷是 ``(消息, 元数据)``。

    两个过滤条件都不能少：

    1. **只认 agent 节点的产出**：工具节点的 ToolMessage 也会出现在这个模式里，
       不过滤的话用户会在气泡里看到工具返回的原始文本（例如 ``19.9 * 3 = 59.7``）。
    2. **同时接受 AIMessage 与 AIMessageChunk**：真实模型逐字输出时是 ``AIMessageChunk``，
       而"一次性返回"的模型（以及测试里的假模型）给的是整条 ``AIMessage``——只认 Chunk 会一个字都流不出来。
    """
    message, metadata = chunk
    if metadata.get("langgraph_node") != "agent":
        return
    if not isinstance(message, (AIMessage, AIMessageChunk)):
        return
    text = text_of(message.content)
    if text:
        yield {"event": "token", "data": {"text": text}}


async def _updates_events(chunk: Any) -> AsyncIterator[dict[str, Any]]:
    """``stream_mode="updates"`` 的载荷是 ``{节点名: 节点输出}``，用来报告工具调用。"""
    if not isinstance(chunk, dict):
        return
    for node, output in chunk.items():
        if node != "tools" or not isinstance(output, dict):
            continue
        for message in output.get("messages") or []:
            if isinstance(message, ToolMessage):
                name = message.name or "tool"
                yield {"event": "tool", "data": {"phase": "start", "name": name}}
                yield {"event": "tool", "data": {"phase": "end", "name": name}}


def _done(
    reply: str,
    *,
    degraded: bool = False,
    reason: str | None = None,
    escalate: bool = False,
    pending_action: dict[str, Any] | None = None,
    usage: TurnUsage | None = None,
) -> dict[str, Any]:
    usage = usage or TurnUsage()
    return {
        "event": "done",
        "data": {
            "reply": reply,
            "degraded": degraded,
            "reason": reason,
            "escalate": escalate,
            "pending_action": pending_action,
            # 本轮 token 用量：前端可展示，后端据此累计成本（见 app/memory/usage.py）
            "usage": {
                "llm_calls": usage.calls,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
        },
    }
