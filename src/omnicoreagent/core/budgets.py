"""Budgets: what a run, a session, an agent, or an application may spend.

Counters live in the memory store the application chose, beside run state, so
a budget is shared by every worker and survives a restart. Each counter is
keyed by scope, identity, and window (``application:acme:2026-09-20``). Every
change to one is a single atomic operation inside the store (an increment
guarded by the limit, in one statement or one script), never a read, a change
in Python and a write back, so two workers cannot both spend the last dollar
and a crowd of runs on one application key cannot starve each other: the
support desk ramp (2026-10-07) lost 46 of 100 concurrent runs to the old
versioned write on one shared row. A hold is a record of its own, so there is
no growing document to rewrite.

Spending that is only known afterwards (a model call) is **reserved** first
and **committed** at its real cost. A process that dies in between leaves the
reservation standing, so budgets over-count until what the dead attempt held
is released when its run is resumed, recovered, retried or ended from outside
(``RunBudgets.release_stale``). Released, that call's real cost was never
known, so it is not recorded: the call is counted, its tokens and cost are not.

A memory store without the budget methods leaves budgets off: the agent runs
as before, and nothing is counted.
"""

from __future__ import annotations

import asyncio
import inspect
import random
import time
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
# What each meter counts; the budgets reference is generated from this.
METER_DESCRIPTIONS = {
    "model_cost_usd": "Dollars spent on model calls, priced from the provider's published rates. Each call's price is held before it is made.",
    "model_tokens": "Tokens in and out of model calls.",
    "model_calls": "Model calls.",
    "tool_calls": "Tool calls, counted once, when each runs (a call waiting for approval is not counted until it does).",
    "sandbox_seconds": "Seconds sandbox sessions were open.",
    "subagent_runs": "Workers started with spawn_subagents.",
}
# What each window means: its counter starts again at each UTC day or month.
WINDOW_DESCRIPTIONS = {
    "total": "Never resets: the whole life of the scope (a request, a session...).",
    "day": "Resets at 00:00 UTC.",
    "month": "Resets on the first of the month, 00:00 UTC.",
}
# What a call could return when the model config sets no ceiling of its own.
DEFAULT_ASSUMED_OUTPUT_TOKENS = 4096
# A charge for work that already happened is never allowed to fail the run:
# if the store is briefly unreachable it is tried again, backing off (with
# jitter) for this long, and only then kept on the run's record as unrecorded.
# The support desk ramp failed runs that had already issued a refund because
# the charge after the call could not be written.
_UNRECORDED_AFTER_SECONDS = 15.0
_BACKOFF_SECONDS = 0.05
_BACKOFF_CAP_SECONDS = 1.0
# Float arithmetic on holds leaves dust (0.1 + 0.2 - 0.3); smaller than this
# is nothing held.
_HELD_DUST = 1e-9
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
        for name in ("get_budget_state", "apply_budget_change")
    )


