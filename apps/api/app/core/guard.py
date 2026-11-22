"""Calling the rate limiter from a route without repeating four lines.

Rule 8 of the security architecture. The check is centralised here so the set of
limited endpoints is one `grep` away, and so "is this route limited?" is a
question with a definite answer rather than a judgement call per handler.

`cost` groups endpoints that share a budget: chat and chat/stream are the same
kind of spend and share one pool, summarisation is another. Without grouping, a
client could triple its effective allowance by alternating between the three
paths that all call the same provider.
"""

from __future__ import annotations

from app.core import ratelimit
from app.core.config import get_settings


async def ai_call(user_id: str, workspace_id: str, *, cost: str = "ai") -> None:
    """Consume one unit of the AI budget, or raise 429.

    Honours `rate_limit_enabled` so a local run can turn it off without patching
    a route, and so the tests can exercise the disabled path.
    """
    if not get_settings().rate_limit_enabled:
        return
    await ratelimit.enforce(user_id=user_id, workspace_id=workspace_id, cost=cost)
