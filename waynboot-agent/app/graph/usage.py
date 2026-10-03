"""本轮 token 用量采集。

**为什么用回调而不是读最后一条 AI 消息**：一轮对话里 LLM 可能被调用多次
（调一次工具就多一轮），只看最后一条会严重低估。回调能捕获**每一次**调用。

模型不返回用量时（例如测试里的假模型）累计值为 0——不影响对话，只是没数据。
"""

from dataclasses import dataclass

from langchain_core.callbacks import AsyncCallbackHandler


@dataclass(frozen=True)
class TurnUsage:
    """一轮对话的累计用量。"""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class UsageCallback(AsyncCallbackHandler):
    """累计一次图执行里所有 LLM 调用的 token 用量。"""

    def __init__(self) -> None:
        self.usage = TurnUsage()

    async def on_llm_end(self, response, **kwargs) -> None:  # noqa: ANN001 —— 回调签名由 LangChain 定义
        """每次 LLM 调用结束都会走到这里。"""
        calls = self.usage.calls + 1
        input_tokens = self.usage.input_tokens
        output_tokens = self.usage.output_tokens
        for generation_list in getattr(response, "generations", []) or []:
            for generation in generation_list:
                message = getattr(generation, "message", None)
                metadata = getattr(message, "usage_metadata", None) or {}
                input_tokens += int(metadata.get("input_tokens") or 0)
                output_tokens += int(metadata.get("output_tokens") or 0)
        self.usage = TurnUsage(calls=calls, input_tokens=input_tokens, output_tokens=output_tokens)