class BudgetLedger:
    """Reads and changes budget counters in the memory store.

    Every change is handed to the store as one description
    (``apply_budget_change``) and applied there atomically; see the store
    contract in ``AbstractMemoryStore``.
    """

    def __init__(self, store: Any) -> None:
        self.store = store
        self.enabled = supports_budgets(store)

    async def usage(self, key: str) -> dict[str, float]:
        """What has been spent against this budget."""
        state = await self._state(key)
        return dict(state.get("meters") or {})

    async def usage_and_grants(self, key: str) -> tuple[dict[str, float], dict[str, float]]:
        """What has been spent and what a person granted, from one read."""
        state = await self._state(key)
        return dict(state.get("meters") or {}), dict(state.get("grants") or {})

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
        """Spend on several meters of one key in one atomic change.

        Every charge is checked before any is applied, so a refusal spends
        nothing on any meter. Returns each charged meter's new total.
        """
        if not self.enabled:
            return {}
        wanted = [[meter, float(amount), limit] for meter, amount, limit in charges if amount]
        if not wanted:
            return {}
        result = await self._apply(key, {"guard": wanted})
        return _totals_or_refusal(key, result)

    async def record_many(
        self, key: str, charges: list[tuple[str, float]]
    ) -> dict[str, float]:
        """Record what was already spent (a call's tokens, once it answered).

        Never refused: the spend happened. Returns each meter's new total.
        """
        if not self.enabled:
            return {}
        wanted = [[meter, float(amount)] for meter, amount in charges if amount]
        if not wanted:
            return {}
        result = await self._apply(key, {"add": wanted})
        return dict((result or {}).get("totals") or {})

    async def check(self, key: str, meter: str, amount: float, *, limit: float | None) -> None:
        """Raise ``BudgetExhausted`` if ``amount`` does not fit; change nothing."""
        if not self.enabled or limit is None:
            return
        _check(await self._state(key), key, meter, float(amount), limit)

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
        """Hold budget for one meter and charge others, in one atomic change.

        A model call holds its cost and counts itself at once. Returns the
        reservation and the new totals of what ``also`` charged. The hold is a
        record of its own (id, meter, amount, run, time), removed when the
        call settles or is released.
        """
        reservation = Reservation(key, meter, float(amount), f"hold_{uuid4().hex}", run_id)
        extra = [[m, float(a), lim] for m, a, lim in (also or []) if a]
        if not self.enabled or (amount == 0 and not extra):
            return reservation, {}
        change: dict[str, Any] = {"guard": extra}
        if amount:
            change["hold"] = {
                "id": reservation.reservation_id,
                "meter": meter,
                "amount": float(amount),
                "run_id": run_id,
                "limit": limit,
                "held_at": datetime.now(timezone.utc).isoformat(),
            }
        result = await self._apply(key, change)
        return reservation, _totals_or_refusal(key, result)

    async def commit(
        self,
        reservation: Reservation,
        *,
        actual: float | None = None,
        also: list[tuple[str, float, float | None]] | None = None,
    ) -> dict[str, float]:
        """Spend the reservation, at its real cost when that is known.

        ``also`` charges other meters of the same key in the same change (the
        tokens a call used are counted as its cost is settled). Returns the
        new totals of the settled meter and of what ``also`` charged.
        Settling is never refused: the spend already happened, so it is
        recorded even past a limit, and the next spend is what is stopped.
        """
        if not self.enabled:
            return {}
        spend = float(reservation.amount if actual is None else actual)
        extra = [[m, float(a)] for m, a, _ in (also or []) if a]
        result = await self._apply(
            reservation.key,
            {
                "settle": {
                    "id": reservation.reservation_id,
                    "meter": reservation.meter,
                    "spend": spend,
                    # A hold already released by a lease sweep is not spent
                    # again, unless the real cost is known: that call happened.
                    "even_if_released": actual is not None,
                },
                "add": extra,
            },
        )
        return dict((result or {}).get("totals") or {})

    async def release(self, reservation: Reservation) -> None:
        """Give the held budget back; nothing is spent."""
        if not self.enabled:
            return
        await self._apply(
            reservation.key,
            {
                "settle": {
                    "id": reservation.reservation_id,
                    "meter": reservation.meter,
                    "spend": None,
                    "even_if_released": False,
                }
            },
        )

    async def granted(self, key: str) -> dict[str, float]:
        """What a person has added to this budget beyond its policy limit."""
        state = await self._state(key)
        return dict(state.get("grants") or {})

    async def grant_history(self, key: str) -> list[dict[str, Any]]:
        """Who granted what, and when."""
        if not self.enabled:
            return []
        try:
            return list(await self.store.get_budget_grant_history(key))
        except Exception as exc:
            if not self._is_unsupported(exc):
                raise
            return []

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
        await self._apply(key, {"grant": entry})
        return entry

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
        if not self.enabled or not run_ids:
            return 0
        result = await self._apply(key, {"release_runs": list(run_ids)})
        return int((result or {}).get("released") or 0)

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

    async def _apply(self, key: str, change: dict[str, Any]) -> dict[str, Any] | None:
        """Hand one change to the store, which applies it atomically."""
        if not self.enabled:
            return None
        try:
            return await self.store.apply_budget_change(key, change)
        except Exception as exc:
            if self._is_unsupported(exc):
                return None
            raise


