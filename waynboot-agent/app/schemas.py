"""HTTP 接口的请求 / 响应模型。"""

from typing import Any

from pydantic import BaseModel, Field

#: 会话 ID 允许的字符集。
#: 会话 ID 会被拼进 Redis key（`waynboot:agent:ckpt:{session_id}:latest`），
#: 所以必须排除可能造成 key 歧义的字符（尤其是 `:`）——这是"用户输入直接进 key"的常见坑。
SESSION_ID_PATTERN = r"^[A-Za-z0-9_.\-]{1,64}$"


class ChatRequest(BaseModel):
    """一轮对话请求。"""

    session_id: str = Field(
        pattern=SESSION_ID_PATTERN,
        description="会话 ID：同一会话的多轮消息共享上下文（建议用登录用户 ID + 随机串）",
    )
    message: str = Field(min_length=1, max_length=2000, description="用户这一轮说的话")


class ChatResponse(BaseModel):
    """一轮对话响应（非流式）。"""

    session_id: str
    reply: str
    degraded: bool = Field(default=False, description="是否走了降级话术（LLM 不可用/超步数/执行失败）")
    reason: str | None = Field(
        default=None, description="降级原因：llm_unavailable / too_many_steps / error …"
    )
    escalate: bool = Field(default=False, description="本轮是否触发了转人工")
    pending_action: dict[str, Any] | None = Field(
        default=None,
        description=(
            "不为空表示有写操作等待用户确认：前端应弹确认卡片，用户点确认后调 /api/chat/confirm。"
            "字段含 action / ask / summary / impact / reversible 等。"
        ),
    )


class ConfirmRequest(BaseModel):
    """写操作确认（人工确认闸门，决策 D3）。"""

    session_id: str = Field(pattern=SESSION_ID_PATTERN, description="与发起对话时相同的会话 ID")
    approved: bool = Field(description="true=确认执行，false=取消")
    note: str | None = Field(default=None, max_length=200, description="用户附言（可选，仅记录）")


class SessionSummary(BaseModel):
    """会话概览（从 Redis 里读出来的实际状态）。"""

    session_id: str
    checkpoint_id: str
    message_count: int = Field(description="会话里累积的消息条数（含用户、助手、工具消息）")
    pending_writes: int = Field(description="待处理的写入数（P4 的人工确认中断会用到）")
    created_at: int = Field(description="最新 checkpoint 的写入时间（epoch 毫秒）")


class LoginCodeRequest(BaseModel):
    """发送短信验证码。"""

    mobile: str = Field(pattern=r"^1[3-9]\d{9}$", description="手机号")


class LoginRequest(BaseModel):
    """验证码登录。"""

    mobile: str = Field(pattern=r"^1[3-9]\d{9}$", description="手机号")
    yzm: str = Field(min_length=1, max_length=10, description="短信验证码（开发环境固定 1234）")


class LoginResponse(BaseModel):
    """登录成功返回的 Sa-Token；前端随后把它放进 Authorization 头透传给 Agent。"""

    token: str = Field(description="mall 侧签发的 Sa-Token")
    mobile: str
