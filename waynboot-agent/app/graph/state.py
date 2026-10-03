"""图状态定义。

**刻意保持最小**：状态会被 checkpointer 持久化（P3 落到 Redis），因此**只放对话本身**。
凭证（Sa-Token）与租户信息走 LangGraph 的运行配置 ``config["configurable"]``，
不进入状态——避免密钥被写进会话存储。
"""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """客服 Agent 的对话状态。

    ``add_messages`` 是 reducer：节点返回的 ``{"messages": [...]}`` 会**追加**到已有列表，
    而不是覆盖（这是 LangGraph 里最容易踩的坑之一）。
    """

    messages: Annotated[list[AnyMessage], add_messages]

    # P4 会用到：是否需要转人工（由节点置位，API 层据此产生 escalation 事件）
    escalate: bool
