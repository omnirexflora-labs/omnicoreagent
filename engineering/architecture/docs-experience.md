# Documentation Experience Architecture

This is an internal architecture record. It defines how the public README,
Mintlify documentation, and cookbook should work together so new users can
understand OmniCoreAgent quickly and experienced builders can find exact
contracts without guessing.

Public docs live under `docs/` and `cookbook/`. This record exists to keep the
documentation system coherent before implementation changes begin.

## Purpose

OmniCoreAgent documentation must convert interest into working usage.

The documentation should answer four user questions in order:

1. What is OmniCoreAgent?
2. How do I run my first agent?
3. How do I build my real application shape?
4. Where is the exact reference when I need to configure or debug something?

The docs should support beginners, production app builders, and AI coding tools.
The same content must be readable by humans and usable by Mintlify Assistant,
`llms.txt`, hosted docs MCP, Cursor, Claude, ChatGPT, and similar tools.

## Product Positioning

OmniCoreAgent is an open production agent harness for application builders.

The docs must not position OmniCoreAgent as:

- a generic "agent framework"
- a personal assistant product
- a consumer chatbot platform
- a marketing-only runtime
- a bag of loosely connected helpers

The docs should consistently use these terms:

| Concept | Preferred Term |
|---------|----------------|
| Runtime around the model | agent harness |
| Model credential | `LLM_API_KEY` |
| Conversation storage | memory |
| File/state surface | workspace |
| Background run state | task store |
| HTTP/SSE serving | OmniServe |
| Runtime progress and traces | telemetry |
| External MCP-provided functions | MCP tools |
| Application Python functions | local tools |

## Documentation Surfaces

### README

The README is the landing page for GitHub, PyPI, social links, and search
traffic. It should make the value obvious before the reader scrolls.

README order:

1. one-line positioning
2. short capability statement
3. install
4. smallest working example
5. "what makes it different"
6. build-by-use-case paths
7. install extras
8. features
9. deeper architecture and contribution details

The README can be strong and direct, but it must avoid long narrative blocks
before the first runnable example.

### Mintlify Docs

Mintlify docs are the primary learning surface. They should be organized around
two parallel paths:

1. goal-oriented paths for users who know what they want to build
2. concept/reference paths for users who need exact runtime behavior

Top-level navigation must preserve fast access to configuration, serving,
production operations, and troubleshooting. Those are not secondary details for
application builders; they are where prototype agents usually become hard to
ship.

The docs should not force a beginner to understand every production feature
before running the first agent.

### Cookbook

The cookbook is the proof surface. It should contain runnable examples, grouped
by user intent:

- first agent
- local tools
- MCP tools
- memory
- workspace files
- tool offload
- subagents
- background tasks
- OmniServe
- real applications

Cookbook pages should state:

- what the example demonstrates
- required environment variables
- optional external services
- how to run it
- what output to expect

### Internal Engineering Docs

Engineering records stay under `engineering/`. They are not product docs. They
exist for maintainers and coding agents. Public docs may summarize decisions
from engineering records, but should not expose internal planning language as
user-facing content.

## AI-Native Documentation Layer

Modern documentation is read by people and by AI tools. OmniCoreAgent docs must
support both.

Required AI-native surfaces:

- Mintlify Assistant enabled in the dashboard.
- `.mintlify/Assistant.md` for assistant behavior and terminology.
- Mintlify contextual menu with `assistant`, `copy`, `view`, `chatgpt`,
  `claude`, `perplexity`, `mcp`, `cursor`, and `vscode`.
- A "Use Docs With AI Tools" page that explains Ask AI, `/llms.txt`, `/mcp`,
  Cursor, Claude, VS Code, and copy-as-Markdown.
- Consistent frontmatter titles and descriptions.
- Language-labelled code blocks.
- Specific nouns instead of ambiguous pronouns.
- Hidden or agent-only content only when it helps AI answer correctly without
  cluttering user-facing docs, and only when indexing/search/MCP behavior is
  explicitly configured and validated.

The assistant should answer from official docs only. If the docs do not contain
the answer, the assistant should say what is missing and link the closest
relevant pages.

## Information Architecture

Target public navigation:

```text
Get Started
  Overview
  Installation
  Quickstart
  Use Docs With AI Tools

Choose Your Path
  First Agent
  Add Tools
  Connect MCP Tools
  Add Memory
  Use Workspace Files
  Serve An Agent API
  Run Background Work
  Build A Real Application

Build Guides
  Basic Usage
  Local Tools
  MCP Tools
  Memory
  Workspace Files
  Context Engineering
  Tool Offload
  Subagents
  Background Agents

Concepts
  Agent Harness
  Architecture
  Guardrails
  Events and Telemetry
  Skills
  Workflows

Configure
  Configuration
  Models
  Memory Backends
  Workspace Backends
  Background Task Stores
  Telemetry Exporters

Serve
  OmniServe
  REST And SSE Endpoints
  Auth And Rate Limits
  Background HTTP API
  Deployment Shape

Production
  Production Checklist
  Troubleshooting
  Guardrails In Production
  Telemetry And Debugging
  Background Task Recovery

Reference
  OmniCoreAgent
  AgentConfig
  MemoryRouter
  Workspace
  OmniServeConfig
  BackgroundAgentManager
  Telemetry APIs

Cookbook
  Getting Started
  Real Applications
  OmniServe
  Background Agents
  Workflows
  Production

Changelog
```

