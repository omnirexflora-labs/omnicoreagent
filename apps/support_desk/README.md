# The support desk

A small, real application on OmniCoreAgent, shaped like a deployment: one
agent served by OmniServe to many customers at once, with Postgres, Redis,
budgets and a person who approves refunds. It is the example for running
OmniCoreAgent in production, and the app the load and chaos tests push on.

Each customer has a session. They chat over HTTP and get the reply as JSON or
as a live stream (SSE). The agent can look up an order, search help articles
and issue a refund. A refund moves money, so the policy pauses the run until a
person approves it over HTTP.

The plan: `engineering/architecture/production-readiness-plan.md`.

## What is here

| File | What it is |
| --- | --- |
| `agent.py` | The agent: its tools, its policy, its budgets, its stores. OmniServe calls `create_agent()`. |
| `compose.yml` | The deployment: `desk` (OmniServe), `postgres`, `redis`, `fakeprovider`. The desk is capped at 2 CPUs and 4 GB. |
| `Dockerfile` | The desk's image: the runtime with the `serve`, `postgres`, `redis`, `otel` extras. No secrets. |
| `env.example` | A template for the settings. No secrets. |
| `fakeprovider/` | A fake model provider that speaks the OpenAI API: scripted answers, set latency, injected faults. Its own container. |
| `scale/` | Scale out: two 1-CPU replicas behind nginx, sharing the same stores. One process uses about one core; this uses two. See `scale/README.md`. |

The desk's data is a small SQLite file, seeded on start: five orders, five
help articles, and a refund ledger that only ever grows.

## How it is set up

- **Tools:** `lookup_order(order_id)`, `search_kb(query)`, `issue_refund(order_id, amount)`.
  `issue_refund` is not idempotent: every call adds a row to the ledger.
- **Policy:** everything is allowed except `issue_refund`, which asks (rule
  `refunds_need_a_person`). `approval_mode` is `suspend`: the run is saved and
  can be resumed after the approval, even by another process.
- **Budgets:** per request (`model_cost_usd` $0.50 and 20 tool calls), per
  session ($1.00), and per application per day ($5.00). Change them with the
  `DESK_*` variables in `env.example`.
- **Memory, run state, budget counters:** Postgres (`DATABASE_URL`).
- **Background tasks:** Redis.
- **Traces:** a store on the desk's disk (a volume), with `capture` set to
  `default` so model prompts and responses are not kept. An OTLP exporter is
  added when `DESK_OTLP_ENDPOINT` is set.

## Run it with the fake provider

You need Docker. From the repository root:

```bash
docker compose -p support-desk -f apps/support_desk/compose.yml up -d --build
curl -s localhost:8800/ready
```

With no `LLM_API_KEY` in your shell, the desk talks to the fake provider and
costs nothing. The API listens on `127.0.0.1:8800`; the token is
`OMNICOREAGENT_SERVE_AUTH_TOKEN` in `env.example` (`change-me`). To use your
own token, copy the template, edit it, and name it:

```bash
cp apps/support_desk/env.example apps/support_desk/desk.env
DESK_ENV_FILE=desk.env docker compose -p support-desk -f apps/support_desk/compose.yml up -d
```

Stop it and delete its volumes with
`docker compose -p support-desk -f apps/support_desk/compose.yml down -v`.

## Run it with a real model

Export the key and the model name in your shell, then start the desk. The key
is never written to a file:

```bash
export LLM_API_KEY=...        # your provider's key
export DESK_MODEL=gpt-5.4-mini
docker compose -p support-desk -f apps/support_desk/compose.yml up -d --build desk
```

The desk reads `LLM_API_KEY`: when it is set, the model is the real one. To
reach a gateway or another OpenAI-compatible server, also set `DESK_BASE_URL`.
Without a key, `model_config` carries `base_url` pointing at the fake provider,
the same public setting.

To run the desk without Docker: `omniserve run --agent apps/support_desk/agent.py`
(start the fake provider with `uvicorn fakeprovider.server:app --app-dir apps/support_desk --port 9000`).
Without `DATABASE_URL`, memory stays in the process.

## Chat

Every route but `/health`, `/ready` and `/prometheus` needs the token.

```bash
T="Authorization: Bearer change-me"

curl -s -X POST localhost:8800/run/sync -H "$T" -H "Content-Type: application/json" \
  -d '{"query": "Where is order 1042?", "session_id": "maya"}'
```

