# 0.5.0rc1 gate — results (2026-09-29)

Wheel built from main f8966e0, installed fresh per area, real model and services. Spend ≈ $0.85 total.
Verdict: **NOT READY.** Core guarantees held everywhere (governance default, command rules, approvals across
processes and HTTP, budgets across processes, kill -9 recovery on SQLite/Redis/MongoDB/Postgres, lost-result
window, fresh-process reads, background scheduling/recovery, sandboxes on Docker/Daytona, the record, exporters,
headless exit codes, Harbor end to end). The findings below must be fixed and re-gated.

## Product — blockers
- R1 First model call imports LiteLLM on the event loop (A-P1, C-F2): 15–306 s freeze under load; heartbeats stall,
  a live run was taken over, background runs failed as lease expired, MCP/HTTP timers fired.
- R2 Budgets written into the caller's own policy object (F-01): default profile + rules + budgets fails to start
  when served (governance built twice) or shared by two agents.
- R3 Auto-discovered policy file drops the profile's allow rules (F-02).
- R4 Workspace bridge's own listing command judged as an agent command → opaque → denied under command-allow
  policies; every execute in a sandbox fails (B-F2).
- R5 Approvals: run() result and OmniServe omit `command` (sub-commands) and OmniServe omits `decision` (B-F1, A).
- R6 `postgresql://` fails on a fresh install (SQLAlchemy 2.1 defaults to psycopg 3; extra ships psycopg2) (C-F1).
- R7 `including_subagents` drops sub-agents from a segment that suspended (E-1).
- R8 `training_records` fallback returns [] for a resumed run without its record (E-2).

## Product — must fix
- R9 `local` provider: asks about network, then refuses the host command anyway (B-F3, D-N1).
- R10 `grant_budget` accepted on an abandoned run (B-F4).
- R11 Permissive fall-through reported as `matched_allow` with no rule (F-03).
- R12 No HTTP route to list runs by status/session (F-04).
- R13 Sandbox containers orphaned by a crash are never removed; only a host-wide sweep exists (F-05).
- R14 Policy load errors: file error hides the reason; dict policy fails only at first run; repeated rule id;
  raw TypeError for an unknown `command` key; sandbox options checked at run, not build (B-F5, A-D3).
- R15 Headless Ctrl-C: exit 1, no evidence (E-6).
- R16 Small: trace-retention counts (C-F3), payload retention TZ (C-F4), OTLP timeout recorded as failure though
  delivered (E-5), MCP "Future exception was never retrieved" (E-4), denied approval shows `used` (F-06, B).

## Docs
Overview policy "off" (A-D1) and list-of-functions claim; store-URL fall-back in 4 places (A-D2); upgrading host-exec
row (D-F1); examples writing `"enabled": True` flip the profile (F-07.1); install 3.10/3.11 contradiction, stale
security-model entry, "defaults" clarity, PolicyBudgets undocumented, 0.4.x version strings/banners, SSE example
events, SQLite needs [postgres], durable-runs points to source files, AGENTS.md docs.json path (F-07); policies
outputs (allow_configured_mcp_tools), hash note, TargetMatcher error; approvals decision field; durable-runs
LookupError source; events/observability counts; read-a-run unpriced cost; code-mode approval arguments;
agents-md/workspace-files missing "create the file"; headless usage (E-3) and hard-coded run id; harbor
setup-timeout multiplier and steps note; bridge include/exclude both ways (D-N2); skill env in sandbox (D-N3);
shutdown "cancels" wording and `lease_expired` attempt reason (C).
