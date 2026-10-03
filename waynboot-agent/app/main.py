"""FastAPI 入口层。

| 接口 | 用途 |
|---|---|
| ``GET /healthz`` | 存活探针（不碰任何外部依赖） |
| ``GET /readyz`` | 就绪探针（探测 mall 与 Redis） |
| ``POST /api/chat`` | 一轮对话，一次性返回 JSON（脚本/调试方便） |
| ``POST /api/chat/stream`` | 一轮对话，SSE 流式返回（前端用） |
| ``GET /api/sessions/{id}`` | 会话概览（从 Redis 读实际状态） |
| ``DELETE /api/sessions/{id}`` | 清空会话 |
| ``POST /api/login/code`` | 登录代理：转发"发送短信验证码"给 mall（避免浏览器跨域） |
| ``POST /api/login`` | 登录代理：拿 mall 签发的 Sa-Token 交给前端 |
| ``ANY /api/mall/{path}`` | 商城业务接口的反向代理（BFF，见 app/mall/proxy.py） |
| ``GET /`` | 商城 H5 页面（单页多视图，`#/xxx` 路由） |
| ``GET /chat``、``/ui/`` | AI 客服聊天页与静态资源 |
"""

import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import Settings, get_settings
from app.graph.builder import build_graph
from app.graph.llm import build_llm
from app.graph.runner import resolve_thread_id, resume_turn, run_turn
from app.graph.streaming import stream_turn
from app.mall.client import MallClient
from app.mall.errors import MallAuthError, MallError
from app.mall.proxy import forward
from app.memory import RedisCheckpointSaver
from app.memory.idempotency import IdempotencyStore
from app.memory.identity import caller_identity
from app.memory.ratelimit import RateLimiter
from app.memory.usage import UsageTracker
from app.observability import get_logger, new_request_id, request_id_var, setup_logging
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ConfirmRequest,
    LoginCodeRequest,
    LoginRequest,
    LoginResponse,
    SessionSummary,
)
from app.tools import build_all_tools

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """进程级资源：配置、日志、mall 客户端、工具集、会话记忆、幂等存储、对话图。"""
    settings: Settings = get_settings()
    setup_logging(settings.log_level)
    app.state.settings = settings
    app.state.mall = MallClient(settings.mall_base_url, timeout=settings.mall_timeout_seconds)
    app.state.saver = RedisCheckpointSaver.from_settings(settings)
    app.state.idempotency = IdempotencyStore.from_settings(settings)
    app.state.rate_limiter = RateLimiter.from_settings(settings)
    app.state.usage = UsageTracker.from_settings(settings)
    tools = build_all_tools(app.state.mall, app.state.idempotency)

    # 未配置 LLM Key 时图不装配：服务照常起来，对话接口返回降级话术（方便本地/CI 调试）
    llm = build_llm(settings)
    if llm is None:
        app.state.graph = None
        logger.warning("未配置 LLM API Key（AGENT_LLM_API_KEY / DEEPSEEK_API_KEY）：对话接口将返回降级话术")
    else:
        app.state.graph = build_graph(
            llm=llm,
            tools=tools,
            checkpointer=app.state.saver,
            history_max_messages=settings.history_max_messages,
        )
    logger.info(
        "waynboot-agent 启动：mall=%s redis=%s llm_configured=%s model=%s tools=%s",
        settings.mall_base_url,
        settings.redis_url,
        settings.llm_configured,
        settings.llm_model,
        [tool.name for tool in tools],
    )
    try:
        yield
    finally:
        await app.state.mall.aclose()
        await app.state.saver.aclose()
        await app.state.idempotency.aclose()
        await app.state.rate_limiter.aclose()
        await app.state.usage.aclose()
        logger.info("waynboot-agent 关闭")


app = FastAPI(title="waynboot-agent", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next: Any):
    """为每个请求分配 request id，贯穿全链路日志并回写到响应头。"""
    request_id = request.headers.get("X-Request-Id") or new_request_id()
    context_token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(context_token)
    response.headers["X-Request-Id"] = request_id
    return response


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    """存活探针：不访问任何外部依赖。"""
    return {"status": "ok", "app": "waynboot-agent", "version": __version__}


