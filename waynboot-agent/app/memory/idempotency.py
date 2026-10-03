"""写操作幂等：同一动作在 TTL 内只真正执行一次（决策 D4）。

**为什么 Agent 侧还要做幂等**：mall 侧确实有自己的幂等（购物车用户锁、券一人一张、订单状态机条件更新），
但它们防的是"业务上不该重复"，防不住"用户连点两下确认"这种**本不该发生的第二次调用**——
多打一次接口、多占一次锁、还可能撞上"购物车数量累加"这种非幂等语义。

本类的语义：**先占位、再执行、后存结果**（``SETNX``）::

    第一次：SETNX 成功 → 执行 → 结果落 Redis（TTL 10 分钟）
    第二次：SETNX 失败 → 直接读回上次结果，不执行

执行抛异常时会把占位删掉：**失败不占坑**，允许用户重试。
"""

import hashlib
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis

from app.config import Settings
from app.observability import get_logger

logger = get_logger(__name__)

#: 占位标记（正在执行中）
PENDING = "__PENDING__"
#: 幂等记录 TTL（秒）：够覆盖"用户在确认卡片上反复点"的时间窗
DEFAULT_TTL_SECONDS = 600


class IdempotencyStore:
    """基于 Redis 的幂等执行器。"""

    def __init__(
        self, redis: Redis, *, prefix: str = "waynboot:agent:idem:", ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> "IdempotencyStore":
        """按配置创建（复用 mall 的 Redis，独立前缀）。"""
        return cls(Redis.from_url(settings.redis_url, decode_responses=False))

    async def aclose(self) -> None:
        """关闭底层连接池。"""
        await self._redis.aclose()

    async def aping(self) -> bool:
        """探活。"""
        try:
            return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def make_key(*parts: object) -> str:
        """把动作指纹压成定长 key（决策 D4：会话 + 动作 + 目标）。

        用哈希而不是拼接原文：目标 ID 里可能带奇怪字符，压成十六进制就不必再考虑转义。
        """
        raw = ":".join(str(part) for part in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    async def run_once(self, key: str, action: Callable[[], Awaitable[str]]) -> tuple[str, bool]:
        """执行 ``action``，但保证同一 ``key`` 在 TTL 内只真正执行一次。

        :return: ``(结果文本, 本次是否真的执行了)``
        """
        full_key = f"{self._prefix}{key}"
        claimed = await self._redis.set(full_key, PENDING, nx=True, ex=self._ttl)
        if not claimed:
            existing = await self._redis.get(full_key)
            text = _as_str(existing) if existing else ""
            if not text or text == PENDING:
                logger.info("幂等键 %s 命中「执行中」占位，未重复执行", key)
                return "（这笔操作正在处理中，稍后确认一下结果即可。）", False
            logger.info("幂等键 %s 命中已有结果，未重复执行", key)
            return text, False

        try:
            result = await action()
        except Exception:
            # 失败不留占位：让用户能重试（否则一次超时就把这个动作永久锁死了）
            await self._redis.delete(full_key)
            raise

        await self._redis.set(full_key, result, ex=self._ttl)
        return result, True


def _as_str(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    return raw.decode("utf-8") if isinstance(raw, bytes) else raw