The answer has `response`, `status`, `run_id` and `trace_id`. `session_id` is
the conversation: the next request with `"maya"` remembers this one.

To stream the reply, post to `/run`. You get Server-Sent Events: tool calls as
they happen, the answer word by word (`text_delta`), and a last `complete`:

```bash
curl -N -X POST localhost:8800/run -H "$T" -H "Content-Type: application/json" \
  -d '{"query": "What is your returns policy?", "session_id": "maya"}'
```

## Approve a refund

Ask for one:

```bash
curl -s -X POST localhost:8800/run/sync -H "$T" -H "Content-Type: application/json" \
  -d '{"query": "Please refund order 1042, $12.50.", "session_id": "maya"}'
```

The run stops with `"status": "awaiting_approval"`. Nothing has moved. The
`approvals` list says what is asked: the tool, its arguments, and the reason.
A person on the support team decides, then the run resumes:

```bash
RUN_ID=run_...            # from the answer
APPROVAL_ID=approval_...  # from the answer

curl -s -X POST localhost:8800/runs/$RUN_ID/approvals/$APPROVAL_ID -H "$T" \
  -H "Content-Type: application/json" \
  -d '{"decision": "approve", "approver": "dana", "note": "Customer called."}'

curl -s -X POST localhost:8800/runs/$RUN_ID/resume -H "$T"
```

Send `"decision": "deny"` with a `note` instead, and the agent tells the
customer the refund was not issued. To find what waits for a person, after a
crash or a lost connection: `GET /runs?status=awaiting_approval`.

## What you can see

- **The run:** `GET /runs/$RUN_ID/trajectory` is the whole run as one story:
  the segments (`suspended`, then `completed`), each tool call once, the
  approval and who decided it.
- **The budgets:** `GET /runs/$RUN_ID/budget` lists every budget that covers
  the run, with what each has spent.
- **The trace:** `GET /telemetry/runs/$RUN_ID/trace`, or `/telemetry/events?run_id=...`.
  It holds `policy_decision_ask`, `approval_request_created`, `run_suspended`,
  `run_resumed`, `approval_resolved` and `policy_decision_allow` with reason
  `approved`. With `capture` at `default`, tool arguments are `[REDACTED]`;
  the approval record, in the run, keeps them for the person deciding.
- **`/prometheus`:** OmniServe's HTTP counters: requests in total, successes
  and errors, one counter per route (for example
  `omniserve_requests_run_sync_total`), active requests, and the request
  duration (count, sum and average). It does not yet count runs, model calls,
  tokens, cost, approvals or budgets; read those from the run and the trace.

## The fake provider

`fakeprovider/` answers `POST /v1/chat/completions` (streaming or not) from a
script: it calls `lookup_order` with the order id in the message, then
`issue_refund` if the customer asks for money back and `search_kb` if not, then
answers from what the tools returned. It returns token counts. The agent reaches
it through `model_config`:

```python
model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "fake",
              "base_url": "http://fakeprovider:9000/v1"}
```

Faults and delays are set by `FAKE_*` variables (see `env.example`) or while it
runs:

```bash
curl -s -X POST localhost:9000/_control -H "Content-Type: application/json" \
  -d '{"latency_min": 0.8, "latency_max": 3, "rate_429": 0.1, "retry_after": 2, "rate_500": 0.05}'
curl -s localhost:9000/_stats
```

| Setting | What it does |
| --- | --- |
| `latency_min`, `latency_max` | Seconds each answer is delayed, drawn uniformly. |
| `rate_429`, `rate_500` | Share of requests answered 429 (with `Retry-After`, set by `retry_after`) or 500. |
| `rate_timeout`, `hang_seconds` | Share of requests that hang for `hang_seconds`, past the client's timeout. |
| `rate_slow_stream`, `slow_stream_delay` | Share of streamed answers that pause `slow_stream_delay` seconds between chunks. |
| `seed` | Makes the faults repeatable. |
| `reset` | `true` zeroes the counters in `/_stats`. |

## Tests

```bash
uv run --no-sync pytest tests/test_fake_provider.py tests/test_support_desk_app.py -q
```

The second one runs the desk in process against the fake provider: a chat, a
refund that pauses, an approval over HTTP, the resume, exactly one row in the
ledger, and the trace of the ask and the approval.