def _totals_or_refusal(key: str, result: dict[str, Any] | None) -> dict[str, float]:
    refused = (result or {}).get("refused")
    if refused:
        raise BudgetExhausted(
            key=key,
            meter=refused["meter"],
            limit=float(refused["limit"]),
            used=float(refused["used"]),
            reserved=float(refused["reserved"]),
            requested=float(refused["requested"]),
        )
    return dict((result or {}).get("totals") or {})


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
        # Charges for work that already happened and could not be written to
        # the store, even after waiting (see ``_after_the_work``). They stay on
        # the run's record, in its trace and in its budget status.
        self.unrecorded: list[dict[str, Any]] = []
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

    async def charge_after_the_work(self, meter: str, amount: float) -> None:
        """Spend for work that already happened (the seconds a sandbox ran).

        Over a limit it still stops the run, as any charge does. But if the
        store cannot take the charge it is waited for, and then kept as
        unrecorded: work that was done is never undone by a broken store.
        """
        await self.charge_many([(meter, amount)], after_the_work=True)

    async def charge_many(
        self, charges: list[tuple[str, float]], *, after_the_work: bool = False
    ) -> None:
        """Spend several meters at once: one write per budget key they share."""
        by_key: dict[str, list[tuple[BudgetScope, Any, float]]] = {}
        for meter, amount in charges:
            for scope, key, limit in self.limits(meter):
                by_key.setdefault(key, []).append((scope, limit, float(amount)))
        for key, entries in by_key.items():
            wanted = [(limit.meter, amount, limit.limit) for _, limit, amount in entries]
            try:
                if after_the_work:
                    totals = await self._after_the_work(
                        lambda: self.ledger.charge_many(key, wanted),
                        [self._unrecorded_entry(s, key, lim.meter, a) for s, lim, a in entries],
                    )
                    if totals is None:
                        continue
                else:
                    totals = await self.ledger.charge_many(key, wanted)
            except BudgetExhausted as exhausted:
                scope, limit = next(
                    (scope, limit) for scope, limit, _ in entries if limit.meter == exhausted.meter
                )
                raise await self._stop(scope, key, limit, exhausted) from None
            for scope, limit, _ in entries:
                await self._warn_if_near(scope, limit, key, totals.get(limit.meter))

    async def record_many(self, charges: list[tuple[str, float]]) -> None:
        """Record what was already spent, on every budget that covers the run.

        Never refused, even past a limit: the next spend is what is stopped
        (``check_room``). Crossing a warning line is still reported.
        """
        by_key: dict[str, list[tuple[BudgetScope, Any, float]]] = {}
        for meter, amount in charges:
            for scope, key, limit in self.limits(meter):
                by_key.setdefault(key, []).append((scope, limit, float(amount)))
        for key, entries in by_key.items():
            totals = await self._after_the_work(
                lambda: self.ledger.record_many(
                    key, [(limit.meter, amount) for _, limit, amount in entries]
                ),
                [self._unrecorded_entry(s, key, lim.meter, a) for s, lim, a in entries],
            )
            if totals is None:
                continue
            for scope, limit, _ in entries:
                await self._warn_if_near(scope, limit, key, totals.get(limit.meter))

    async def check_room(self, meter: str, amount: float) -> None:
        """Stop before a spend that cannot fit, on every budget that covers
        the run: a model call's input tokens against a token budget, which is
        otherwise counted only once the call has answered."""
        for scope, key, limit in self.limits(meter):
            try:
                await self.ledger.check(key, meter, amount, limit=limit.limit)
            except BudgetExhausted as exhausted:
                raise await self._stop(scope, key, limit, exhausted) from None

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
            totals = await self._after_the_work(
                lambda: self.ledger.commit(
                    reservation,
                    actual=actual,
                    also=[(lim.meter, a, lim.limit) for _, lim, a in extra],
                ),
                [
                    self._unrecorded_entry(
                        governing[reservation.key][0]
                        if reservation.key in governing
                        else BudgetScope(reservation.key.split(":", 1)[0]),
                        reservation.key,
                        reservation.meter,
                        actual,
                    ),
                    *[self._unrecorded_entry(s, reservation.key, lim.meter, a) for s, lim, a in extra],
                ],
            )
            if totals is None:
                continue
            if reservation.key in governing:
                scope, limit = governing[reservation.key]
                await self._warn_if_near(scope, limit, reservation.key, totals.get(limit.meter))
            for other_scope, other_limit, _ in extra:
                await self._warn_if_near(
                    other_scope, other_limit, reservation.key, totals.get(other_limit.meter)
                )
        for key, entries in extra_by_key.items():
            totals = await self._after_the_work(
                lambda: self.ledger.record_many(key, [(lim.meter, a) for _, lim, a in entries]),
                [self._unrecorded_entry(s, key, lim.meter, a) for s, lim, a in entries],
            )
            if totals is None:
                continue
            for other_scope, other_limit, _ in entries:
                await self._warn_if_near(
                    other_scope, other_limit, key, totals.get(other_limit.meter)
                )

    async def release(self, held: list[Reservation]) -> None:
        """Give holds back. A hold that cannot be given back now is left for
        ``release_stale`` (the run's end does it): this runs while a failed
        call is being reported, and must not replace that report."""
        for reservation in held:
            try:
                await self.ledger.release(reservation)
            except Exception as exc:
                logger.warning(
                    f"Could not release a budget hold on {reservation.key} "
                    f"({type(exc).__name__}: {exc}); it is released when the run ends"
                )

    async def _after_the_work(self, call: Any, entries: list[dict[str, Any]]) -> Any:
        """Run a store write for work that already happened, and never fail.

        A model call or a tool call has been made; failing the run because its
        charge could not be written would not undo it. The support desk ramp
        (2026-10-07) failed runs this way after their refund had been issued.
        So the write is tried again, backing off, for ``_UNRECORDED_AFTER_SECONDS``;
        and if the store is still not there, the charge is kept as unrecorded
        (``unrecorded``, the trace, the run record) and ``None`` is returned.
        A limit is the budget doing its job, and is passed on.
        """
        deadline = time.monotonic() + _UNRECORDED_AFTER_SECONDS
        delay = _BACKOFF_SECONDS
        while True:
            try:
                return await call()
            except (BudgetExhausted, asyncio.CancelledError):
                raise
            except Exception as exc:
                if time.monotonic() + delay >= deadline:
                    await self._note_unrecorded(entries, exc)
                    return None
                await asyncio.sleep(delay * (0.5 + random.random()))
                delay = min(delay * 2, _BACKOFF_CAP_SECONDS)

    @staticmethod
    def _unrecorded_entry(scope: Any, key: str, meter: str, amount: Any) -> dict[str, Any]:
        return {
            "scope": getattr(scope, "value", scope),
            "key": key,
            "meter": meter,
            "amount": None if amount is None else float(amount),
        }

    async def _note_unrecorded(self, entries: list[dict[str, Any]], exc: Exception) -> None:
        """Keep what could not be written where a person will see it."""
        at = datetime.now(timezone.utc).isoformat()
        error = f"{type(exc).__name__}: {exc}"[:300]
        records = [{**entry, "error": error, "at": at} for entry in entries]
        self.unrecorded.extend(records)
        logger.error(
            f"Could not record {len(records)} budget charge(s) for run {self.run_id} after "
            f"the work was done ({error}); the run goes on and the charge is kept as unrecorded"
        )
        await self._emit("budget_charge_unrecorded", {"charges": records})
        from omnicoreagent.core.runs import current_run

        run = current_run()
        if run is not None:
            try:
                await run.add_unrecorded_charges(records)
            except Exception:  # the store is what is down; settle() carries the list too
                logger.debug("Could not add the unrecorded charge to the run record")

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
        totals: dict[str, Any] = {}
        granted: dict[str, float] = {}
        seen: set[str] = set()
        # A hold whose commit could not be written is still standing; the run
        # is over, so nothing of its own may stay held.
        try:
            await self.release_stale()
        except Exception as exc:
            logger.warning(f"Could not release the holds of run {self.run_id}: {exc}")
        for meter in METERS:
            for scope, key, _ in self.limits(meter):
                if key in seen:
                    continue
                seen.add(key)
                # One read per counter, as ``spent`` does.
                usage, grants = await self.ledger.usage_and_grants(key)
                if usage:
                    totals[scope.value] = usage
                if scope is BudgetScope.REQUEST:
                    # What a person granted this run goes on its record with
                    # what it spent: the counter that held it is removed.
                    for grant_meter, amount in grants.items():
                        granted[grant_meter] = granted.get(grant_meter, 0.0) + amount
                    await self.ledger.delete(key)
        if granted:
            totals["granted"] = {BudgetScope.REQUEST.value: granted}
        if self.unrecorded:
            totals["unrecorded"] = list(self.unrecorded)
        return totals

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
        # What a person granted counts: after a top-up the warning said
        # "remaining 0.0" with budget left (the 0.5.0rc4 gate). Read only
        # once spending passes the configured line.
        granted = float((await self.ledger.granted(key)).get(limit.meter, 0.0))
        allowed = limit.limit + granted
        if float(spent) < limit.warn_at * allowed:
            return
        self._warned.add((key, limit.meter))
        await self._emit(
            "budget_warning",
            {
                "scope": scope.value,
                "meter": limit.meter,
                "limit": limit.limit,
                "granted": granted,
                "used": float(spent),
                "remaining": max(0.0, allowed - float(spent)),
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
            # What people granted before, apart from the limit, as
            # budget_status shows it (the 0.5.0rc5 gate: the docs said the
            # limit included it, and nothing on the request said it).
            "granted": float((await self.ledger.granted(key)).get(limit.meter, 0.0)),
            "used": exhausted.used + exhausted.reserved,
            "needed": exhausted.requested,
            "shortfall": exhausted.shortfall,
            "status": "pending",
            "asked_at": datetime.now(timezone.utc).isoformat(),
            # Which call was refused, so a repeat refusal of it (after a
            # crash) is not counted again.
            "for": _refused_call_id(),
        }
        from omnicoreagent.core.runs import current_run

        run = current_run()
        if run is not None:
            request = await run.add_budget_request(request)
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


class WorkerBudgets:
    """The lead's budgets as a spawned worker spends them: every charge lands
    on the lead's counters, but the worker's run ending is not the lead's.
    Settling deleted the lead's request counter each time a worker finished,
    and releasing stale holds would free the lead's live ones."""

    def __init__(self, lead: RunBudgets) -> None:
        self._lead = lead

    def __getattr__(self, name: str) -> Any:
        return getattr(self._lead, name)

    async def settle(self) -> dict[str, dict[str, float]]:
        return await self._lead.spent()

    async def release_stale(self) -> int:
        return 0


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
    """What is held, per meter, from a counter as the store reads it."""
    return {
        meter: float(amount)
        for meter, amount in (state.get("reserved") or {}).items()
        if abs(float(amount)) > _HELD_DUST
    }


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


# --- the change a store applies atomically ------------------------------------
#
# A store's ``apply_budget_change(key, change)`` takes one description, and
# applies all of it or none of it:
#
#   guard         [[meter, amount, limit]]  spend; refused if it would pass the limit
#   hold          {id, meter, amount, run_id, limit, held_at}  hold; refused likewise
#   add           [[meter, amount]]         spend, never refused
#   settle        {id, meter, spend, even_if_released}  remove a hold, spend ``spend``
#                 (None: release it, spend nothing)
#   release_runs  [run_id]                  remove every hold those runs left
#   grant         {meter, amount, approver, note, granted_at}
#
# and answers {"refused": None | {meter, limit, used, reserved, requested},
# "totals": {meter: spent}, "released": int}. The checks come first (the hold,
# then each guard, in order), each against what the counter held before the
# change; a refusal changes nothing. The in-memory store applies it with
# ``apply_to_counters``; the others do the same in one statement or script.


def budget_checks(change: dict[str, Any]) -> list[tuple[str, float, float | None]]:
    """The spends a change must fit, in the order they are checked."""
    checks: list[tuple[str, float, float | None]] = []
    hold = change.get("hold")
    if hold and hold.get("amount"):
        checks.append((hold["meter"], float(hold["amount"]), hold.get("limit")))
    for meter, amount, limit in change.get("guard") or []:
        checks.append((meter, float(amount), limit))
    return checks


def refusal_for(
    checks: list[tuple[str, float, float | None]],
    counters: dict[str, dict[str, float]],
) -> dict[str, Any] | None:
    """The first check that does not fit the counters, or None."""
    for meter, amount, limit in checks:
        if limit is None:
            continue
        row = counters.get(meter) or {}
        used = float(row.get("spent", 0.0))
        held = float(row.get("reserved", 0.0))
        allowed = float(limit) + float(row.get("granted", 0.0))
        if used + held + amount > allowed:
            return {
                "meter": meter,
                "limit": allowed,
                "used": used,
                "reserved": max(held, 0.0),
                "requested": amount,
            }
    return None


def apply_to_counters(
    counters: dict[str, dict[str, float]],
    holds: dict[str, dict[str, Any]],
    history: list[dict[str, Any]],
    change: dict[str, Any],
) -> dict[str, Any]:
    """Apply a change to one key's counters held in memory (see above)."""
    refused = refusal_for(budget_checks(change), counters)
    if refused:
        return {"refused": refused, "totals": {}, "released": 0}

    def row(meter: str) -> dict[str, float]:
        return counters.setdefault(meter, {"spent": 0.0, "reserved": 0.0, "granted": 0.0})

    def drop(hold_id: str) -> dict[str, Any] | None:
        held = holds.pop(hold_id, None)
        if held is not None:
            target = row(held["meter"])
            target["reserved"] = max(0.0, target["reserved"] - float(held["amount"]))
        return held

    touched: set[str] = set()
    released = 0
    settle = change.get("settle")
    if settle:
        held = drop(settle["id"])
        if settle.get("spend") is not None and (held is not None or settle.get("even_if_released")):
            row(settle["meter"])["spent"] += float(settle["spend"])
            touched.add(settle["meter"])
    wanted = set(change.get("release_runs") or [])
    if wanted:
        for hold_id in [h for h, held in holds.items() if held.get("run_id") in wanted]:
            drop(hold_id)
            released += 1
    hold = change.get("hold")
    if hold and hold.get("amount"):
        holds[hold["id"]] = {
            "meter": hold["meter"],
            "amount": float(hold["amount"]),
            "run_id": hold.get("run_id"),
            "held_at": hold.get("held_at"),
        }
        row(hold["meter"])["reserved"] += float(hold["amount"])
    for meter, amount, _ in change.get("guard") or []:
        row(meter)["spent"] += float(amount)
        touched.add(meter)
    for meter, amount in change.get("add") or []:
        row(meter)["spent"] += float(amount)
        touched.add(meter)
    grant = change.get("grant")
    if grant:
        row(grant["meter"])["granted"] += float(grant["amount"])
        history.append(dict(grant))
        del history[:-_GRANT_HISTORY_KEPT]
    return {
        "refused": None,
        "totals": {meter: counters[meter]["spent"] for meter in touched},
        "released": released,
    }


def counters_view(
    key: str, counters: dict[str, dict[str, float]]
) -> dict[str, Any] | None:
    """What a store answers to ``get_budget_state``: spent, held and granted
    per meter, leaving out what is zero."""
    meters = {m: r["spent"] for m, r in counters.items() if r.get("spent")}
    reserved = {
        m: r["reserved"] for m, r in counters.items() if abs(r.get("reserved", 0.0)) > _HELD_DUST
    }
    grants = {m: r["granted"] for m, r in counters.items() if r.get("granted")}
    if not (meters or reserved or grants):
        return None
    return {"key": key, "meters": meters, "reserved": reserved, "grants": grants}


def legacy_budget_parts(state: dict[str, Any]) -> dict[str, Any]:
    """A counter in the 0.5.x shape, as the parts the stores now keep.

    0.5.x kept one JSON document per key: ``meters`` (spent), ``reservations``
    (every in-flight hold), ``grants`` and ``grant_history``. Stores read such
    a document once, on the first touch of its key, move it into counters and
    hold records, and remove it.
    """
    counters: dict[str, dict[str, float]] = {}

    def row(meter: str) -> dict[str, float]:
        return counters.setdefault(meter, {"spent": 0.0, "reserved": 0.0, "granted": 0.0})

    for meter, spent in (state.get("meters") or {}).items():
        row(meter)["spent"] += float(spent)
    for meter, granted in (state.get("grants") or {}).items():
        row(meter)["granted"] += float(granted)
    holds: dict[str, dict[str, Any]] = {}
    for hold_id, held in (state.get("reservations") or {}).items():
        holds[hold_id] = {
            "meter": held["meter"],
            "amount": float(held["amount"]),
            "run_id": held.get("run_id"),
            "held_at": held.get("held_at"),
        }
        row(held["meter"])["reserved"] += float(held["amount"])
    return {
        "counters": counters,
        "holds": holds,
        "history": list(state.get("grant_history") or []),
    }


def _refused_call_id() -> str | None:
    from omnicoreagent.governance.calls import current_tool_call

    call = current_tool_call()
    return call.tool_call_id if call is not None else None
