"""图装配。

结构就是 LangGraph 官方 ReAct 模式（``agent ⇄ tools`` 循环）::

    START → agent ─┬─ 有 tool_calls → tools ──┐
                   │                          │
                   └─ 无 tool_calls → END     └──→ 回到 agent（带着工具结果再决策）

两个关键点：

1. **分支判断用现成的 ``tools_condition``**：它检查最后一条 AIMessage 是否带 ``tool_calls``，
   带就路由到名为 ``tools`` 的节点，否则结束。自己写也不难，但没必要。
2. **循环步数不在图里写死**：由调用方在 ``config["recursion_limit"]`` 里给（见 ``runner.run_turn``），
   这样"最多让 LLM 折腾几轮"变成一个可配的运行参数，而不是图的结构。
"""

from typing import Any

from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.graph.nodes import make_agent_node
from app.graph.state import AgentState


def build_graph(
    *, llm: Any, tools: list[Any], checkpointer: Any = None, history_max_messages: int | None = None
) -> Any:
    """装配并编译客服对话图。

    :param llm: 聊天模型（不可为 None；未配置 LLM 的场景由 runner 直接返回降级话术）
    :param tools: 工具集合（至少一个）
    :param checkpointer: 会话持久化（P1 传 None；P3 接 Redis checkpointer）
    :param history_max_messages: 最多把多少条历史消息发给模型（None = 用默认值）
    """
    if llm is None:
        raise ValueError("llm 不能为空：未配置 LLM 时不应装配图，请走降级话术")
    if not tools:
        raise ValueError("至少要有一个工具，否则不应使用工具调用图")

    builder = StateGraph(AgentState)
    node_kwargs = {} if history_max_messages is None else {"history_max_messages": history_max_messages}
    builder.add_node("agent", make_agent_node(llm, tools, **node_kwargs))
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer)
