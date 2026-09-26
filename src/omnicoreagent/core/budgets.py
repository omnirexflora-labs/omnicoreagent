"""Budgets: what a run, a session, an agent, or an application may spend.

Counters live in the memory store the application chose, beside run state, so
a budget is shared by every worker and survives a restart. Each counter is
keyed by scope, identity, and window (``application:acme:2026-09-20``), and is
written through the same versioned compare-and-swap as run state, so two
workers cannot both spend the last dollar.

Spending that is only known afterwards (a model call) is **reserved** first
and **committed** at its real cost. A process that dies in between leaves the
reservation standing: budgets over-count rather than lose a spend, and what
the dead attempt held is released when its run is resumed, recovered, retried
or ended from outside (``RunBudgets.release_stale``).

A memory store without the budget methods leaves budgets off: the agent runs
as before, and nothing is counted.
"""

from __future__ import annotations

import asyncio
import inspect
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from contextvars import ContextVar

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
# What a call could return when the model config sets no ceiling of its own.
DEFAULT_ASSUMED_OUTPUT_TOKENS = 4096
# A change that loses the race to another worker is tried again, backing off
# a little more each time (with jitter, so two workers do not keep
# colliding): a charge is a tiny read-modify-write, and a run whose charge
# cannot be recorded cannot go on, so it waits rather than gives up early.
_RETRIES = 60
_BACKOFF_SECONDS = 0.005
_BACKOFF_CAP_SECONDS = 0.05
# Grants kept on the counter itself; the trace holds the full story.
_GRANT_HISTORY_KEPT = 20


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
        totals = await self.charge_many(key, [(meter, amount, limit)])
        return totals.get(meter, 0.0)

    async def charge_many(
        self, key: str, charges: list[tuple[str, float, float | None]]
    ) -> dict[str, float]:
        """Spend on several meters of one key in one read-modify-write.

        Every charge is checked before any is applied, so a refusal spends
        nothing on any meter. Returns each charged meter's new total. On a
        remote store this is one round trip where one per meter was.
        """
        if not self.enabled:
            return {}
        wanted = [(meter, float(amount), limit) for meter, amount, limit in charges if amount]
        if not wanted:
            return {}

        def change(state: dict[str, Any]) -> dict[str, float]:
            for meter, amount, limit in wanted:
                _check(state, key, meter, amount, limit)
            meters = state.setdefault("meters", {})
            totals: dict[str, float] = {}
            for meter, amount, _ in wanted:
                meters[meter] = float(meters.get(meter, 0.0)) + amount
                totals[meter] = meters[meter]
            return totals

        return await self._apply(key, change) or {}

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
        reservation, _ = await self.reserve_and_charge(
            key, meter, amount, limit=limit, run_id=run_id
        )
        return reservation

    async def reserve_and_charge(
        self,
        key: str,
        meter: str,
        amount: float,
        *,
        limit: float | None,
        run_id: str | None = None,
        also: list[tuple[str, float, float | None]] | None = None,
    ) -> tuple[Reservation, dict[str, float]]:
        """Hold budget for one meter and charge others, in one write.

        A model call holds its cost and counts itself at once. Returns the
        reservation and the new totals of what ``also`` charged.
        """
        reservation = Reservation(key, meter, float(amount), f"hold_{uuid4().hex}", run_id)
        extra = [(m, float(a), lim) for m, a, lim in (also or []) if a]
        if not self.enabled or (amount == 0 and not extra):
            return reservation, {}

        def change(state: dict[str, Any]) -> dict[str, float]:
            if amount:
                _check(state, key, meter, amount, limit)
            for other, other_amount, other_limit in extra:
                _check(state, key, other, other_amount, other_limit)
            if amount:
                state.setdefault("reservations", {})[reservation.reservation_id] = {
                    "meter": meter,
                    "amount": float(amount),
                    "run_id": run_id,
                    "held_at": datetime.now(timezone.utc).isoformat(),
                }
            meters = state.setdefault("meters", {})
            totals: dict[str, float] = {}
            for other, other_amount, _ in extra:
                meters[other] = float(meters.get(other, 0.0)) + other_amount
                totals[other] = meters[other]
            return totals

        totals = await self._apply(key, change) or {}
        return reservation, totals

    async def commit(
        self,
        reservation: Reservation,
        *,
        actual: float | None = None,
        also: list[tuple[str, float, float | None]] | None = None,
    ) -> dict[str, float]:
        """Spend the reservation, at its real cost when that is known.

        ``also`` charges other meters of the same key in the same write (the
        tokens a call used are counted as its cost is settled). Returns the
        new totals of the settled meter and of what ``also`` charged.
        """
        if not self.enabled:
            return {}
        spend = float(reservation.amount if actual is None else actual)
        extra = [(m, float(a), lim) for m, a, lim in (also or []) if a]

        def change(state: dict[str, Any]) -> dict[str, float]:
            for other, other_amount, other_limit in extra:
                _check(state, reservation.key, other, other_amount, other_limit)
            meters = state.setdefault("meters", {})
            held = state.setdefault("reservations", {}).pop(reservation.reservation_id, None)
            if not (held is None and actual is None):
                # Not already released by a lease sweep: spend it.
                meters[reservation.meter] = float(meters.get(reservation.meter, 0.0)) + spend
            totals = {reservation.meter: float(meters.get(reservation.meter, 0.0))}
            for other, other_amount, _ in extra:
                meters[other] = float(meters.get(other, 0.0)) + other_amount
                totals[other] = meters[other]
            return totals

        return await self._apply(reservation.key, change) or {}

    async def release(self, reservation: Reservation) -> None:
        """Give the held budget back; nothing is spent."""
        if not self.enabled:
            return

        def change(state: dict[str, Any]) -> None:
            state.setdefault("reservations", {}).pop(reservation.reservation_id, None)

        await self._apply(reservation.key, change)

    async def granted(self, key: str) -> dict[str, float]:
        """What a person has added to this budget beyond its policy limit."""
        state = await self._state(key)
        return dict(state.get("grants") or {})

    async def grant_history(self, key: str) -> list[dict[str, Any]]:
        """Who granted what, and when."""
        state = await self._state(key)
        return list(state.get("grant_history") or [])

    async def grant(
        self,
        key: str,
        meter: str,
        amount: float,
        *,
        approver: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Add to one budget, once, on a person's authority.

        The policy is not changed: its limit still says what it said. This is
        a recorded exception to one counter, with the name of whoever made it,
        so the run can finish the work it has already partly done.
        """
        if not self.enabled:
            return {}
        entry = {
            "meter": meter,
            "amount": float(amount),
            "approver": approver,
            "note": note,
            "granted_at": datetime.now(timezone.utc).isoformat(),
        }

        def change(state: dict[str, Any]) -> dict[str, Any]:
            grants = state.setdefault("grants", {})
            grants[meter] = float(grants.get(meter, 0.0)) + float(amount)
            history = state.setdefault("grant_history", [])
            history.append(entry)
            # Keep the record bounded; the trace holds the full story.
            del history[:-_GRANT_HISTORY_KEPT]
            return entry

        return await self._apply(key, change)

    async def delete(self, key: str) -> None:
        """Remove one counter: a finished request's own, once its spend is on
        the run's record. A store that keeps no budgets has nothing to remove."""
        if not self.enabled:
            return
        try:
            await self.store.delete_budget_state(key)
        except Exception as exc:
            if not self._is_unsupported(exc):
                raise

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
            for attempt in range(_RETRIES):
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
                    pause = min(_BACKOFF_SECONDS * (1.5 ** attempt), _BACKOFF_CAP_SECONDS)
                    await asyncio.sleep(pause * (0.5 + random.random()))
            logger.warning(f"Budget {key} is changing too fast to record")
            raise RuntimeError(f"Could not record the budget change for {key}")


# --- what a model call could cost, before it is made -------------------------


@dataclass(frozen=True)
class ModelCallEstimate:
    """The most one model call could cost, priced before it is made.

    The input is counted from the messages that were just assembled, so it is
    exact. The output is not known, but it cannot exceed ``max_tokens``, so
    that is what is priced. Without ``max_tokens``, DEFAULT_ASSUMED_OUTPUT_TOKENS
    is priced and sent as the call's ceiling (``OUTPUT_TOKEN_CEILING``), so
    the output is never an under-count either way. ``cost_usd`` is
    ``None`` when the model has no published price, and then tokens and calls
    are what govern the run.
    """

    input_tokens: int
    output_tokens: int
    cost_usd: float | None


def estimate_model_call(
    llm_connection: Any, messages: Any, *, max_output_tokens: int | None
) -> ModelCallEstimate:
    # Imported here: building an agent should not load the tokenizer or the
    # usage types when nothing is budgeted.
    from omnicoreagent.core.summarizer.tokenizer import count_tokens
    from omnicoreagent.core.token_usage import Usage

    input_tokens = 0
    for message in messages or ():
        try:
            input_tokens += count_tokens(_render_for_counting(message))
        except Exception:  # a message shape the counter cannot render
            continue
    output_tokens = int(max_output_tokens or DEFAULT_ASSUMED_OUTPUT_TOKENS)
    estimate = getattr(llm_connection, "estimate_cost", None)
    cost = None
    if callable(estimate):
        try:
            cost = estimate(
                Usage(
                    requests=1,
                    request_tokens=input_tokens,
                    response_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                )
            )
        except Exception:
            cost = None
    return ModelCallEstimate(input_tokens, output_tokens, cost)


def _render_for_counting(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    if isinstance(content, str):
        return content
    return str(content or "")


# --- the budgets covering one run --------------------------------------------


class BudgetExhaustedForRun(Exception):
    """A budget covering this run is spent; the run stops."""

    def __init__(self, exhausted: BudgetExhausted, action: str) -> None:
        super().__init__(str(exhausted))
        self.exhausted = exhausted
        self.action = action

    @property
    def scope(self) -> str:
        return self.exhausted.scope


class RunAwaitingBudget(Exception):
    """The run cannot afford its next step and is waiting for a person.

    It carries what a person needs in order to decide: which budget ran out,
    by how much, and the identifier to answer with.
    """

    def __init__(self, request: dict[str, Any]) -> None:
        super().__init__(
            f"The {request['scope']} budget for {request['meter']} is exhausted: "
            f"{request['shortfall']:g} more is needed to continue"
        )
        self.request = request
        self.usage: Any = None


class RunBudgets:
    """Charges what a run spends to every budget that covers it.

    A request charges its own budget, its session's, its agent's, and the
    application's; any exhausted level stops the work, and the run says which
    one. With nothing budgeted this costs nothing: no key is read or written.
    """

    def __init__(
        self,
        ledger: BudgetLedger,
        budgets: Any,
        *,
        run_id: str,
        session_id: str | None = None,
        agent_name: str | None = None,
        telemetry_recorder: Any = None,
        refused: set[tuple[str, str]] | None = None,
    ) -> None:
        self.ledger = ledger
        self.budgets = budgets
        self.telemetry_recorder = telemetry_recorder
        self.identities = {
            BudgetScope.REQUEST: run_id,
            BudgetScope.SESSION: session_id,
            BudgetScope.AGENT: agent_name,
            BudgetScope.APPLICATION: getattr(budgets, "application_id", None),
        }
        self.run_id = run_id
        # Budgets a person has already refused for this run: asking again
        # would be asking the same person the same question.
        self.refused = refused or set()
        self._warned: set[tuple[str, str]] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.budgets) and self.ledger.enabled

    def limits(self, meter: str) -> list[tuple[BudgetScope, str, Any]]:
        """Every (scope, key, limit) that governs this meter for this run."""
        if not self.enabled:
            return []
        governing = []
        for scope in BudgetScope:
            identity = self.identities.get(scope)
            if not identity:
                continue
            for limit in self.budgets.limits_for(scope.value):
                if limit.meter != meter:
                    continue
                governing.append((scope, budget_key(scope, identity, limit.window), limit))
        return governing

    async def charge(self, meter: str, amount: float) -> None:
        """Spend against every budget that covers this run."""
        await self.charge_many([(meter, amount)])

    async def charge_many(self, charges: list[tuple[str, float]]) -> None:
        """Spend several meters at once: one write per budget key they share."""
        by_key: dict[str, list[tuple[BudgetScope, Any, float]]] = {}
        for meter, amount in charges:
            for scope, key, limit in self.limits(meter):
                by_key.setdefault(key, []).append((scope, limit, float(amount)))
        for key, entries in by_key.items():
            try:
                totals = await self.ledger.charge_many(
                    key, [(limit.meter, amount, limit.limit) for _, limit, amount in entries]
                )
            except BudgetExhausted as exhausted:
                scope, limit = next(
                    (scope, limit) for scope, limit, _ in entries if limit.meter == exhausted.meter
                )
                raise await self._stop(scope, key, limit, exhausted) from None
            for scope, limit, _ in entries:
                await self._warn_if_near(scope, limit, key, totals.get(limit.meter))

    async def reserve(
        self, meter: str, amount: float, *, also: list[tuple[str, float]] | None = None
    ) -> list[Reservation]:
        """Hold what a spend could cost, on every budget that covers the run.

        ``also`` charges other meters in the same writes (a model call holds
        its cost and counts itself at once).
        """
        held: list[Reservation] = []
        extra_by_key: dict[str, list[tuple[BudgetScope, Any, float]]] = {}
        for other, other_amount in also or []:
            for scope, key, limit in self.limits(other):
                extra_by_key.setdefault(key, []).append((scope, limit, float(other_amount)))
        governing = self.limits(meter)
        keys_with_hold = {key for _, key, _ in governing}
        for scope, key, limit in governing:
            extra = extra_by_key.pop(key, [])
            try:
                reservation, totals = await self.ledger.reserve_and_charge(
                    key,
                    meter,
                    amount,
                    limit=limit.limit,
                    run_id=self.run_id,
                    also=[(lim.meter, a, lim.limit) for _, lim, a in extra],
                )
            except BudgetExhausted as exhausted:
                await self.release(held)  # hold nothing when the call cannot run
                if exhausted.meter != meter:
                    scope, limit = next(
                        (s, lim) for s, lim, _ in extra if lim.meter == exhausted.meter
                    )
                raise await self._stop(scope, key, limit, exhausted) from None
            held.append(reservation)
            for other_scope, other_limit, _ in extra:
                await self._warn_if_near(
                    other_scope, other_limit, key, totals.get(other_limit.meter)
                )
        # Meters in ``also`` whose keys hold nothing are charged on their own.
        for key, entries in extra_by_key.items():
            if key in keys_with_hold:
                continue
            try:
                totals = await self.ledger.charge_many(
                    key, [(lim.meter, a, lim.limit) for _, lim, a in entries]
                )
            except BudgetExhausted as exhausted:
                await self.release(held)
                scope, limit = next(
                    (s, lim) for s, lim, _ in entries if lim.meter == exhausted.meter
                )
                raise await self._stop(scope, key, limit, exhausted) from None
            for other_scope, other_limit, _ in entries:
                await self._warn_if_near(
                    other_scope, other_limit, key, totals.get(other_limit.meter)
                )
        return held

    async def commit(
        self,
        held: list[Reservation],
        *,
        actual: float,
        also: list[tuple[str, float]] | None = None,
    ) -> None:
        """Settle each hold at its real cost; ``also`` counts other meters in
        the same writes. The totals the writes return are what is checked for
        warnings, rather than reading the key back."""
        extra_by_key: dict[str, list[tuple[BudgetScope, Any, float]]] = {}
        for other, other_amount in also or []:
            for scope, key, limit in self.limits(other):
                extra_by_key.setdefault(key, []).append((scope, limit, float(other_amount)))
        governing = {key: (scope, limit) for scope, key, limit in self.limits(held[0].meter)} if held else {}
        for reservation in held:
            extra = extra_by_key.pop(reservation.key, [])
            try:
                totals = await self.ledger.commit(
                    reservation,
                    actual=actual,
                    also=[(lim.meter, a, lim.limit) for _, lim, a in extra],
                )
            except BudgetExhausted as exhausted:
                scope, limit = next((s, lim) for s, lim, _ in extra if lim.meter == exhausted.meter)
                raise await self._stop(scope, reservation.key, limit, exhausted) from None
            if reservation.key in governing:
                scope, limit = governing[reservation.key]
                await self._warn_if_near(scope, limit, reservation.key, totals.get(limit.meter))
            for other_scope, other_limit, _ in extra:
                await self._warn_if_near(
                    other_scope, other_limit, reservation.key, totals.get(other_limit.meter)
                )
        for key, entries in extra_by_key.items():
            try:
                totals = await self.ledger.charge_many(
                    key, [(lim.meter, a, lim.limit) for _, lim, a in entries]
                )
            except BudgetExhausted as exhausted:
                scope, limit = next((s, lim) for s, lim, _ in entries if lim.meter == exhausted.meter)
                raise await self._stop(scope, key, limit, exhausted) from None
            for other_scope, other_limit, _ in entries:
                await self._warn_if_near(
                    other_scope, other_limit, key, totals.get(other_limit.meter)
                )

    async def release(self, held: list[Reservation]) -> None:
        for reservation in held:
            await self.ledger.release(reservation)

    async def release_stale(self) -> int:
        """Release what this run held before it went on or ended.

        Only one attempt of a run holds its lease, so a hold under this run's
        id from before is what an attempt that died left standing. Without
        this, a run killed during a model call held its worst case on the
        day's counter until the day ended.
        """
        released = 0
        seen: set[str] = set()
        for meter in METERS:
            for _, key, _ in self.limits(meter):
                if key in seen:
                    continue
                seen.add(key)
                released += await self.ledger.release_for_runs(key, run_ids=[self.run_id])
        return released

    async def settle(self) -> dict[str, dict[str, float]]:
        """The run is over: return what it spent per scope, and remove its own
        counters.

        Measured over 400 requests, every request left its request-scope key in
        the store for good — one key per request on a durable store, never
        expired. The request's spend goes on its record; session, agent and
        application counters are shared and stay. A run that is only paused
        must not settle: a top-up lands on its counter.
        """
        spent = await self.spent()
        seen: set[str] = set()
        for meter in METERS:
            for scope, key, _ in self.limits(meter):
                if scope is BudgetScope.REQUEST and key not in seen:
                    seen.add(key)
                    await self.ledger.delete(key)
        return spent

    async def spent(self) -> dict[str, dict[str, float]]:
        """What this run has spent, per scope, for the run's totals."""
        totals: dict[str, dict[str, float]] = {}
        seen: set[str] = set()
        for meter in METERS:
            for scope, key, _ in self.limits(meter):
                if key in seen:
                    continue
                seen.add(key)
                usage = await self.ledger.usage(key)
                if usage:
                    totals[scope.value] = usage
        return totals

    async def _warn_if_near(self, scope: BudgetScope, limit: Any, key: str, spent: Any) -> None:
        if spent is None or float(spent) < limit.warn_at * limit.limit:
            return
        if (key, limit.meter) in self._warned:
            return
        self._warned.add((key, limit.meter))
        await self._emit(
            "budget_warning",
            {
                "scope": scope.value,
                "meter": limit.meter,
                "limit": limit.limit,
                "used": float(spent),
                "remaining": max(0.0, limit.limit - float(spent)),
                "window": limit.window,
            },
        )

    async def _stop(
        self, scope: BudgetScope, key: str, limit: Any, exhausted: BudgetExhausted
    ) -> Exception:
        """What to raise when a budget runs out: wait for a person, or end."""
        await self._record_exhausted(scope, limit, exhausted)
        if limit.on_exhausted != "pause" or (key, limit.meter) in self.refused:
            return BudgetExhaustedForRun(exhausted, limit.on_exhausted)
        request = {
            "request_id": f"budgetreq_{uuid4().hex}",
            "key": key,
            "scope": scope.value,
            "meter": limit.meter,
            "window": limit.window,
            "limit": limit.limit,
            "used": exhausted.used + exhausted.reserved,
            "needed": exhausted.requested,
            "shortfall": exhausted.shortfall,
            "status": "pending",
            "asked_at": datetime.now(timezone.utc).isoformat(),
        }
        from omnicoreagent.core.runs import current_run

        run = current_run()
        if run is not None:
            await run.add_budget_request(request)
        return RunAwaitingBudget(request)

    async def _record_exhausted(
        self, scope: BudgetScope, limit: Any, exhausted: BudgetExhausted
    ) -> None:
        await self._emit(
            "budget_exhausted",
            {
                "scope": scope.value,
                "meter": limit.meter,
                "limit": limit.limit,
                "used": exhausted.used + exhausted.reserved,
                "needed": exhausted.requested,
                "shortfall": exhausted.shortfall,
                "window": limit.window,
                "on_exhausted": limit.on_exhausted,
            },
        )

    async def _emit(self, event: str, metadata: dict[str, Any]) -> None:
        if self.telemetry_recorder is None:
            return
        try:
            await self.telemetry_recorder.emit_event(event, metadata=metadata)
        except Exception:  # a budget is not worth failing a run over telemetry
            logger.debug(f"Could not record {event}")


_CURRENT_BUDGETS: ContextVar[RunBudgets | None] = ContextVar(
    "omnicoreagent_run_budgets", default=None
)


def current_budgets() -> RunBudgets | None:
    """The budgets covering the run this code is part of, if any."""
    return _CURRENT_BUDGETS.get()


@asynccontextmanager
async def active_budgets(budgets: RunBudgets | None):
    token = _CURRENT_BUDGETS.set(budgets)
    try:
        yield budgets
    finally:
        _CURRENT_BUDGETS.reset(token)


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
    # What a person has added to this budget counts as part of it.
    limit = float(limit) + float((state.get("grants") or {}).get(meter, 0.0))
    if used + held + float(amount) > float(limit):
        raise BudgetExhausted(
            key=key, meter=meter, limit=float(limit), used=used, reserved=held, requested=float(amount)
        )