This can be implemented gradually. The first implementation phase should not
create empty reference pages. It should add the navigation structure only when
pages exist.

## Beginner Journey

A beginner should be able to complete this path without reading architecture:

1. Install with `pip install omnicoreagent`.
2. Set `LLM_API_KEY`.
3. Run a minimal agent.
4. Add one local tool.
5. Add a stable `session_id`.
6. Write/read a workspace file.
7. Know where to go next based on use case.

Every step must be copy-paste runnable or clearly marked as conceptual.

The beginner quickstart should not require OmniServe. The local-agent to served
API transition gets its own short guide: "Serve An Agent API". That guide starts
from the quickstart agent and shows the smallest OmniServe path.

## Production Builder Journey

A production app builder should quickly find:

- memory backend choice
- workspace backend choice
- task store choice
- OmniServe auth/rate-limit/prefix settings
- telemetry trace retrieval and export
- guardrail behavior
- background task lifecycle
- deployment-shaped examples
- production checklist
- troubleshooting and recovery steps

The docs should explain the difference between similar concepts:

- memory vs workspace
- workspace storage vs memory backend
- task store vs memory router
- telemetry events vs trace exports
- local tools vs MCP tools
- background agent manager vs OmniServe

## Goal-Oriented Entry Points

The docs landing page and README must include a "Choose Your Path" section.
This section maps user intent to pages and examples instead of asking users to
infer which feature they need.

Required paths:

| User Goal | Destination |
|-----------|-------------|
| Run the first agent | Quickstart |
| Add application tools | Local tools guide |
| Connect external tools | MCP tools guide |
| Keep session continuity | Memory guide |
| Store files and artifacts | Workspace files guide |
| Avoid context overflow | Context engineering guide |
| Serve an agent over HTTP/SSE | OmniServe guide |
| Run durable long-running work | Background agents guide |
| Build a production-shaped app | Real applications cookbook |
| Debug or operate production runs | Production/troubleshooting docs |

## Troubleshooting Path

Troubleshooting is part of the docs architecture, not an afterthought. The docs
must give direct fixes for common first-run and production failures:

- missing `LLM_API_KEY`
- unsupported provider/model name
- optional extra not installed
- async cleanup/lifecycle mistakes
- MCP server connection failures
- port already in use
- OmniServe auth failures
- rate-limit behavior
- workspace backend misconfiguration
- Redis/MongoDB/SQL task-store connection failures
- background task stuck, cancelled, retried, timed out, or missing workspace output
- telemetry trace not found

## Voice And Style

The docs should sound confident and direct. They should not weaken clear claims
with unnecessary hedging. They should also not overclaim features that are not
implemented.

Writing rules:

- lead with the answer
- use short paragraphs
- use concrete examples
- say exactly which config key or env var matters
- prefer tables for option comparison
- avoid story-heavy sections in quickstart paths
- keep official docs cleaner than social posts
- use one term per concept

## Implementation Phases

### Phase 1: AI-Native Discovery And First-Run Path

Goal: make docs easier to enter and easier for AI tools to use.

Scope:

- add `.mintlify/Assistant.md`
- add Mintlify contextual menu configuration
- add `docs/getting-started/use-docs-with-ai-tools.mdx`
- tighten docs landing page and quickstart
- tighten README top section
- add frontmatter to cookbook index pages where missing

### Phase 2: Build-By-Use-Case Guides

Goal: help users map intent to examples.

Scope:

- choose-your-path page or landing section
- support agent guide
- research agent guide
- background worker guide
- served API guide
- MCP tools guide
- workspace-first guide
- production checklist
- troubleshooting guide

### Phase 3: Reference Section

Goal: give app builders exact contracts.

Scope:

- OmniCoreAgent reference
- AgentConfig reference
- ModelConfig reference
- MemoryRouter reference
- Workspace reference
- OmniServeConfig reference
- BackgroundAgentManager reference
- Telemetry API reference

### Phase 4: Cookbook Quality Pass

Goal: make every cookbook page runnable, searchable, and explicit about
requirements.

Scope:

- standard frontmatter
- prerequisites
- run commands
- expected output
- required services
- links back to docs concepts
- every individual recipe documents whether it is local-only or requires
  external services

## Non-Goals

This docs redesign does not:

- redesign the visual brand
- add new runtime features
- publish internal engineering docs as official product docs
- create API reference pages without verifying source code contracts
- claim unsupported provider-specific environment variables
- replace examples with marketing copy

## Success Criteria

The docs pass when:

- a new user can run the quickstart in under ten minutes
- the README communicates the harness value before deep details
- the docs navigation exposes beginner, builder, concept, and reference paths
- Ask AI and contextual AI actions are configured
- `/llms.txt` and `/mcp` are explained for AI-tool users
- hidden or agent-only content is indexed/searchable when used
- no docs page contradicts the code about env vars, defaults, or feature status
- every code block has a language tag
- cookbook pages tell users what runs locally and what needs services
- troubleshooting gives direct fixes for common setup and production failures
- docs validation passes locally
