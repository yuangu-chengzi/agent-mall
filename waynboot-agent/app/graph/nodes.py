"""图节点：Agent 循环里的「决策」环节。

工具节点（执行 tool_calls）直接用 LangGraph 现成的 ``ToolNode``，不自己写——
它已经处理了参数校验、并行调用、错误包装这些琐事。
"""

from collections.abc import Callable
from typing import Any

from langchain_core.messages import AnyMessage, SystemMessage, ToolMessage

from app.graph.prompts import SYSTEM_PROMPT
from app.graph.state import AgentState

#: 默认最多把多少条历史消息发给 LLM（0 = 不限）。超出的用"已省略"提示收口。
#: 为什么需要它：每轮都把**全量历史**发给模型，token 成本随轮数线性涨，
#: 聊到几十轮会直接撞上下文上限报错——这是"能跑"和"能不炸"之间的差别。
DEFAULT_HISTORY_MAX_MESSAGES = 24

#: 历史被截断时追加到系统提示词里的说明（让模型知道"你没看到全部"，而不是以为用户只说了这些）
OMITTED_HINT = "\n\n[提示] 更早的 {count} 条对话已省略（超出上下文预算）。如需更早的信息，请直接问用户。"


def window_history(messages: list[AnyMessage], limit: int) -> tuple[list[AnyMessage], int]:
    """截取最近 ``limit`` 条消息，并保证工具调用配对完整。

    :return: ``(窗口内的消息, 被省略的条数)``

    工具配对为什么必须保住：``ToolMessage`` 是某个 ``AIMessage(tool_calls)`` 的执行结果，
    从中间切开时开头可能留下"孤儿 ToolMessage"，部分模型会直接报错。
    """
    if limit <= 0 or len(messages) <= limit:
        return list(messages), 0
    kept = list(messages[-limit:])
    while kept and isinstance(kept[0], ToolMessage):
        kept.pop(0)
    return kept, len(messages) - len(kept)


def make_agent_node(
    llm: Any,
    tools: list[Any],
    system_prompt: str = SYSTEM_PROMPT,
    history_max_messages: int = DEFAULT_HISTORY_MAX_MESSAGES,
) -> Callable[[AgentState], Any]:
    """构造 agent 节点：让 LLM 读对话历史，产出「最终回答」或「工具调用」。

    :param llm: 聊天模型（需支持 ``bind_tools``；测试里传入假 LLM 即可）
    :param tools: 可调用工具集合，会随请求一起发给模型
    :param system_prompt: 系统提示词
    :param history_max_messages: 最多把多少条历史消息发给模型（0 = 不限）
    """
    llm_with_tools = llm.bind_tools(tools)

    async def agent_node(state: AgentState) -> dict[str, Any]:
        # 系统提示词每轮现拼、不进状态：既避免它被写进会话存储，也方便随时改话术
        history, omitted = window_history(state["messages"], history_max_messages)
        content = system_prompt + (OMITTED_HINT.format(count=omitted) if omitted else "")
        messages: list[AnyMessage] = [SystemMessage(content=content), *history]
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    return agent_node
