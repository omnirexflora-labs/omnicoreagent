"""How many times a support desk run goes to its database, with a ceiling.

The support desk ramp (2026-10-07) found about 54 Postgres transactions for one
completed run of about 3 model calls and 2 tool calls, and a per-step overhead
that was mostly threads waiting on those round trips. This counts them in
process, against the fake provider, for the scenario the desk exists for: an
order lookup, a refund that waits for a person, the approval, the resume, with
the desk's three budget scopes on.

The ceilings are the count after the last change that lowered it, with no
slack beyond a call or two for timing. A change that adds a round trip to the
hot path fails here and has to say why it is worth it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

DESK = Path(__file__).resolve().parent.parent / "apps" / "support_desk"
for path in (DESK, DESK / "load"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import roundtrips  # noqa: E402
from test_fake_provider import running_fake_provider  # noqa: E402

# Transactions per run, by who asked. See the git log for what lowered each.
CEILING = {
    "budgets": 11,
    "messages": 7,
    "run_state": 26,
    "other": 0,
    "total": 44,
}


async def _measure(url: str) -> dict:
    with roundtrips.desk_environment(url):
        agent = roundtrips.load_desk().create_agent()
        await agent.initialize()
        counter = roundtrips.RoundTripCounter()
        counter.attach(agent.memory_router.memory_store)
        # The first run pays one-time setup (tables, legacy-counter lookups);
        # the second is what every later run costs.
        await roundtrips.refund_scenario(agent, "warm")
        counter.reset()
        await roundtrips.refund_scenario(agent, "maya")
        return counter.snapshot()


@pytest.fixture(scope="module")
def counted():
    with running_fake_provider() as url:
        return asyncio.run(_measure(url))


def test_the_refund_scenario_stays_under_its_round_trip_ceiling(counted, capsys):
    with capsys.disabled():
        print("\n[roundtrips] per run:", counted["transactions"], "total", counted["total_transactions"])
    for caller, ceiling in CEILING.items():
        actual = counted["total_transactions"] if caller == "total" else counted["transactions"][caller]
        assert actual <= ceiling, f"{caller}: {actual} transactions, ceiling {ceiling}\n{counted['methods']}"


def test_every_store_call_is_one_transaction_at_most_once_per_attempt(counted):
    # A store call that opens two transactions is a round trip someone could
    # have saved: the counts of calls and transactions stay together.
    assert counted["total_transactions"] <= counted["total_calls"] + 2
