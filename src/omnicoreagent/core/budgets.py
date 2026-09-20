"""Budgets: what a run, a session, an agent, or an application may spend.

Counters live in the memory store the application chose, beside run state, so
a budget is shared by every worker and survives a restart. Each counter is
keyed by scope, identity, and window (``application:acme:2026-09-20``), and is
written through the same versioned compare-and-swap as run state, so two
workers cannot both spend the last dollar.

Spending that is only known afterwards (a model call) is **reserved** first
and **committed** at its real cost. A process that dies in between leaves the
reservation standing: budgets over-count rather than lose a spend, and the
reservations of a run whose process died are released when its lease expires.

A memory store without the budget methods leaves budgets off: the agent runs
as before, and nothing is counted.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from omnicoreagent.core.logging import logger

# The meters a budget can limit.
METERS = (
    "model_cost_usd",
    "model_tokens",
    "model_calls",
    "tool_calls",
    "sandbox_seconds",
    "subagent_runs",
)
WINDOWS = ("total", "day", "month")
_RETRIES = 8


class BudgetScope(str, Enum):
    REQUEST = "request"
    SESSION = "session"
    AGENT = "agent"
    APPLICATION = "application"


class BudgetExhausted(Exception):
    """The spend would take a meter past its limit."""

    def __init__(
        self,
        *,
        key: str,
        meter: str,
        limit: float,
        used: float,
        reserved: float,
        requested: float,
    ) -> None:
        scope = key.split(":", 1)[0]
        super().__init__(
            f"The {scope} budget for {meter} is exhausted: "
            f"{used + reserved:g} of {limit:g} used, {requested:g} more needed"
        )
        self.key = key
        self.scope = scope
        self.meter = meter
        self.limit = limit
        self.used = used
        self.reserved = reserved
        self.requested = requested

    @property
    def shortfall(self) -> float:
        return max(0.0, self.used + self.reserved + self.requested - self.limit)


@dataclass(frozen=True)
class Reservation:
    """Budget held while a spend happens, until its real cost is known."""

    key: str
    meter: str
    amount: float
    reservation_id: str
    run_id: str | None = None


def window_key(window: str, now: datetime | None = None) -> str:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if window == "day":
        return moment.strftime("%Y-%m-%d")
    if window == "month":
        return moment.strftime("%Y-%m")
    if window == "total":
        return "total"
    raise ValueError(f"window must be one of: {', '.join(WINDOWS)}")


def budget_key(
    scope: BudgetScope | str, identity: str, window: str, now: datetime | None = None
) -> str:
    scope_name = getattr(scope, "value", scope)
    return f"{scope_name}:{identity}:{window_key(window, now)}"


def supports_budgets(store: Any) -> bool:
    """Whether a memory store or router can keep budget counters."""
    return all(
        inspect.iscoroutinefunction(getattr(store, name, None))
        for name in ("get_budget_state", "save_budget_state")
    )


class BudgetLedger:
    """Reads and changes budget counters in the memory store."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.enabled = supports_budgets(store)
        self._lock = asyncio.Lock()

    async def usage(self, key: str) -> dict[str, float]:
        """What has been spent against this budget."""
        state = await self._state(key)
        return dict(state.get("meters") or {})

    async def reserved(self, key: str) -> dict[str, float]:
        """What is held by reservations that have not been committed."""
        state = await self._state(key)
        return _reserved_totals(state)

    async def available(self, key: str, meter: str, *, limit: float | None) -> float:
        if limit is None:
            return float("inf")
        state = await self._state(key)
        spent = float((state.get("meters") or {}).get(meter, 0.0))
        held = _reserved_totals(state).get(meter, 0.0)
        return max(0.0, limit - spent - held)

    async def charge(
        self, key: str, meter: str, amount: float, *, limit: float | None
    ) -> float:
        """Spend now. Raises ``BudgetExhausted`` and spends nothing if it does not fit."""
        if not self.enabled or amount == 0:
            return 0.0

        def change(state: dict[str, Any]) -> float:
            _check(state, key, meter, amount, limit)
            meters = state.setdefault("meters", {})
            meters[meter] = float(meters.get(meter, 0.0)) + float(amount)
            return meters[meter]

        return await self._apply(key, change)

    async def reserve(
        self,
        key: str,
        meter: str,
        amount: float,
        *,
        limit: float | None,
        run_id: str | None = None,
    ) -> Reservation:
        """Hold budget for a spend whose real cost is known only afterwards."""
        reservation = Reservation(key, meter, float(amount), f"hold_{uuid4().hex}", run_id)
        if not self.enabled or amount == 0:
            return reservation

        def change(state: dict[str, Any]) -> None:
            _check(state, key, meter, amount, limit)
            state.setdefault("reservations", {})[reservation.reservation_id] = {
                "meter": meter,
                "amount": float(amount),
                "run_id": run_id,
                "held_at": datetime.now(timezone.utc).isoformat(),
            }

        await self._apply(key, change)
        return reservation

    async def commit(self, reservation: Reservation, *, actual: float | None = None) -> float:
        """Spend the reservation, at its real cost when that is known."""
        if not self.enabled:
            return 0.0
        spend = float(reservation.amount if actual is None else actual)

        def change(state: dict[str, Any]) -> float:
            held = state.setdefault("reservations", {}).pop(reservation.reservation_id, None)
            if held is None and actual is None:
                # Already released (a lease swept it); do not spend twice.
                return float((state.get("meters") or {}).get(reservation.meter, 0.0))
            meters = state.setdefault("meters", {})
            meters[reservation.meter] = float(meters.get(reservation.meter, 0.0)) + spend
            return meters[reservation.meter]

        return await self._apply(reservation.key, change)

    async def release(self, reservation: Reservation) -> None:
        """Give the held budget back; nothing is spent."""
        if not self.enabled:
            return

        def change(state: dict[str, Any]) -> None:
            state.setdefault("reservations", {}).pop(reservation.reservation_id, None)

        await self._apply(reservation.key, change)

    async def release_for_runs(self, key: str, *, run_ids: list[str]) -> int:
        """Release what runs that are no longer alive were holding."""
        if not self.enabled:
            return 0
        wanted = set(run_ids)

        def change(state: dict[str, Any]) -> int:
            reservations = state.setdefault("reservations", {})
            stale = [
                held_id
                for held_id, held in reservations.items()
                if held.get("run_id") in wanted
            ]
            for held_id in stale:
                reservations.pop(held_id)
            return len(stale)

        return await self._apply(key, change)

    # --- storage ---------------------------------------------------------

    async def _state(self, key: str) -> dict[str, Any]:
        if not self.enabled:
            return {}
        try:
            return await self.store.get_budget_state(key) or {}
        except Exception as exc:
            if not self._is_unsupported(exc):
                raise
            return {}

    def _is_unsupported(self, exc: Exception) -> bool:
        """A store that keeps no budgets: leave budgets off rather than fail."""
        from omnicoreagent.core.runs import RunStateUnsupported

        if isinstance(exc, RunStateUnsupported):
            self.enabled = False
            logger.debug("The memory store keeps no budgets; budgets are not counted")
            return True
        return False

    async def _apply(self, key: str, change) -> Any:
        """Read, change, and save, retrying when another worker got there first."""
        if not self.enabled:
            return None
        async with self._lock:  # one in-flight change per ledger object
            for _ in range(_RETRIES):
                try:
                    state = await self.store.get_budget_state(key) or {"key": key}
                except Exception as exc:
                    if self._is_unsupported(exc):
                        return None
                    raise
                version = state.pop("version", None)
                result = change(state)
                try:
                    await self.store.save_budget_state(state, expected_version=version)
                    return result
                except Exception as exc:
                    if self._is_unsupported(exc):
                        return None
                    if type(exc).__name__ != "RunStateConflict":
                        raise
                    await asyncio.sleep(0.005)
            logger.warning(f"Budget {key} is changing too fast to record")
            raise RuntimeError(f"Could not record the budget change for {key}")


def _reserved_totals(state: dict[str, Any]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for held in (state.get("reservations") or {}).values():
        meter = held["meter"]
        totals[meter] = totals.get(meter, 0.0) + float(held["amount"])
    return totals


def _check(
    state: dict[str, Any], key: str, meter: str, amount: float, limit: float | None
) -> None:
    if limit is None:
        return
    used = float((state.get("meters") or {}).get(meter, 0.0))
    held = _reserved_totals(state).get(meter, 0.0)
    if used + held + float(amount) > float(limit):
        raise BudgetExhausted(
            key=key, meter=meter, limit=float(limit), used=used, reserved=held, requested=float(amount)
        )
