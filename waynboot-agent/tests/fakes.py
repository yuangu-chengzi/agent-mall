"""测试替身（决策 D7：测试不依赖真实 LLM）。

``ScriptedLLM`` 只实现图节点真正调用的两个方法，所以不必继承 ``BaseChatModel``，
也不会有任何网络行为——这样"图的结构与循环控制"能被确定性地测出来。
"""

from typing import Any

from langchain_core.messages import AIMessage


class ScriptedLLM:
    """按脚本依次返回消息的假 LLM。

    脚本元素可以是 ``AIMessage``，也可以是异常实例（用来模拟 LLM 调用失败）。
    """

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.bound_tools: list[Any] = []
        self.calls: list[list[Any]] = []

    def bind_tools(self, tools: list[Any]) -> "ScriptedLLM":
        """记录被绑定的工具，返回自身（模拟 LLM 的工具绑定行为）。"""
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages: list[Any], config: Any = None) -> Any:
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("ScriptedLLM 脚本已用尽：图执行的轮次比预期多（可能死循环）")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> AIMessage:
    """构造一条「要求调用工具」的 AI 消息（内容为空，只有 tool_calls）。"""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )
