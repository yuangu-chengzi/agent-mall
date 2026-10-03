"""图结构与循环控制测试。

用假 LLM + **真工具**（faq/calculator）：这样测的是真实的图装配与 ToolNode 执行路径，
而不是"我 mock 出来的一条假链路"。
"""

import pytest
from fakeredis import aioredis
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.graph.builder import build_graph
from app.graph.nodes import window_history
from app.graph.runner import run_turn
from app.memory import RedisCheckpointSaver
from app.tools import LOCAL_TOOLS
from tests.fakes import ScriptedLLM, tool_call


async def test_answers_directly_without_tools() -> None:
    """LLM 不需要工具时，图一步结束。"""
    llm = ScriptedLLM([AIMessage(content="你好，我是 wayn 商城客服。")])
    graph = build_graph(llm=llm, tools=LOCAL_TOOLS)

    result = await run_turn(graph, session_id="s1", message="在吗", recursion_limit=8)

    assert result.reply == "你好，我是 wayn 商城客服。"
    assert result.degraded is False
    assert result.escalate is False  # 正常回答不该触发转人工
    assert result.pending_action is None
    assert [tool.name for tool in llm.bound_tools] == ["faq", "calculator"]


async def test_degraded_results_always_carry_an_escalation_exit() -> None:
    """决策 D8：任何降级都必须同时给出"转人工"出口，前端据此接入人工客服。

    三种降级都要标记：LLM 未配置 / LLM 调用失败 / 超出步数上限。
    """
    failed = await run_turn(
        build_graph(llm=ScriptedLLM([RuntimeError("boom")]), tools=LOCAL_TOOLS),
        session_id="s8",
        message="你好",
        recursion_limit=8,
    )
    assert (failed.degraded, failed.escalate, failed.reason) == (True, True, "error")

    unconfigured = await run_turn(None, session_id="s9", message="你好", recursion_limit=8)
    assert (unconfigured.degraded, unconfigured.escalate) == (True, True)

    stuck = ScriptedLLM([tool_call("faq", {"question": "x"}, call_id=f"c{i}") for i in range(30)])
    over_limit = await run_turn(
        build_graph(llm=stuck, tools=LOCAL_TOOLS), session_id="s10", message="一直问", recursion_limit=4
    )
    assert (over_limit.degraded, over_limit.escalate, over_limit.reason) == (True, True, "too_many_steps")


async def test_multi_step_loop_executes_tool_then_answers() -> None:
    """关键路径：LLM 要工具 → ToolNode 真的执行 → 结果回灌 → LLM 给出最终答复。"""
    llm = ScriptedLLM(
        [
            tool_call("calculator", {"expression": "39.9 * 2"}),
            AIMessage(content="两件一共 79.8 元。"),
        ]
    )
    graph = build_graph(llm=llm, tools=LOCAL_TOOLS)

    result = await run_turn(graph, session_id="s2", message="两件 39.9 的多少钱", recursion_limit=8)

    assert result.reply == "两件一共 79.8 元。"
    assert result.degraded is False
    # 第二次问 LLM 时，历史里必须已经带上工具的**真实执行结果**
    tool_messages = [m for m in llm.calls[1] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert "79.8" in str(tool_messages[0].content)


async def test_tool_loop_keeps_full_history_in_state() -> None:
    """reducer 的存在性验证：一轮里「用户问题 → 工具调用 → 工具结果」必须都在状态里。

    这条断言专门防「把 state.py 的 Annotated[..., add_messages] 去掉」这类回归：
    没有 reducer 时每个节点会**覆盖** messages 通道，历史只剩最后一条，
    而只检查"ToolMessage 在不在"的断言抓不到（学习者做实验时发现，见笔记）。
    """
    llm = ScriptedLLM([tool_call("calculator", {"expression": "1 + 1"}), AIMessage(content="等于 2。")])
    graph = build_graph(llm=llm, tools=LOCAL_TOOLS)

    await run_turn(graph, session_id="s11", message="1+1 等于几", recursion_limit=8)

    second_call = llm.calls[1]
    assert [type(message).__name__ for message in second_call] == [
        "SystemMessage",
        "HumanMessage",
        "AIMessage",
        "ToolMessage",
    ]
    assert second_call[1].content == "1+1 等于几"  # 用户问题没被覆盖掉


async def test_system_prompt_is_prepended_on_every_round() -> None:
    """系统提示词每轮现拼（不进状态），两轮调用都应带上。"""
    llm = ScriptedLLM(
        [
            tool_call("faq", {"question": "怎么退货"}),
            AIMessage(content="可以这样退：……"),
        ]
    )
    graph = build_graph(llm=llm, tools=LOCAL_TOOLS)

    await run_turn(graph, session_id="s3", message="怎么退货", recursion_limit=8)

    assert len(llm.calls) == 2
    for call_messages in llm.calls:
        assert isinstance(call_messages[0], SystemMessage)


async def test_recursion_limit_terminates_loop_gracefully() -> None:
    """LLM 陷在工具循环里停不下来 → 被 recursion_limit 掐断 → 给话术，不抛异常。

    这是 P1 的验收点之一：**可控循环**。
    """
    llm = ScriptedLLM([tool_call("faq", {"question": "怎么退货"}, call_id=f"c{i}") for i in range(30)])
    graph = build_graph(llm=llm, tools=LOCAL_TOOLS)

    result = await run_turn(graph, session_id="s4", message="一直问", recursion_limit=4)

    assert result.degraded is True
    assert result.reason == "too_many_steps"
    assert "转人工" in result.reply


async def test_llm_failure_degrades_instead_of_raising() -> None:
    """LLM 调用失败（超时/网关错误）→ 降级话术，绝不冒泡成 500。"""
    llm = ScriptedLLM([RuntimeError("deepseek 502 bad gateway")])
    graph = build_graph(llm=llm, tools=LOCAL_TOOLS)

    result = await run_turn(graph, session_id="s5", message="你好", recursion_limit=8)

    assert result.degraded is True
    assert result.reason == "error"
    assert "转人工" in result.reply


async def test_missing_graph_degrades_without_touching_llm() -> None:
    """未配置 LLM（图未装配）时直接降级，不需要走到图里。"""
    result = await run_turn(None, session_id="s6", message="你好", recursion_limit=8)

    assert result.degraded is True
    assert result.reason == "llm_unavailable"


async def test_token_is_reachable_from_tools_via_config() -> None:
    """鉴权透传（决策 D2）：工具能从运行配置里取到用户的 Sa-Token（P2 的 mall 工具全靠它）。"""
    seen: dict[str, str | None] = {}

    @tool
    def spy(text: str, config: RunnableConfig) -> str:
        """记录运行配置里的 token。"""
        seen["token"] = config["configurable"].get("token")
        return f"收到 {text}"

    llm = ScriptedLLM([tool_call("spy", {"text": "x"}), AIMessage(content="done")])
    graph = build_graph(llm=llm, tools=[spy])

    await run_turn(graph, session_id="s7", message="hi", token="tok-123", recursion_limit=8)

    assert seen["token"] == "tok-123"


def test_build_graph_rejects_incomplete_inputs() -> None:
    """装配前的前置校验：没有 LLM 或没有工具都不该装配图。"""
    with pytest.raises(ValueError):
        build_graph(llm=None, tools=LOCAL_TOOLS)
    with pytest.raises(ValueError):
        build_graph(llm=ScriptedLLM([]), tools=[])


async def test_long_history_is_windowed_with_a_hint() -> None:
    """长对话只把最近 N 条发给模型，且明确告知"更早的已省略"。

    不这么做的话：每轮把全量历史发给模型，token 成本随轮数线性涨，几十轮后撞上下文上限报错。
    """
    saver = RedisCheckpointSaver(aioredis.FakeRedis(decode_responses=False), prefix="test:win:")
    llm = ScriptedLLM([AIMessage(content="一"), AIMessage(content="二"), AIMessage(content="三")])
    # 注意：窗口是"从状态里取最近 N 条"，所以必须接 checkpointer —— 没有存档就没有历史可裁
    graph = build_graph(llm=llm, tools=LOCAL_TOOLS, checkpointer=saver, history_max_messages=3)

    try:
        for index in range(3):
            await run_turn(graph, session_id="w", message=f"第{index + 1}句", recursion_limit=8)
    finally:
        await saver.aclose()

    # 第三轮时状态是 [H1 A1 H2 A2 H3]（5 条），窗口取最近 3 条 → [H2 A2 H3]
    sent = llm.calls[-1]
    assert len(sent) == 4  # 系统提示 + 窗口内 3 条
    assert [type(message).__name__ for message in sent[1:]] == ["HumanMessage", "AIMessage", "HumanMessage"]
    assert "更早的 2 条对话已省略" in sent[0].content


def test_window_drops_orphan_tool_messages() -> None:
    """从中间切开时不能留下"孤儿 ToolMessage"（没有对应的 AIMessage(tool_calls)），部分模型会报错。"""
    messages = [
        HumanMessage(content="a"),
        AIMessage(content="b"),
        ToolMessage(content="工具结果", name="faq", tool_call_id="c1"),
        AIMessage(content="d"),
        HumanMessage(content="e"),
    ]

    kept, omitted = window_history(messages, 3)

    assert [type(message).__name__ for message in kept] == ["AIMessage", "HumanMessage"]
    assert omitted == 3


def test_window_disabled_when_limit_is_zero() -> None:
    messages = [HumanMessage(content=str(index)) for index in range(10)]

    kept, omitted = window_history(messages, 0)

    assert len(kept) == 10
    assert omitted == 0
