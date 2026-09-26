# How OmniCoreAgent compares — a verified table

Status: started 2026-09-26, for the 0.4.0 announcement (2026-09-28).

## Why

People evaluating an agent runtime ask first how it differs from what they
know. Without an answer they guess; with a wrong or stale answer they stop
trusting the rest. So the table is only worth publishing if every cell can be
checked.

## Rules

- **Concrete capabilities, not adjectives.** Each row is something a reader can
  check in each project's docs: "a person's approval pauses a run and it
  resumes later in another process", not "production-ready".
- **Each cell is sourced.** Yes / Partial / No / Not documented, with a link to
  the project's own current documentation and the date it was read. "Not
  documented" is not "No".
- **Honest about where others lead.** Rows where another project is ahead
  (graph workflows, ecosystem, hosted tracing, TypeScript) are in the table.
- **Our own column is verified the same way**, against our docs and code.
- **Dated and maintained.** The page says when it was checked, and invites
  corrections through an issue.

## Projects

OpenAI Agents SDK (Python), LangGraph, Pydantic AI, Claude Agent SDK,
CrewAI.

## Rows

1. Policy that allows, asks or denies each action (tool, command, network)
2. A person's approval pauses the run; it resumes later, in another process
3. Budgets in dollars enforced before a model call
4. Built-in sandboxed command execution (which providers)
5. Sandbox network off by default
6. A run survives a process crash and resumes from its checkpoint
7. Every run recorded locally (trajectory/trace) with no external service
8. OpenTelemetry export
9. MCP client (stdio and remote)
10. Sub-agents / delegation
11. Scheduled / background runs built in
12. HTTP server for the agent built in
13. Model providers (one vendor or many)
14. Graph / explicit workflow orchestration
15. Hosted tracing / evaluation product from the same vendor
16. Languages
17. License

## Steps

- R1. Research, one agent per project, official docs only, every cell with
  a source URL and a quote. Our column from our docs and code.
- R2. Verify: spot-check every "No" and every claim where we look better,
  since those are the cells readers challenge.
- R3. Page: `docs/comparison.mdx` (dated, sourced, correction invite), a
  short pointer in the README.
