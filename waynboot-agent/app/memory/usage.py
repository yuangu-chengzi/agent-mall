"""token 用量统计：按「调用方身份 + 天」累计，用于回答"这个月花了多少、谁花得最多"。

为什么必须做：Agent 服务最贵的资源就是 LLM 调用，而**没有用量数据就没法谈成本**——
上线第一天你就会想知道：一天多少次调用？一个会话平均烧多少 token？哪个用户是异常大户？

key 布局（与限流、会话记忆同一个前缀家族，便于 `redis-cli --scan` 一次性查看）::

    {p}{identity}:{yyyymmdd}   HASH  → calls / input_tokens / output_tokens

身份规则与限流共用（见 ``app.memory.identity``），所以"限流看到的用户"和"用量统计看到的用户"是同一个人。
只保留最近 N 天（默认 7 天）——够看成本趋势，又不至于长期占内存。
"""

from dataclasses import dataclass
from datetime import date, timedelta

from redis.asyncio import Redis

from app.config import Settings
from app.graph.usage import TurnUsage
from app.observability import get_logger

logger = get_logger(__name__)

#: 用量记录保留天数（超过就随 TTL 过期）
DEFAULT_RETENTION_DAYS = 7


@dataclass(frozen=True)
class DailyUsage:
    """某一天的用量。"""

    date: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class UsageTracker:
    """基于 Redis 的按天用量累加器。"""

    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "waynboot:agent:usage:",
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        self._redis = redis
        self._prefix = prefix
        self._retention_days = max(retention_days, 1)

    @classmethod
    def from_settings(cls, settings: Settings) -> "UsageTracker":
        """按配置创建。"""
        return cls(
            Redis.from_url(settings.redis_url, decode_responses=False),
            prefix=f"{settings.redis_key_prefix}usage:",
        )

    async def aclose(self) -> None:
        """关闭底层连接池。"""
        await self._redis.aclose()

    async def aping(self) -> bool:
        """探活。"""
        try:
            return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001
            return False

    async def arecord(self, identity: str, usage: TurnUsage) -> None:
        """把一轮对话的用量累加到当天。

        **统计失败不能影响对话**：这里刻意吞掉异常（只打警告）——用量是观测手段，
        不该成为新的故障点（和限流 fail-open 是同一个判断）。
        """
        if usage.calls <= 0:
            return
        key = self._key(identity, date.today())
        try:
            pipe = self._redis.pipeline()
            pipe.hincrby(key, "calls", usage.calls)
            pipe.hincrby(key, "input_tokens", usage.input_tokens)
            pipe.hincrby(key, "output_tokens", usage.output_tokens)
            pipe.expire(key, self._retention_days * 86400)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("用量统计写入失败（不影响对话）：%s", exc)

    async def aread(self, identity: str, *, days: int = DEFAULT_RETENTION_DAYS) -> dict:
        """读最近 ``days`` 天的用量（从今天往前数）。"""
        days = max(1, min(days, self._retention_days))
        today = date.today()
        daily: list[DailyUsage] = []
        for offset in range(days):
            day = today - timedelta(days=offset)
            stored = await self._redis.hgetall(self._key(identity, day))
            daily.append(
                DailyUsage(
                    date=day.isoformat(),
                    calls=_int_of(stored.get(b"calls")),
                    input_tokens=_int_of(stored.get(b"input_tokens")),
                    output_tokens=_int_of(stored.get(b"output_tokens")),
                )
            )
        total = DailyUsage(
            date=f"最近{days}天",
            calls=sum(item.calls for item in daily),
            input_tokens=sum(item.input_tokens for item in daily),
            output_tokens=sum(item.output_tokens for item in daily),
        )
        return {"daily": [_as_payload(item) for item in daily], "total": _as_payload(total)}

    def _key(self, identity: str, day: date) -> str:
        return f"{self._prefix}{identity}:{day.strftime('%Y%m%d')}"


def _as_payload(item: DailyUsage) -> dict:
    """转成接口返回结构（``total_tokens`` 是计算值，asdict 不会带上，所以显式拼）。"""
    return {
        "date": item.date,
        "calls": item.calls,
        "input_tokens": item.input_tokens,
        "output_tokens": item.output_tokens,
        "total_tokens": item.total_tokens,
    }


def _int_of(raw: bytes | None) -> int:
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0
