# Documentation Experience Specification

This is an internal specification. It defines the documentation behavior that
the README, Mintlify docs, and cookbook must satisfy.

Public documentation lives in `README.md`, `docs/`, and `cookbook/`.

## Source Of Truth

Documentation must describe implemented behavior only. If a page documents
configuration, defaults, exports, or public behavior, the claim must be grounded
in code, tests, or an accepted engineering specification.

Priority order when resolving conflicts:

1. shipped source code
2. tests
3. engineering specifications
4. public docs
5. README

Public docs and README must be updated when source code changes public behavior.

## Terminology Contract

Use these terms consistently:

| Term | Meaning |
|------|---------|
| agent harness | runtime layer around one or more models |
| model_config | provider/model selection and model options |
| `LLM_API_KEY` | single public model API key env var |
| local tools | Python functions registered by the application |
| MCP tools | tools loaded from MCP servers |
| memory | conversation/session history |
| workspace | file/state surface for notes, artifacts, offloads, subagent output |
| task store | background task/run/attempt/lease storage |
| telemetry | typed events, traces, streams, and exporters |
| OmniServe | REST/SSE serving boundary |

Forbidden public-doc substitutions:

- do not call OmniCoreAgent only an "agent framework" in primary positioning
- do not document `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or
  `GROQ_API_KEY` as OmniCoreAgent public env vars
- do not use `DeepAgent` as a current public API path
- do not call background task-store state "memory"
- do not call workspace storage a memory backend

## README Contract

`README.md` must contain, in this order:

1. logo/name/badges
2. one-sentence positioning
3. short capability line
4. navigation links
5. "What It Is"
6. "Quick Start"
7. "Choose Your Path"
8. "What You Can Build"
9. differentiators
10. install extras
11. features
12. implementation map or architecture pointer
13. cookbook/docs links
14. development/contribution/license/author

The first runnable example must appear before any long architecture narrative.

README quickstart must:

- install with `pip install omnicoreagent`
- set `LLM_API_KEY`
- construct `OmniCoreAgent`
- call `agent.run(...)`
- print `result["response"]`
- call `await agent.cleanup()`

## Mintlify Configuration Contract

`docs.json` must:

- keep project name as `OmniCoreAgent`
- keep the primary docs CTA pointing to quickstart
- include a GitHub navbar link
- include a docs contextual menu when supported by Mintlify
- include `assistant`, `copy`, `view`, `chatgpt`, `claude`, `perplexity`,
  `mcp`, `cursor`, and `vscode` contextual actions when supported
- include the "Use Docs With AI Tools" page in the Get Started group
- include goal-oriented navigation for "Choose Your Path" when those pages exist
- keep configuration, serving, production, and troubleshooting discoverable from
  navigation when those pages exist
- configure `seo.indexing: "all"` when hidden or agent-only pages are used as
  assistant/MCP/`llms.txt` context
- avoid navigation entries for pages that do not exist

If Mintlify changes the config schema, docs config must be validated before
merge.

## Assistant Contract

`.mintlify/Assistant.md` must exist.

The assistant instructions must tell Mintlify Assistant to:

- answer from official OmniCoreAgent docs
- use "agent harness" as the primary identity
- tell users to use `LLM_API_KEY`
- distinguish memory, workspace, and task store
- distinguish local tools and MCP tools
- avoid claiming unimplemented features
- link relevant docs pages when answering
- say when the docs do not contain enough information
- keep answers concise and action-oriented

The assistant instructions must not include secrets or local-only paths.

Assistant availability is a deployment concern. The first implementation must
configure `.mintlify/Assistant.md`; release verification must also check the
deployed docs site after merge to confirm Ask AI is available when the Mintlify
plan/dashboard supports it. If Ask AI is unavailable because of dashboard or
plan state, the PR should still improve AI-tool docs through contextual actions,
`/llms.txt`, `/mcp`, and Markdown export, but the release note must call out
that dashboard enablement is still required.

## AI Tools Page Contract

`docs/getting-started/use-docs-with-ai-tools.mdx` must exist.

It must explain:

- Mintlify Ask AI
- copy page as Markdown
- view page as Markdown
- `/llms.txt`
- hosted docs MCP at `/mcp`
- using the docs from Cursor
- using the docs from VS Code
- using the docs from ChatGPT, Claude, and Perplexity

The page must include examples of good prompts:

- "Show me the smallest OmniCoreAgent with one local tool."
- "Explain memory vs workspace vs task store."
- "Build an OmniServe API with auth and rate limiting."
- "Which config enables context management and tool offload?"

The page must not imply AI answers replace source verification for production
changes.

## Quickstart Contract

`docs/getting-started/quickstart.mdx` must be beginner-first.

It must contain:

- install
- set `LLM_API_KEY`
- first agent
- run command
- expected result shape
- add one local tool
- add a stable session id
- point to workspace and OmniServe next steps
- a common-errors section for missing `LLM_API_KEY`, missing optional extras,
  and provider/model failures

It must not require:

- Redis
- MongoDB
- PostgreSQL
- S3/R2
- MCP
- OmniServe
- background tasks

Those belong in later sections.

The transition from local agent to API must live in a separate "Serve An Agent
API" path or the OmniServe guide. Quickstart may link to it but must not make it
part of the first successful run.

## Docs Landing Page Contract

`docs/index.mdx` must:

- identify OmniCoreAgent as an open production agent harness
- give a minimal install command
- link to quickstart
- link to "Use Docs With AI Tools"
- include a "Choose Your Path" section that maps user goals to docs/cookbook
  destinations
- link to production and troubleshooting paths when those pages exist
- avoid overwhelming users with every feature before the first run path

## Cookbook Contract

Cookbook index pages must have frontmatter unless Mintlify explicitly does not
render them.

Each cookbook category page should include:

- title
- description
- what examples prove
- prerequisites
- run command
- expected output or output location
- table of examples
- links back to docs concept pages

Cookbook examples that require external services must say so.

Cookbook examples that run with local defaults must say so.

Each individual cookbook recipe should be copy-paste runnable or explicitly
marked as conceptual. Each recipe should state required env vars, optional
services, and expected output when practical.

## Configuration Accuracy Contract

Public docs must document these environment facts:

- `LLM_API_KEY` is the single public hosted-model API key variable used by
  OmniCoreAgent examples.
- Memory defaults to in-memory.
- Workspace defaults to local storage.
- OmniServe has defaults and does not require env vars to start.
- Background task store defaults to in-memory.
- Redis/MongoDB/SQL task store is separate from MemoryRouter.
- S3/R2 workspace storage is separate from memory.

Docs must not add provider-specific model API key env vars unless source code
adds them as public OmniCoreAgent configuration.

## Code Block Contract

All fenced code blocks in public docs must have a language tag unless they are
plain terminal output or intentionally generic text.

Allowed common tags:

- `bash`
- `python`
- `json`
- `text`
- `yaml`
- `dockerfile`

## Frontmatter And Metadata Contract

Every touched public `.mdx` page must include frontmatter with:

- `title`
- `description`

Use `icon` where the page appears in primary navigation.

Descriptions should be concrete enough to help Mintlify search, `llms.txt`, and
AI retrieval. Avoid vague descriptions such as "Learn more".

## Goal Path Contract

The docs must include a goal-oriented entry point on the landing page and
README. The implementation may add separate pages gradually, but the mapping
must exist.

Required goal mappings:

| Goal | Required Destination |
|------|----------------------|
| Run first agent | quickstart |
| Add Python tools | local tools guide or cookbook |
| Connect MCP tools | MCP guide or cookbook |
| Keep memory | memory guide |
| Use files/artifacts | workspace files guide |
| Serve as API | OmniServe guide |
| Run background work | background agents guide or cookbook |
| Build real app | real applications cookbook |
| Debug production issue | troubleshooting or production guide |

## Troubleshooting Contract

Beginner and production docs must expose troubleshooting paths. At minimum,
docs must include direct fixes for:

- missing `LLM_API_KEY`
- unsupported provider/model value
- missing optional extra
- MCP server connection failure
- OmniServe port already in use
- OmniServe auth failure
- workspace backend credentials missing
- Redis/MongoDB/SQL connection failure
- background run timeout/cancel/retry states
- telemetry trace not found

## Validation Contract

Before merging docs changes:

- run a local search for forbidden provider-specific env var claims
- run a local search for stale `DeepAgent` references
- run a local search for "framework" in primary positioning pages
- validate `docs.json` is parseable JSON
- verify every touched public `.mdx` page has `title` and `description`
  frontmatter
- run docs/cookbook tests when examples or claims change
- run a markdown/frontmatter sanity check for new docs pages
- validate AI contextual menu schema against current Mintlify docs
- check `/llms.txt` and `/mcp` documentation links after deployment

If Mintlify CLI is available locally, run:

- `mint validate`
- `mint broken-links --check-anchors`

If Mintlify CLI is unavailable, record that explicitly in the PR and run the
fallback checks above. A deployed docs smoke check must run after merge.

## First Implementation Acceptance Criteria

The first docs-experience implementation is complete when:

- `.mintlify/Assistant.md` exists
- `docs.json` includes AI contextual actions and the AI tools page
- `docs/getting-started/use-docs-with-ai-tools.mdx` exists
- `docs/index.mdx` has a clearer beginner and AI-tool entry
- `docs/getting-started/quickstart.mdx` is beginner-first and runnable
- `README.md` top section is tighter and keeps claims grounded
- cookbook index pages touched by the change have frontmatter
- all new claims avoid unsupported provider-specific env vars
- the first-run docs include common setup errors
- the landing page and README include goal-oriented path selection
- validation commands pass
