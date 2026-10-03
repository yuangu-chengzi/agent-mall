"""LLM 构造。

用 OpenAI 兼容协议接 DeepSeek（决策 D1）：换供应商只需改 ``AGENT_LLM_BASE_URL`` / ``AGENT_LLM_MODEL``。
"""

from langchain_openai import ChatOpenAI

from app.config import Settings


def build_llm(settings: Settings) -> ChatOpenAI | None:
    """按配置构造 LLM。

    未配置 API Key 时返回 ``None``（由上层走降级话术）：**服务照常启动**，
    便于没有 Key 的环境（CI、他人克隆后试跑）也能把非 LLM 部分跑起来。
    """
    if not settings.llm_configured:
        return None
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout_seconds,
        # 客服要的是稳定复现而不是创造性
        temperature=0,
        # 只重试 1 次：再失败就走降级话术，不能让用户干等
        max_retries=1,
    )
