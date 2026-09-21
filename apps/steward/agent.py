"""The repository steward: an agent that keeps omnicoreagent's own repository.

Served by OmniServe (``omniserve run --agent apps/steward/agent.py``). It finds
work on a schedule — a failing test, an open issue, a failed run in its own
telemetry — reproduces it in a sandbox against the real test suite, writes a
fix, and opens a pull request for a person to review. It cannot push, comment
publicly, or merge without an approval; merging is not in its capability set
at all.

Everything it may do is written in the policy below, which is hashed, so it
cannot be widened at runtime; what it may spend is written there too.
Secrets come from the environment and never reach a sandbox.

See ``engineering/architecture/production-proving-plan.md``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from omnicoreagent import MemoryRouter, OmniCoreAgent
from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

REPOSITORY = os.environ.get("STEWARD_REPOSITORY", "omnirexflora-labs/omnicoreagent")
MODEL = os.environ.get("STEWARD_MODEL", "gpt-5.6-terra")
# The hosted GitHub MCP server; the token is the steward's own, from the
# environment of the server it runs on.
GITHUB_MCP_URL = os.environ.get("STEWARD_GITHUB_MCP_URL", "https://api.githubcopilot.com/mcp/")

# Tools the steward may call without asking (reads), must ask for (writes a
# person should see), and may never call (nothing in the plan needs them).
GITHUB_READS = (
    "get_me", "get_file_contents", "get_commit", "list_commits", "list_branches",
    "list_issues", "issue_read", "list_pull_requests", "pull_request_read",
    "search_code", "search_issues", "search_pull_requests", "list_issue_types",
    "list_issue_fields", "get_label", "list_tags", "list_releases",
    "get_latest_release", "get_release_by_tag", "get_tag",
)
GITHUB_WRITES_THAT_ASK = (
    "create_branch", "push_files", "create_or_update_file", "create_pull_request",
    "update_pull_request", "update_pull_request_branch", "add_issue_comment",
    "issue_write", "sub_issue_write", "update_issue_comment",
)
GITHUB_NEVER = (
    "merge_pull_request", "delete_file", "create_repository", "fork_repository",
    "pull_request_review_write", "add_comment_to_pending_review",
    "add_reply_to_pull_request_comment", "request_copilot_review",
    "run_secret_scanning",
)


def _rule(rule_id: str, tool: str, *, reason: str | None = None) -> dict:
    rule = {
        "rule_id": rule_id,
        "capability": "tool.mcp.call",
        "target": {"mcp_server": "github", "tool_name": tool},
    }
    if reason:
        rule["reason"] = reason
    return rule


POLICY = {
    "name": "repository-steward",
    "mode": "strict",
    "rules": {
        "allow": [
            # Reaching the GitHub MCP server at all is a capability of its own,
            # and so is running as a background task.
            {"rule_id": "github_connect", "capability": "mcp.server.connect",
             "target": {"mcp_server": "github"}},
            *[
                {"rule_id": f"background_run_{action}", "capability": f"background.run.{action}"}
                for action in ("start", "cancel")
            ],
            *[
                {"rule_id": f"background_task_{action}", "capability": f"background.task.{action}"}
                for action in ("create", "update", "pause", "resume", "delete")
            ],
            *[_rule(f"github_read_{tool}", tool) for tool in GITHUB_READS],
            {"rule_id": "sandbox", "capability": "sandbox.execute"},
            {"rule_id": "sandbox_process", "capability": "process.exec",
             "constraints": {"sandbox_required": True}},
            # The sandbox clones the repository and installs it, so it has the
            # network. Nothing secret is in it: the steward's tokens stay in
            # this process.
            {"rule_id": "sandbox_network", "capability": "sandbox.network.configure"},
            {"rule_id": "workspace_read", "capability": "workspace.files.read"},
            {"rule_id": "workspace_write", "capability": "workspace.files.write"},
            {"rule_id": "local_tools", "capability": "tool.local.call"},
            {"rule_id": "delegate", "capability": "subagent.spawn"},
        ],
        "ask": [
            _rule(f"github_write_{tool}", tool,
                  reason="A change on GitHub is a person's decision; the branch must be steward/*.")
            for tool in GITHUB_WRITES_THAT_ASK
        ],
        "deny": [
            _rule(f"github_never_{tool}", tool, reason="Not part of the steward's job.")
            for tool in GITHUB_NEVER
        ],
    },
}

BUDGETS = {
    # Deliberately low while proving: the cap should be hit early and seen.
    "application_id": "steward",
    "application": [
        {"meter": "model_cost_usd", "limit": float(os.environ.get("STEWARD_DAILY_USD", "1.00")),
         "window": "day", "warn_at": 0.5},
    ],
    "request": [
        {"meter": "model_cost_usd", "limit": float(os.environ.get("STEWARD_REQUEST_USD", "0.20")),
         "warn_at": 0.5},
        {"meter": "tool_calls", "limit": 60},
    ],
}

SANDBOX_HOME = "/home/user"
SANDBOX_WORKDIR = f"{SANDBOX_HOME}/workspace"
REPO_CHECKOUT = f"{SANDBOX_HOME}/repo"

SYSTEM = f"""You are the repository steward for {REPOSITORY}.

