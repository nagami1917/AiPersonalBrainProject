"""The rate limiter, tested against a fake clock.

Rule 8 of the security architecture. The properties that matter are the ones a
naive counter gets wrong:

  * a rejected call must not consume the other budget (a noisy neighbour would
    otherwise zero out everyone else's allowance);
  * the window must actually reset, or a limit is a permanent ban;
  * state must not grow without bound, or the limit becomes a memory leak;
  * the 429 must say which budget was hit, because "slow down" is not actionable
    when the real cause is a colleague's traffic.
"""

from __future__ import annotations

import asyncio

import pytest
from app.core.errors import RateLimited
from app.core.ratelimit import Limit, RateLimiter


class FakeClock:
    """A monotonic clock the test advances by hand — no sleeping."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build(
    *, user: int = 3, workspace: int = 100, seconds: float = 60.0
) -> tuple[RateLimiter, FakeClock]:
    clock = FakeClock()
    limiter = RateLimiter(
        user_limit=Limit(calls=user, seconds=seconds),
        workspace_limit=Limit(calls=workspace, seconds=seconds),
        clock=clock,
    )
    return limiter, clock


class TestUserBudget:
    async def test_calls_up_to_the_limit_pass(self):
        limiter, _ = build(user=3)
        for _ in range(3):
            await limiter.check(user_id="u1", workspace_id="w1", cost="chat")

    async def test_the_next_call_is_rejected(self):
        limiter, _ = build(user=3)
        for _ in range(3):
            await limiter.check(user_id="u1", workspace_id="w1", cost="chat")
        with pytest.raises(RateLimited) as exc:
            await limiter.check(user_id="u1", workspace_id="w1", cost="chat")
        assert exc.value.details["scope"] == "user"
        assert exc.value.status_code == 429

    async def test_the_window_resets(self):
        limiter, clock = build(user=2, seconds=60)
        await limiter.check(user_id="u1", workspace_id="w1", cost="chat")
        await limiter.check(user_id="u1", workspace_id="w1", cost="chat")
        with pytest.raises(RateLimited):
            await limiter.check(user_id="u1", workspace_id="w1", cost="chat")

        clock.advance(61)
        await limiter.check(user_id="u1", workspace_id="w1", cost="chat")

    async def test_users_do_not_share_a_budget(self):
        limiter, _ = build(user=1)
        await limiter.check(user_id="u1", workspace_id="w1", cost="chat")
        await limiter.check(user_id="u2", workspace_id="w1", cost="chat")

    async def test_costs_do_not_share_a_budget(self):
        """Chat and summarise are separate pools — one cannot starve the other."""
        limiter, _ = build(user=1)
        await limiter.check(user_id="u1", workspace_id="w1", cost="chat")
        await limiter.check(user_id="u1", workspace_id="w1", cost="summarize")


class TestWorkspaceBudget:
    async def test_members_share_the_workspace_ceiling(self):
        limiter, _ = build(user=10, workspace=3)
        await limiter.check(user_id="u1", workspace_id="w1", cost="chat")
        await limiter.check(user_id="u2", workspace_id="w1", cost="chat")
        await limiter.check(user_id="u3", workspace_id="w1", cost="chat")
        with pytest.raises(RateLimited) as exc:
            await limiter.check(user_id="u4", workspace_id="w1", cost="chat")
        assert exc.value.details["scope"] == "workspace"

    async def test_a_workspace_rejection_does_not_consume_the_user_budget(self):
        """The rejection must not follow the user into their next workspace.

        If a workspace-429 incremented the user counter, a member of a busy
        workspace would find their personal allowance already spent the moment
        they switched — the noisy neighbour punishing them twice.
        """
        limiter, _ = build(user=5, workspace=1)
        await limiter.check(user_id="u1", workspace_id="w1", cost="chat")  # uses the one slot
        with pytest.raises(RateLimited):
            await limiter.check(user_id="u2", workspace_id="w1", cost="chat")

        # u2 has spent nothing personally, so another workspace still works.
        await limiter.check(user_id="u2", workspace_id="w2", cost="chat")

    async def test_workspaces_do_not_share_a_budget(self):
        limiter, _ = build(user=10, workspace=1)
        await limiter.check(user_id="u1", workspace_id="w1", cost="chat")
        await limiter.check(user_id="u2", workspace_id="w2", cost="chat")


class TestRetryAfter:
    async def test_reports_time_remaining_in_the_window(self):
        limiter, clock = build(user=1, seconds=60)
        await limiter.check(user_id="u1", workspace_id="w1", cost="chat")
        clock.advance(20)
        with pytest.raises(RateLimited) as exc:
            await limiter.check(user_id="u1", workspace_id="w1", cost="chat")
        retry_after = exc.value.details["retry_after"]
        assert 35 <= retry_after <= 41, retry_after


class TestBoundedState:
    async def test_expired_windows_are_swept(self):
        """Distinct users must not accumulate forever."""
        clock = FakeClock()
        limiter = RateLimiter(
            user_limit=Limit(calls=5, seconds=10),
            workspace_limit=Limit(calls=1000, seconds=10),
            clock=clock,
            sweep_every=10,
        )
        for index in range(20):
            await limiter.check(user_id=f"u{index}", workspace_id="w1", cost="chat")
        assert limiter.tracked_keys > 0

        clock.advance(11)  # everything expires
        for index in range(20):
            await limiter.check(user_id=f"u{index}", workspace_id="w2", cost="chat")

        # The old windows are gone; only the current batch is tracked.
        assert limiter.tracked_keys <= 22

    async def test_reset_clears_everything(self):
        limiter, _ = build(user=1)
        await limiter.check(user_id="u1", workspace_id="w1", cost="chat")
        await limiter.reset()
        assert limiter.tracked_keys == 0
        await limiter.check(user_id="u1", workspace_id="w1", cost="chat")


class TestGuardedThroughTheRoute:
    """The guard honours the enable switch and is what routes actually call."""

    async def test_disabled_limiter_never_raises(self, monkeypatch):
        from app.core import guard

        monkeypatch.setattr(guard.get_settings(), "rate_limit_enabled", False, raising=False)
        for _ in range(50):
            await guard.ai_call("u1", "w1", cost="chat")

    async def test_the_guard_raises_once_the_budget_is_spent(self, monkeypatch):
        from app.core import guard, ratelimit

        clock = FakeClock()
        limiter = RateLimiter(
            user_limit=Limit(calls=2, seconds=60),
            workspace_limit=Limit(calls=100, seconds=60),
            clock=clock,
        )
        monkeypatch.setattr(guard.get_settings(), "rate_limit_enabled", True, raising=False)
        ratelimit.set_limiter(limiter)
        try:
            await guard.ai_call("u1", "w1", cost="chat")
            await guard.ai_call("u1", "w1", cost="chat")
            with pytest.raises(RateLimited):
                await guard.ai_call("u1", "w1", cost="chat")
        finally:
            ratelimit.set_limiter(None)


class TestConcurrency:
    async def test_concurrent_calls_count_exactly(self):
        """The lock must make N concurrent calls consume exactly N slots.

        Without it, two coroutines can both read `count < limit` and both
        proceed, so the limit is advisory under load — which is precisely when
        it matters.
        """
        limiter, _ = build(user=10, workspace=1000)
        await asyncio.gather(
            *(limiter.check(user_id="u1", workspace_id="w1", cost="chat") for _ in range(10))
        )
        with pytest.raises(RateLimited):
            await limiter.check(user_id="u1", workspace_id="w1", cost="chat")
