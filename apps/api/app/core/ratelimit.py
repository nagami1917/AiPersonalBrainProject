"""Rate limiting for the expensive endpoints.

Rule 8 of the security architecture: apply rate limits to expensive AI endpoints.
Expensive means *provider calls* — chat, streaming chat, summarisation, and any
endpoint that embeds a query. CRUD is not rate limited; a user dragging a note
into shape with autosave every 1.5 s is normal behaviour, and throttling it would
be an outage dressed as security.

## Two independent budgets

Every limited call is checked against BOTH dimensions, because they fail
differently:

  * **per-user** — one account hammering the provider. This is the abuse and
    runaway-client case (a retry loop in a tab left open overnight).
  * **per-workspace** — the shared cost ceiling. A workspace with 20 members
    should not be able to spend 20× one member's budget, and a single leaked
    member account should not drain the whole tenant.

A limit that only counts per user cannot protect the bill; a limit that only
counts per workspace lets one user lock everyone else out. Both are checked.

## Why in-process state, and what that costs

The counters live in a dict guarded by an asyncio lock. That is honest only
while the API is a single instance: with N replicas the effective limit is N×.
The alternative — Redis — adds an operational dependency the project does not
have (ADR-002 uses Postgres for the queue precisely to avoid one). So the limit
is *approximate by design*, and `RateLimiter.is_distributed` is exposed so the
capability document can say so rather than implying a guarantee that is not
there. Postgres-backed counting is the upgrade path, and the interface is
already async, so it can be swapped without touching a route.

## Window

Fixed window, not sliding. A sliding window needs a timestamp per call for
every (scope, key) pair; a fixed window needs two integers. Fixed windows allow
a burst of up to 2× the limit across a boundary, which is an acceptable trade
for a cost guard — this is not an anti-bot defence.

The clock is injectable so the tests exercise expiry without sleeping.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.core.errors import RateLimited

Clock = Callable[[], float]


@dataclass
class _Window:
    """One counter for one (scope, key) pair."""

    started_at: float
    count: int = 0


@dataclass(frozen=True)
class Limit:
    """`calls` per `seconds`, for one scope."""

    calls: int
    seconds: float


class RateLimiter:
    """Fixed-window counters over two scopes, with a bounded amount of state.

    The sweep is what keeps this from being a memory leak: keys arrive at the
    rate of distinct users and workspaces, so an unbounded dict would grow
    forever on a busy instance. Expired windows are dropped on a coarse interval
    rather than on every call, so the common path is a dict lookup.
    """

    def __init__(
        self,
        *,
        user_limit: Limit,
        workspace_limit: Limit,
        clock: Clock = time.monotonic,
        sweep_every: int = 500,
    ) -> None:
        self._user_limit = user_limit
        self._workspace_limit = workspace_limit
        self._clock = clock
        self._windows: dict[tuple[str, str], _Window] = {}
        self._lock = asyncio.Lock()
        self._calls_since_sweep = 0
        self._sweep_every = sweep_every

    # The API is a single process. Said out loud here so no caller assumes
    # otherwise; a future Postgres- or Redis-backed limiter flips this.
    is_distributed = False

    async def check(self, *, user_id: str, workspace_id: str, cost: str) -> None:
        """Raise `RateLimited` if either budget is exhausted, else count the call.

        Both budgets are checked before either is incremented, so a request
        rejected by the workspace ceiling does not also consume the human's
        personal allowance — otherwise a noisy neighbour would silently reduce
        everyone's limit to zero.
        """
        now = self._clock()
        async with self._lock:
            user_window = self._peek(("user", f"{cost}:{user_id}"), self._user_limit, now)
            workspace_window = self._peek(
                ("workspace", f"{cost}:{workspace_id}"), self._workspace_limit, now
            )

            if user_window.count >= self._user_limit.calls:
                raise RateLimited(
                    "You are making requests faster than this endpoint allows. "
                    "Wait a moment and try again.",
                    details={"scope": "user", "retry_after": self._retry_after(user_window)},
                )
            if workspace_window.count >= self._workspace_limit.calls:
                raise RateLimited(
                    "This workspace has reached its request limit for now. "
                    "Wait a moment and try again.",
                    details={
                        "scope": "workspace",
                        "retry_after": self._retry_after(workspace_window),
                    },
                )

            user_window.count += 1
            workspace_window.count += 1

            self._calls_since_sweep += 1
            if self._calls_since_sweep >= self._sweep_every:
                self._sweep(now)
                self._calls_since_sweep = 0

    def _peek(self, key: tuple[str, str], limit: Limit, now: float) -> _Window:
        """Current window for `key`, rolling it over if it has expired."""
        window = self._windows.get(key)
        if window is None or now - window.started_at >= limit.seconds:
            window = _Window(started_at=now)
            self._windows[key] = window
        return window

    def _retry_after(self, window: _Window) -> int:
        elapsed = self._clock() - window.started_at
        for limit in (self._user_limit, self._workspace_limit):
            remaining = limit.seconds - elapsed
            if remaining > 0:
                return max(1, int(remaining) + 1)
        return 1

    def _sweep(self, now: float) -> None:
        longest = max(self._user_limit.seconds, self._workspace_limit.seconds)
        expired = [key for key, w in self._windows.items() if now - w.started_at >= longest]
        for key in expired:
            del self._windows[key]

    # -- introspection, used by tests and the capability document -------------

    @property
    def tracked_keys(self) -> int:
        return len(self._windows)

    async def reset(self) -> None:
        async with self._lock:
            self._windows.clear()
            self._calls_since_sweep = 0


# ---------------------------------------------------------------------------
# Process-wide limiter, configured from settings.
#
# Built lazily rather than at import so tests can construct their own with a
# fake clock, and so importing this module never reads the environment.
# ---------------------------------------------------------------------------

_limiter: RateLimiter | None = None
_limiter_lock = asyncio.Lock()


async def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        async with _limiter_lock:
            if _limiter is None:
                from app.core.config import get_settings

                settings = get_settings()
                _limiter = RateLimiter(
                    user_limit=Limit(
                        calls=settings.rate_limit_user_calls,
                        seconds=settings.rate_limit_window_seconds,
                    ),
                    workspace_limit=Limit(
                        calls=settings.rate_limit_workspace_calls,
                        seconds=settings.rate_limit_window_seconds,
                    ),
                )
    return _limiter


def set_limiter(limiter: RateLimiter | None) -> None:
    """Install a limiter (tests), or clear it so the next call rebuilds from settings."""
    global _limiter
    _limiter = limiter


async def enforce(user_id: str, workspace_id: str, *, cost: str) -> None:
    """Convenience wrapper for routes that already have both ids in hand."""
    limiter = await get_limiter()
    await limiter.check(user_id=user_id, workspace_id=workspace_id, cost=cost)