Your job, each time you are run: find one piece of work worth doing — a failing
test, an open issue that can be reproduced, a failed run in the runtime's own
telemetry — reproduce it in a sandbox against the real test suite, and if you
can fix it, prepare a fix and open a pull request for a person to review.

Rules you work under (the policy enforces them; this is so you plan for them):
- Every write to GitHub asks a person first. Only branches named steward/... may
  be created or pushed to. Never touch main. You cannot merge anything.
- Read before you write: look at the repository, the issue, and the test first.
- Reproduce before you fix: a fix without a reproduction is a guess.
- Say what you did and what you did not do, plainly, in the pull request body,
  and link the run that produced it.
- You have a small budget. Prefer one thing done well to many things started.

How to reproduce something (the `execute` tool runs commands in a sandbox):
- The sandbox has the network and no credentials. The repository is public:
  `git clone -b <branch> --depth 1 https://github.com/{REPOSITORY}.git {REPO_CHECKOUT}`
  then `cd {REPO_CHECKOUT} && pip install -q uv && uv sync`.
- Clone into {REPO_CHECKOUT}, never into {SANDBOX_WORKDIR}: everything under
  {SANDBOX_WORKDIR} is copied back to your workspace after each command.
- Run one test as `uv run pytest <path::name> -q -p no:cacheprovider`, then its
  file the same way, and keep the exact failing lines: the assertion or
  exception and the test's name. Do not paraphrase them.
- If the sandbox is lost mid-command, the next command gets a fresh one: clone
  and install again, then continue.
- Delegate the reproduction to one worker (spawn_subagents, name it
  `reproduce`) and give it an output path under this run's workspace; read
  that output before you write your own report to output.md in the run's
  workspace, quoting the failing lines and saying what you did not do.

How to fix something (after it is reproduced):
- Delegate the fix to one worker named `fix`: in its sandbox it makes the
  smallest change that makes the test pass, runs the test and its file green,
  and writes to its output path the full new contents of every changed file
  (fenced, with the repository-relative path above each) and a commit message.
- Workers never write to GitHub. You do, and every write asks a person, so
  the run will pause: `create_branch` (name it steward/<topic>-<the first 8
  characters of this run id>, from main), then `push_files` (all changed files,
  one commit, the worker's message), then `create_pull_request` to main.
- The pull request body says: the failure and how it was reproduced, the
  change and how it was verified, what you did not do, and this run's id and
  trace id so a person can read the trace.
- If you are told a call's outcome is unknown (the process stopped while it
  ran), check first — list_branches, get_file_contents on the branch,
  list_pull_requests — and never push the same commit twice.
