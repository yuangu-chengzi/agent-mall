"""会话记忆测试：图 + Redis checkpointer 打通。

对应 P3 的验收点："多轮对话能记住上下文" + "Redis 中可见会话状态"。
用 ``fakeredis`` 在进程内跑（决策 D7），不依赖本机 Redis 进程。
"""

import pytest
from fakeredis import aioredis
from langchain_core.messages import AIMessage

from app.graph.builder import build_graph
from app.graph.runner import resolve_thread_id, run_turn
from app.memory import RedisCheckpointSaver
from app.tools import LOCAL_TOOLS
from tests.fakes import ScriptedLLM


@pytest.fixture
async def saver():
    client = aioredis.FakeRedis(decode_responses=False)
    instance = RedisCheckpointSaver(client, prefix="test:mem:", ttl_seconds=600)
    yield instance
    await instance.aclose()


async def test_second_turn_sees_first_turn(saver) -> None:
    """关键验收：同一会话的第二轮，送给 LLM 的历史里必须有第一轮的问与答。"""
    llm = ScriptedLLM(
        [
            AIMessage(content="你好，我是 wayn 商城客服。"),
            AIMessage(content="你刚才问我在不在。"),
        ]
    )
    graph = build_graph(llm=llm, tools=LOCAL_TOOLS, checkpointer=saver)

    first = await run_turn(graph, session_id="u1", message="在吗", recursion_limit=8)
    second = await run_turn(graph, session_id="u1", message="我刚才问的什么？", recursion_limit=8)

    assert first.reply.startswith("你好")
    assert second.reply == "你刚才问我在不在。"

    # 第二轮请求里：SystemMessage + [H1, A1, H2]
    second_turn = llm.calls[1]
    assert [type(message).__name__ for message in second_turn[1:]] == [
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
    ]
    assert second_turn[1].content == "在吗"


async def test_sessions_do_not_leak_into_each_other(saver) -> None:
    """会话隔离：不同 session_id 之间不能串上下文。"""
    llm = ScriptedLLM([AIMessage(content="A 的回答"), AIMessage(content="B 的回答")])
    graph = build_graph(llm=llm, tools=LOCAL_TOOLS, checkpointer=saver)

    await run_turn(graph, session_id="alice", message="我是 alice", recursion_limit=8)
    await run_turn(graph, session_id="bob", message="我是 bob", recursion_limit=8)

    bob_turn = llm.calls[1]
    assert len(bob_turn) == 2  # 只有 SystemMessage + 本轮输入
    assert bob_turn[1].content == "我是 bob"


async def test_session_state_is_visible_in_redis(saver) -> None:
    """Redis 里能看到会话状态（会话概览接口的数据来源）。"""
    llm = ScriptedLLM([AIMessage(content="好的，已为你记录。")])
    graph = build_graph(llm=llm, tools=LOCAL_TOOLS, checkpointer=saver)

    await run_turn(graph, session_id="u9", message="你好", recursion_limit=8)

    summary = await saver.aget_thread_summary(resolve_thread_id("u9", None))

    assert summary is not None
    assert summary["thread_id"] == resolve_thread_id("u9", None)  # 身份派生的线程键
    assert summary["message_count"] == 2  # HumanMessage + AIMessage
    assert summary["pending_writes"] == 0


async def test_tool_calls_are_also_remembered(saver) -> None:
    """工具调用轮次也要进历史（否则第二轮模型不知道自己查过什么）。"""
    from tests.fakes import tool_call

    llm = ScriptedLLM(
        [
            tool_call("calculator", {"expression": "1 + 1"}),
            AIMessage(content="等于 2。"),
            AIMessage(content="你刚才算了 1+1。"),
        ]
    )
    graph = build_graph(llm=llm, tools=LOCAL_TOOLS, checkpointer=saver)

    await run_turn(graph, session_id="u10", message="1+1 等于几", recursion_limit=8)
    await run_turn(graph, session_id="u10", message="我刚才让你算什么", recursion_limit=8)

    # 第二轮的历史里应包含第一轮的：H → AI(tool_call) → ToolMessage → AIMessage → H
    second_turn = llm.calls[-1]
    kinds = [type(message).__name__ for message in second_turn[1:]]
    assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage", "HumanMessage"]

    summary = await saver.aget_thread_summary(resolve_thread_id("u10", None))
    # 两轮累计 6 条：H1、AI(工具调用)、ToolMessage、AI(答复)、H2、AI(第二轮答复)
    assert summary is not None and summary["message_count"] == 6


async def test_same_session_id_with_different_tokens_is_isolated(saver) -> None:
    """A18 修复验证：会话归属由 token 决定，换 token 拿同一个 session_id 读不到别人的历史。

    修复前实测过：用户 B 带自己的 token、用用户 A 的 session_id 继续聊，
    LLM 能看到 A 说过的全部内容（包括手机号这种个人信息）。
    """
    llm = ScriptedLLM([AIMessage(content="已记录"), AIMessage(content="（第二轮）")])
    graph = build_graph(llm=llm, tools=LOCAL_TOOLS, checkpointer=saver)

    await run_turn(
        graph, session_id="web-abc", message="我的手机号是 13800000001", token="token-A", recursion_limit=8
    )
    await run_turn(graph, session_id="web-abc", message="继续", token="token-B", recursion_limit=8)

    assert [message.content for message in llm.calls[1][1:]] == ["继续"]


async def test_session_summary_is_scoped_to_the_caller(saver) -> None:
    """会话概览也按身份派生：同一个 session_id，别人的 token 查不到（返回 None）。"""
    llm = ScriptedLLM([AIMessage(content="好的")])
    graph = build_graph(llm=llm, tools=LOCAL_TOOLS, checkpointer=saver)

    await run_turn(graph, session_id="s", message="你好", token="token-A", recursion_limit=8)

    assert await saver.aget_thread_summary(resolve_thread_id("s", "token-A")) is not None
    assert await saver.aget_thread_summary(resolve_thread_id("s", "token-B")) is None
    assert await saver.aget_thread_summary(resolve_thread_id("s", None)) is None
