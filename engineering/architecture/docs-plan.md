# The docs and the README: anyone can use every feature from them alone

Status: asked for by the maintainer 2026-09-25 ("even me myself i want to read the
docs and readme and be able to use all kind of features … without me knowing that
i build this myself"; "make it modernize … make it interesting, dont make the docs
boring"). Part of `excellence-plan.md` (X3, X4).

## The bar, and how it is checked

A reader who has never seen the code reaches every feature from the README and
the docs alone, and enjoys getting there. Three checks keep that true after the
release, not only on the day:

1. **Every example is real.** Every Python block parses and names only what
   exists (`tests/test_docs_code.py`, PR #277); the examples a page is built
   around were run for real and show what they printed.
2. **Nothing is undocumented.** A test lists the public surface from the code —
   `OmniCoreAgent`'s methods, every `agent_config` and `telemetry_config`
   setting, every CLI command and flag, every OmniServe route — and fails when
   one appears in no page. On 2026-09-25: 7 of 41 methods and 3 of 26 settings
   were in no page, and 148 of 164 public exports had nowhere to be looked up.
3. **The stranger test.** An agent with no knowledge of this repository gets the
   docs and a task — "add a tool, make it ask for approval, then read what
   happened" — and must finish from the docs alone. Where it stalls is a page
   to fix. One task per journey below.

## Organized by what a reader is trying to do

| Section | Pages | The reader leaves with |
|---|---|---|
| **Start** | What it is (one page, one diagram) · Install · Quickstart (5 minutes: an agent, a tool, and the evidence of its run) · A tour (15 minutes: memory, a policy that asks, a sandbox, the trace) | A working agent and a map |
| **Build** | Tools · MCP · Skills · Code mode · Memory and sessions · Workspace files · Context · Sub-agents · Streaming and events · Models | The agent doing real work |
| **Make it safe** | Governance and policies · Approvals · Sandboxes and execution · Guardrails · Budgets · Credentials and privacy | An agent they can let act |
| **Run it** | Durable runs and recovery · Background agents · OmniServe · Deploying in a container · Stores and scale | An agent in production |
| **See and improve** | Trajectories · Telemetry and exporters · Outcomes and training records · Headless runs in CI · Harbor and Terminal-Bench · Portable evidence | Evidence, evaluation, data to train on |
| **Reference** | `OmniCoreAgent` · `agent_config` · `telemetry_config` · Policy schema · CLI · OmniServe HTTP API · Environment variables · Errors | Every name, looked up in seconds |
| **Releases** | Upgrading from 0.3.9 · Changelog | A painless upgrade |

The reference is **generated** where it can be — the CLI from its command
definitions, the HTTP API from OmniServe's own OpenAPI document (rendered as
interactive pages), the settings from the config classes — so it cannot drift.

## Every page, the same shape

1. **What you'll have** — one or two sentences and, where it helps, a diagram.
2. **Working code, then what it printed** — copied from a real run.
3. **How it works** — short, with the one diagram a reader needs.
4. **Options** — a table, linking to the reference.
5. **When things go wrong** — the errors a reader will actually meet.
6. **Next** — cards to the pages that naturally follow.

Modern and readable: Mintlify's steps, tabs, cards, callouts and code groups;
Mermaid diagrams for flows; short paragraphs; no page that is a wall of text or
a list of every option before the reader has seen one work.

## Units

- **D1. The README** — the front door: what is different in one screen, a
  quick start that ends with the evidence, the CLI and Harbor, a diagram.
- **D2. Start** — Install, Quickstart, the tour; the stranger test for "first
  agent".
- **D3. The coverage test and the generated reference** — CLI, HTTP API,
  settings, `OmniCoreAgent`.
- **D4. Upgrading from 0.3.9**, and the v0.4.0 release notes.
- **D5–D8. Build, Make it safe, Run it, See and improve** — page by page in the
  shape above, each section closed by its stranger test.

For v0.4.0 on Monday: D1–D4, and the existing pages kept correct by the tests.
D5–D8 continue after the release; the docs site updates without one.
