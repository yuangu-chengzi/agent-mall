"""调用方身份派生：限流与用量统计共用同一套规则。

为什么必须共用：如果限流按"用户"计数、用量统计按"IP"计数，两边对不上账——
排查"谁把额度刷爆了"时会得出互相矛盾的结论。

规则：
- **带 token**：`sha256(token)` 前 16 位。绝不把凭证原文写进 Redis key。
- **不带 token**：按客户端 IP（匿名流量也要能被计数/限流）。
"""

import hashlib


def caller_identity(token: str | None, client_host: str | None) -> str:
    """返回调用方身份标识（可直接拼进 Redis key）。"""
    if token:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return f"ip-{(client_host or 'unknown').replace(':', '_')}"