@app.get("/readyz")
async def readyz(request: Request):
    """就绪探针：探测 mall 与 Redis（联调时先看这个）。任一不可达返回 503。"""
    mall: MallClient = request.app.state.mall
    saver: RedisCheckpointSaver = request.app.state.saver
    settings: Settings = request.app.state.settings

    mall_reachable = await mall.ping()
    redis_reachable = await saver.aping()
    payload = {
        "status": "ok" if (mall_reachable and redis_reachable) else "degraded",
        "mall": {"base_url": mall.base_url, "reachable": mall_reachable},
        "redis": {"reachable": redis_reachable},
        "llm_configured": settings.llm_configured,
    }
    if payload["status"] != "ok":
        return JSONResponse(status_code=503, content=payload)
    return payload


async def require_access_token(request: Request) -> None:
    """Agent 自身访问令牌（可选闸门，配置 ``AGENT_ACCESS_TOKEN`` 后启用）。

    为什么不靠"没有 token 就查不到数据"兜着：**LLM 调用要花钱**，任何人都能拿匿名会话
    刷我们的额度。生产环境仍应把本服务放在网关/内网之后，这只是"不裸奔向公网"的最低成本做法。

    刻意**不管**登录代理与探针：前者是用户登录的入口（mall 侧有自己的频控），后者要给编排系统用。
    """
    expected: str = (request.app.state.settings.access_token or "").strip()
    if not expected:
        return
    provided = request.headers.get("X-Agent-Token", "")
    if not secrets.compare_digest(provided, expected):
        logger.warning("拒绝访问：X-Agent-Token 缺失或不匹配 path=%s", request.url.path)
        raise HTTPException(status_code=401, detail={"message": "缺少或错误的 X-Agent-Token"})


def _caller_identity(request: Request) -> str:
    """调用方身份：登录按 token 哈希、匿名按 IP（限流与用量统计共用，见 app/memory/identity.py）。"""
    client = request.client
    return caller_identity(request.headers.get("Authorization"), client.host if client else None)


async def _record_stream_usage(request: Request, usage: dict[str, Any] | None) -> None:
    """流式接口的用量记录：done 事件里带的就是本轮累计值。"""
    if not usage or not usage.get("llm_calls"):
        return
    from app.graph.usage import TurnUsage

    await request.app.state.usage.arecord(
        _caller_identity(request),
        TurnUsage(
            calls=int(usage.get("llm_calls") or 0),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        ),
    )


async def _record_usage(request: Request, result: Any) -> None:
    """把本轮用量累加到当天（统计失败不影响对话，tracker 内部已兜）。"""
    usage = getattr(result, "usage", None)
    if usage is None or usage.calls <= 0:
        return
    await request.app.state.usage.arecord(_caller_identity(request), usage)


async def enforce_rate_limit(request: Request) -> None:
    """限流闸门（按调用方身份计数，不需要请求体）：QPS 或日配额超了返回 429。"""
    limiter: RateLimiter = request.app.state.rate_limiter
    client = request.client
    identity = limiter.identity(request.headers.get("Authorization"), client.host if client else None)
    decision = await limiter.check(identity)
    if decision.allowed:
        return

    if decision.scope == "daily":
        message = "今天的咨询次数已经用完了，可以明天再来，或回复「转人工」由人工客服处理。"
    else:
        message = "你问得有点快，缓一下再问吧～"
    logger.info("限流拦截：scope=%s identity=%s limit=%s", decision.scope, identity, decision.limit)
    raise HTTPException(
        status_code=429,
        detail={"reason": decision.scope, "retry_after": decision.retry_after, "message": message},
        headers={"Retry-After": str(decision.retry_after)},
    )