- A fix has landed only if its pull request is open or merged. Read the
  pull request's `state` and `merged` from the tool, not from what you
  remember of an earlier run, and report them as the tool gives them. A
  closed, unmerged pull request is a fix that did not land: do the work again
  on a new branch, and say in the new body that the earlier one was closed.

How to find work (triage):
- Your own failed runs are work: list_failed_runs. The repository's open
  issues and its failing tests are work: list_issues, search_issues.
- Cluster what you find by cause, not by occurrence: three runs that failed
  the same way are one item. Read list_work_items first; an item that exists
  is not scheduled again.
- schedule_work_item once per cause, with a query a future run of yours can
  act on alone (what to reproduce, where, what counts as done). Report the
  items you scheduled and the ones that already existed.
"""

# --- the steward's own tools: its telemetry as a source of work -----------------

WORK_ITEM_PREFIX = "steward-work-"
OWN_API = os.environ.get("STEWARD_OWN_API", "http://127.0.0.1:8000")

tools = ToolRegistry()


async def _own_api(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """The steward's own OmniServe, on loopback, with its own token.

    In a thread: the server that answers runs on this event loop, so a call
    that blocked the loop while waiting would wait for itself — and stall
    every heartbeat with it, until the run's lease expired (P5 found this).
    """
    return await asyncio.to_thread(_own_api_blocking, method, path, body)


def _own_api_blocking(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    token = os.environ.get("OMNICOREAGENT_SERVE_AUTH_TOKEN", "")
    request = urllib.request.Request(
        f"{OWN_API}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b"{}")
        except ValueError:
            return error.code, {}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


@tools.register_tool(
    name="list_failed_runs",
    description=(
        "The steward's own runs that failed, timed out, or were stopped by a guard, "
        "newest first, with the first error each recorded. Use it to find work: a "
        "failed run is a defect of the runtime or of the steward's instructions."
    ),
    inputSchema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 15}},
        "additionalProperties": False,
    },
    idempotent=True,
)
async def list_failed_runs(limit: int = 15) -> dict:
    from omnicoreagent.core.telemetry.models import TraceFilter, TraceStatus

    store = agent.telemetry_store
    if store is None:
        return {"status": "error", "message": "The steward keeps no telemetry."}
    failed: list[dict] = []
    for status in (TraceStatus.FAILED, TraceStatus.TIMEOUT, TraceStatus.ABORTED_SAFETY_GUARD,
                   TraceStatus.ABORTED_RESOURCE_GUARD):
        for trace in await store.list_traces(TraceFilter(status=status)):
            error = next(
                (
                    f"{e.event_type}: {(e.error or {}).get('message') if isinstance(e.error, dict) else getattr(e.error, 'message', None) or ''}"
                    for e in trace.events
                    if e.error is not None
                ),
                None,
            )
            failed.append({
                "run_id": trace.run_id,
                "trace_id": trace.trace_id,
                "status": getattr(trace.status, "value", trace.status),
                "started_at": trace.started_at.isoformat(),
                "first_error": (error or "")[:300],
            })
    failed.sort(key=lambda item: item["started_at"], reverse=True)
    return {"failed_runs": failed[:limit], "total": len(failed)}


@tools.register_tool(
    name="list_work_items",
    description="The work items the steward has already scheduled (one background task each). Read this before scheduling anything, so one cause gets one item.",
    inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    idempotent=True,
)
async def list_work_items() -> dict:
    status, body = await _own_api("GET", "/background/tasks")
    tasks = body.get("tasks", body) if isinstance(body, dict) else body
    items = [
        {"item_id": t["task_id"][len(WORK_ITEM_PREFIX):], "cause": (t.get("metadata") or {}).get("cause"),
         "title": (t.get("metadata") or {}).get("title"), "enabled": t.get("enabled")}
        for t in (tasks or [])
        if str(t.get("task_id", "")).startswith(WORK_ITEM_PREFIX)
    ]
    return {"work_items": items}


@tools.register_tool(
    name="schedule_work_item",
    description=(
        "Schedule one piece of work as a background task of the steward's, identified by its "
        "cause. The id is derived from the cause, so the same cause scheduled twice is one item: "
        "the second call reports it already exists and schedules nothing."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "cause": {"type": "string", "description": "What is wrong, in one short line (the item's identity)."},
            "title": {"type": "string", "description": "A short title for a person."},
            "query": {"type": "string", "description": "The instruction the steward will run for this item."},
        },
        "required": ["cause", "title", "query"],
        "additionalProperties": False,
    },
)
async def schedule_work_item(cause: str, title: str, query: str) -> dict:
    item_id = _slug(cause)
    if not item_id:
        return {"status": "error", "message": "The cause must name something."}
    task_id = WORK_ITEM_PREFIX + item_id
    status, existing = await _own_api("GET", f"/background/tasks/{task_id}")
    if status == 200:
        return {"status": "success", "data": {"item_id": item_id, "already_scheduled": True}}
    status, body = await _own_api("POST", "/background/tasks", {
        "task_id": task_id,
        "agent_id": "steward",
        "query": query,
        "schedule": {"type": "manual"},
        "timeout_seconds": 2400,
        "metadata": {"cause": cause, "title": title, "scheduled_by": "triage"},
    })
    if status >= 300:
        return {"status": "error", "message": f"Could not schedule: HTTP {status} {json.dumps(body)[:200]}"}
    return {"status": "success", "data": {"item_id": item_id, "already_scheduled": False}}


agent = OmniCoreAgent(
    name="steward",
    system_instruction=SYSTEM,
    model_config={
        "provider": "openai",
        "model": MODEL,
        "api_key": os.environ.get("LLM_API_KEY"),
        # No temperature: a reasoning model rejects any value but its own.
        "max_tokens": 4000,
    },
    mcp_tools=[
        {
            "name": "github",
            "transport_type": "streamable_http",
            "url": GITHUB_MCP_URL,
            "headers": {
                "Authorization": f"Bearer {os.environ.get('GITHUB_PERSONAL_ACCESS_TOKEN', '')}",
            },
            "timeout": 60,
        }
    ],
    # Memory, run state and budgets live in Postgres (DATABASE_URL); the task
    # store is Redis, set on the server (see compose.yml).
    memory_router=MemoryRouter("sql"),
    local_tools=tools,
    agent_config={
        "max_steps": 40,
        "tool_call_timeout": 300,
        "request_limit": 60,
        "enable_workspace_files": True,
        # Work is delegated to workers under the same policy and budgets.
        "enable_subagents": True,
        "guardrail_mode": "full",
        "governance_config": {
            "enabled": True,
            "policy": POLICY,
            "budgets": BUDGETS,
            "approval_mode": "suspend",
            "sandbox_config": {"provider": "e2b", "options": {"timeout_seconds": 1200}},
            # What the sandbox is: on the network (the policy allows it), with
            # the workspace bridged into a directory the sandbox user owns.
            "sandbox_manifest": {
                "network_policy": {"default": "allow"},
                "working_dir": SANDBOX_WORKDIR,
            },
        },
    },
    # Every model prompt and response is kept: the trace is the proof.
    telemetry_config={"capture": "full"},
)


# --- the page: what the steward is doing, for anyone with the token --------------
# Served by OmniServe beside the API (same origin, same tunnel); the page itself
# needs no token, everything it reads does.

router = APIRouter()
public_paths = ["/steward/"]
_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")


@router.get("/steward/", response_class=HTMLResponse, include_in_schema=False)
async def steward_page() -> str:
    with open(_PAGE, encoding="utf-8") as page:
        return page.read()
