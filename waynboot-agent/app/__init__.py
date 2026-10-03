"""waynboot-agent：waynboot-mall 的 AI 客服 Agent 服务。

分层（依赖方向自上而下，禁止反向依赖）：

    main.py         FastAPI 入口层（HTTP/SSE、鉴权透传、限流）
    graph/          LangGraph 编排层（state / nodes / builder）
    tools/          工具层（LangChain @tool；只读 + 写操作）
    mall/           mall HTTP 客户端（R 信封解包、token 透传）
    memory/         会话记忆（Redis checkpointer）
    config.py       配置绑定
    observability.py 日志与请求追踪
"""

__version__ = "0.1.0"