@app.post("/api/chat", dependencies=[Depends(require_access_token), Depends(enforce_rate_limit)])
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    """一轮对话（一次性返回；流式版本见 ``/api/chat/stream``）。

    鉴权透传：请求头里的 ``Authorization`` 原样交给工具层去调 mall（决策 D2）。

    若返回里带 ``pending_action``，说明有写操作在等用户确认——前端弹确认卡片，
    用户点确认后再调 ``POST /api/chat/confirm``。
    """
    settings: Settings = request.app.state.settings
    result = await run_turn(
        request.app.state.graph,
        session_id=payload.session_id,
        message=payload.message,
        token=request.headers.get("Authorization"),
        recursion_limit=settings.graph_recursion_limit,
    )
    await _record_usage(request, result)
    return ChatResponse(
        session_id=payload.session_id,
        reply=result.reply,
        degraded=result.degraded,
        reason=result.reason,
        escalate=result.escalate,
        pending_action=result.pending_action,
    )


@app.post(
    "/api/chat/confirm",
    dependencies=[Depends(require_access_token), Depends(enforce_rate_limit)],
)
async def chat_confirm(payload: ConfirmRequest, request: Request) -> ChatResponse:
    """确认（或取消）上一步被中断的写操作，然后继续这一轮对话。

    ⚠️ 必须再次携带 ``Authorization``：恢复时工具会**从头重跑**（重新校验一遍订单归属与状态，
    这正好覆盖了"用户思考期间订单状态变了"的情况），因此同样需要用户的凭证。
    """
    settings: Settings = request.app.state.settings
    result = await resume_turn(
        request.app.state.graph,
        session_id=payload.session_id,
        approved=payload.approved,
        note=payload.note,
        token=request.headers.get("Authorization"),
        recursion_limit=settings.graph_recursion_limit,
    )
    return ChatResponse(
        session_id=payload.session_id,
        reply=result.reply,
        degraded=result.degraded,
        reason=result.reason,
        escalate=result.escalate,
        pending_action=result.pending_action,
    )


@app.post(
    "/api/chat/stream",
    dependencies=[Depends(require_access_token), Depends(enforce_rate_limit)],
)
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    """一轮对话，SSE 流式返回（事件协议见 app/graph/streaming.py）。"""
    settings: Settings = request.app.state.settings
    graph = request.app.state.graph
    token = request.headers.get("Authorization")
    # 流式响应在中间件返回后还在继续产出，此时 request_id 已被重置 → 先取出来，在生成器里重新绑定
    request_id = request_id_var.get()

    async def event_stream() -> AsyncIterator[str]:
        request_id_var.set(request_id)
        async for event in stream_turn(
            graph,
            session_id=payload.session_id,
            message=payload.message,
            token=token,
            recursion_limit=settings.graph_recursion_limit,
        ):
            if event["event"] == "done":
                await _record_stream_usage(request, event["data"].get("usage"))
            yield _sse(event["event"], event["data"])

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关掉 nginx 等反向代理的缓冲，否则"流式"会被攒成一次性返回
            "X-Accel-Buffering": "no",
        },
    )


@app.api_route(
    "/api/mall/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    include_in_schema=False,
)
async def mall_proxy(path: str, request: Request) -> Response:
    """把商城页面的请求转发给 mall（BFF）。

    **刻意不挂限流与访问闸门**：这两道闸门是保护 LLM 额度的（花的是我们的钱），
    而这里是用户查自己的订单、加自己的购物车——mall 自己有鉴权与频控。
    给它们挂上 LLM 的限流只会误伤正常购物。
    """
    return await forward(request, request.app.state.mall, path)


