"""配置绑定测试：环境变量映射、默认值、单例缓存。"""

import pytest

from app.config import Settings, get_settings


def test_env_prefix_maps_to_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量 AGENT_<字段名大写> 必须能覆盖默认值。"""
    monkeypatch.setenv("AGENT_MALL_BASE_URL", "http://mall.example:82")
    monkeypatch.setenv("AGENT_GRAPH_RECURSION_LIMIT", "42")

    settings = Settings(_env_file=None)

    assert settings.mall_base_url == "http://mall.example:82"
    assert settings.graph_recursion_limit == 42


def test_defaults_are_usable_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """不提供任何环境变量时也应有可用默认值（本地开发零配置启动）。"""
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.port == 8300
    assert settings.mall_base_url == "http://localhost:82"
    assert settings.redis_key_prefix == "waynboot:agent:"
    assert settings.llm_configured is False


def test_api_key_falls_back_to_system_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """没配 AGENT_LLM_API_KEY 时回退读系统里的 DEEPSEEK_API_KEY（密钥不进仓库）。"""
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-system")

    assert Settings(_env_file=None).llm_api_key == "sk-from-system"


def test_agent_api_key_wins_over_system_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式配置优先于系统变量。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-system")
    monkeypatch.setenv("AGENT_LLM_API_KEY", "sk-explicit")

    assert Settings(_env_file=None).llm_api_key == "sk-explicit"


def test_llm_configured_reflects_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """空白 Key 视为未配置（用于触发降级话术，而不是启动失败）。"""
    monkeypatch.setenv("AGENT_LLM_API_KEY", "   ")
    assert Settings(_env_file=None).llm_configured is False

    monkeypatch.setenv("AGENT_LLM_API_KEY", "sk-test")
    assert Settings(_env_file=None).llm_configured is True


def test_get_settings_is_cached_singleton() -> None:
    """进程内复用同一份配置；测试可通过 cache_clear 复位。"""
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()


def test_unknown_env_keys_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """extra=ignore：环境下多出的无关变量不应导致启动失败。"""
    monkeypatch.setenv("AGENT_NOT_A_REAL_FIELD", "x")
    assert Settings(_env_file=None).port == 8300


def test_access_token_env_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """AGENT_ACCESS_TOKEN 必须真的能读到。

    这条测试是为了防一个踩过的坑：字段名带 `agent_` 前缀时，pydantic-settings 会去找
    `AGENT_AGENT_ACCESS_TOKEN`，于是 `.env` 里写的 `AGENT_ACCESS_TOKEN` **静默失效**
    （配置没生效但功能看起来"做了"）。单元测试用 init 参数传值时发现不了，只有走环境变量才会暴露。
    """
    monkeypatch.setenv("AGENT_ACCESS_TOKEN", "secret-from-env")

    assert Settings(_env_file=None).access_token == "secret-from-env"


def test_history_window_env_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """AGENT_HISTORY_MAX_MESSAGES 同理：名字对不上就会静默用默认值。"""
    monkeypatch.setenv("AGENT_HISTORY_MAX_MESSAGES", "7")

    assert Settings(_env_file=None).history_max_messages == 7
