"""每用户限流与日配额（工程约定：QPS + 日配额）。

思路与 mall 秒杀频控一致：Redis 计数 + TTL 窗口。

- **QPS**：``INCR`` 一个 1 秒窗口的计数键，超阈值即拒绝（固定窗口，简单够用；
  要精确到滑动窗口得上 ZSET 存时间戳，对这个场景是过度设计）；
- **日配额**：``INCR`` 一个 24 小时窗口的计数键——防的是"一个人把当天额度刷爆"。

身份识别：优先用 ``Authorization`` 头（**哈希后**进 key，绝不把 token 原文写进 Redis key），
没带就退回客户端 IP——匿名流量同样不能无限刷。

**fail-open**：Redis 出问题时**放行**。限流是保护措施，不该变成故障点；
但会打警告日志，免得"限流失效"这件事悄悄发生。
"""

from dataclasses import dataclass

from redis.asyncio import Redis

from app.config import Settings
from app.memory.identity import caller_identity
from app.observability import get_logger

logger = get_logger(__name__)

#: 日配额窗口（秒）
DAY_SECONDS = 86400


@dataclass(frozen=True)
class RateLimitDecision:
    """限流判定结果。"""

    allowed: bool
    scope: str | None = None  # "qps"（每秒） / "daily"（日配额）
    retry_after: int = 0
    limit: int = 0


class RateLimiter:
    """基于 Redis 的固定窗口限流器。"""

    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "waynboot:agent:rl:",
        qps: int = 3,
        daily: int = 300,
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._qps = max(qps, 1)
        self._daily = max(daily, 1)

    @classmethod
    def from_settings(cls, settings: Settings) -> "RateLimiter":
        """按配置创建。"""
        return cls(
            Redis.from_url(settings.redis_url, decode_responses=False),
            prefix=f"{settings.redis_key_prefix}rl:",
            qps=settings.rate_limit_qps,
            daily=settings.rate_limit_daily,
        )

    async def aclose(self) -> None:
        """关闭底层连接池。"""
        await self._redis.aclose()

    @staticmethod
    def identity(token: str | None, client_host: str | None) -> str:
        """限流身份：实现见 ``app.memory.identity``（与用量统计共用同一套规则）。"""
        return caller_identity(token, client_host)

    async def check(self, identity: str) -> RateLimitDecision:
        """记一次请求，并判断是否放行。"""
        try:
            return await self._check(identity)
        except Exception as exc:  # noqa: BLE001
            logger.warning("限流检查失败，放行（fail-open）：%s", exc)
            return RateLimitDecision(allowed=True)

    async def _check(self, identity: str) -> RateLimitDecision:
        qps_key = f"{self._prefix}qps:{identity}"
        count = await self._redis.incr(qps_key)
        if count == 1:
            await self._redis.expire(qps_key, 1)
        if count > self._qps:
            ttl = await self._redis.ttl(qps_key)
            return RateLimitDecision(False, "qps", max(ttl, 1), self._qps)

        day_key = f"{self._prefix}day:{identity}"
        total = await self._redis.incr(day_key)
        if total == 1:
            await self._redis.expire(day_key, DAY_SECONDS)
        if total > self._daily:
            ttl = await self._redis.ttl(day_key)
            return RateLimitDecision(False, "daily", max(ttl, 1), self._daily)

        return RateLimitDecision(True)