@app.get(
    "/api/sessions/{session_id}",
    response_model=SessionSummary,
    dependencies=[Depends(require_access_token)],
)
async def get_session(session_id: str, request: Request) -> SessionSummary:
    """会话概览：直接从 Redis 读，能看到消息条数与待处理写入数。"""
    saver: RedisCheckpointSaver = request.app.state.saver
    # 与对话路径用同一套派生规则：会话归属由 token 决定，别人拿到 session_id 也读不到
    thread_id = resolve_thread_id(session_id, request.headers.get("Authorization"))
    summary = await saver.aget_thread_summary(thread_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在或已过期")
    return SessionSummary(
        session_id=session_id, **{key: summary[key] for key in summary if key != "thread_id"}
    )


@app.delete("/api/sessions/{session_id}", status_code=204, dependencies=[Depends(require_access_token)])
async def delete_session(session_id: str, request: Request) -> Response:
    """清空会话（幂等：不存在也返回 204）。"""
    saver: RedisCheckpointSaver = request.app.state.saver
    await saver.adelete_thread(resolve_thread_id(session_id, request.headers.get("Authorization")))
    return Response(status_code=204)


@app.get("/api/usage", dependencies=[Depends(require_access_token)])
async def get_usage(request: Request, days: int = Query(default=7, ge=1, le=30)) -> dict[str, Any]:
    """当前调用方的 token 用量（按天）。

    身份规则与限流一致：登录用户按 token 哈希、匿名按 IP —— 所以这里看到的就是"你自己的账"。
    """
    tracker: UsageTracker = request.app.state.usage
    return await tracker.aread(_caller_identity(request), days=days)


@app.post("/api/login/code")
async def send_login_code(payload: LoginCodeRequest, request: Request) -> dict[str, Any]:
    """登录代理：转发「发送短信验证码」给 mall。

    **为什么由 Agent 代理登录**：mall 跑在 82、前端页面在 8300，浏览器直连 mall 会撞跨域；
    代理一层后前端只认 Agent 一个入口（也顺便不用把 mall 暴露给浏览器）。
    开发环境 mall 的验证码固定 `1234`（`LoginController` 的非 prod 分支），真上生产要接短信通道。
    """
    mall: MallClient = request.app.state.mall
    try:
        await mall.post("/genMobileCode", json={"mobile": payload.mobile})
    except MallError as exc:
        raise _login_error(exc) from exc
    return {"sent": True, "hint": "开发环境验证码固定 1234"}


@app.post("/api/login", response_model=LoginResponse)
async def login(payload: LoginRequest, request: Request) -> LoginResponse:
    """登录代理：把 mall 签发的 Sa-Token 交给前端（Agent 自己不保存凭证）。"""
    mall: MallClient = request.app.state.mall
    try:
        token = await mall.post("/login", json={"mobile": payload.mobile, "yzm": payload.yzm})
    except MallError as exc:
        raise _login_error(exc) from exc
    if not token:
        raise HTTPException(status_code=502, detail={"message": "登录失败：商城没有返回令牌"})
    logger.info("登录代理成功：mobile=%s****%s", payload.mobile[:3], payload.mobile[-4:])
    return LoginResponse(token=str(token), mobile=payload.mobile)


def _login_error(exc: MallError) -> HTTPException:
    """把 mall 的失败原因原样透给前端（例如「验证码输入错误」），别吞成"系统错误"。"""
    message = getattr(exc, "msg", None) or "登录失败，请稍后再试"
    status = 401 if isinstance(exc, MallAuthError) else 400
    return HTTPException(status_code=status, detail={"message": message})


# 商城前端与客服聊天页：都是静态文件，无构建步骤（见 app/static/）
_WEBUI_DIR = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
async def webui_root() -> FileResponse:
    """根路径给商城首页（H5 单页，`#/xxx` 是页面路由）。"""
    return FileResponse(_WEBUI_DIR / "index.html")


@app.get("/chat", include_in_schema=False)
async def chat_page() -> FileResponse:
    """AI 客服聊天页（商城页右下角浮标指向这里）。"""
    return FileResponse(_WEBUI_DIR / "chat.html")


app.mount("/ui", StaticFiles(directory=_WEBUI_DIR, html=True), name="ui")


def _sse(event: str, data: dict[str, Any]) -> str:
    """拼一个 SSE 帧（``event:`` + ``data:`` + 空行结尾）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
