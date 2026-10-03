"""配置绑定。

所有可调项集中在本模块的 ``Settings``，取值优先级：**环境变量 > .env > 默认值**。
环境变量统一前缀 ``AGENT_``（例如 ``llm_api_key`` → ``AGENT_LLM_API_KEY``），
样例见仓库根目录 ``.env.example``。
"""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """服务配置。字段名即配置项，注释里给出对应的环境变量名。"""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 服务自身 ---
    host: str = "0.0.0.0"  # AGENT_HOST
    port: int = 8300  # AGENT_PORT
    log_level: str = "INFO"  # AGENT_LOG_LEVEL

    # --- mall（waynboot-mobile-api）---
    mall_base_url: str = "http://localhost:82"  # AGENT_MALL_BASE_URL
    mall_timeout_seconds: float = 10.0  # AGENT_MALL_TIMEOUT_SECONDS

    # --- LLM（DeepSeek，OpenAI 兼容接口）---
    llm_base_url: str = "https://api.deepseek.com/v1"  # AGENT_LLM_BASE_URL
    # 优先读 AGENT_LLM_API_KEY；没配则回退用系统环境里的 DEEPSEEK_API_KEY
    # （密钥留在操作系统的环境变量里，比复制一份到仓库的 .env 更安全）
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("AGENT_LLM_API_KEY", "DEEPSEEK_API_KEY"),
    )
    # 可选值以 GET {base_url}/models 为准（当前：deepseek-flash / deepseek-v4-pro）
    llm_model: str = "deepseek-flash"  # AGENT_LLM_MODEL
    llm_timeout_seconds: float = 30.0  # AGENT_LLM_TIMEOUT_SECONDS

    # 每轮最多把多少条历史消息发给 LLM（0 = 不限）。超出部分用"已省略"提示收口，
    # 防止长对话的 token 成本线性上涨、最终撞上下文上限。
    history_max_messages: int = 24  # AGENT_HISTORY_MAX_MESSAGES

    # Agent 自己的访问令牌（可选）：非空时要求 /api/* 带 X-Agent-Token。
    # ⚠️ 字段名不要带 agent_ 前缀：env_prefix 会把 AGENT_ 拼在**字段名**前面，
    #   叫 agent_access_token 的话实际要找的是 AGENT_AGENT_ACCESS_TOKEN（踩过这个坑）。
    # 生产环境应放在网关/内网后面，这里是"不裸奔"的最低成本做法。
    access_token: str = ""  # AGENT_ACCESS_TOKEN

    # LangGraph 单次对话的最大步数（recursion_limit）。
    # 一轮「LLM 决策 + 执行工具」消耗 2 步，16 步 ≈ 8 轮工具调用。
    graph_recursion_limit: int = 16  # AGENT_GRAPH_RECURSION_LIMIT

    # --- Redis（会话记忆，复用 mall 的 Redis 实例，独立 key 前缀避免冲突）---
    redis_url: str = "redis://localhost:6379/0"  # AGENT_REDIS_URL
    redis_key_prefix: str = "waynboot:agent:"  # AGENT_REDIS_KEY_PREFIX
    session_ttl_seconds: int = 86400  # AGENT_SESSION_TTL_SECONDS（默认 24 小时，见 DECISIONS D5）

    # --- 限流与配额（按用户身份计数，见 app/memory/ratelimit.py）---
    rate_limit_qps: int = 3  # AGENT_RATE_LIMIT_QPS：每秒允许的对话请求数
    rate_limit_daily: int = 300  # AGENT_RATE_LIMIT_DAILY：每天允许的对话请求数

    @property
    def llm_configured(self) -> bool:
        """是否配置了 LLM API Key（未配置时走降级话术，而不是启动失败）。"""
        return bool(self.llm_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    """进程级单例配置。测试中如需覆盖环境变量，先调用 ``get_settings.cache_clear()``。"""
    return Settings()
