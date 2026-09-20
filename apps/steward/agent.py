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

import os

from omnicoreagent import MemoryRouter, OmniCoreAgent

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
            {"rule_id": "background_run", "capability": "background.run"},
            *[
                {"rule_id": f"background_task_{action}", "capability": f"background.task.{action}"}
                for action in ("create", "update", "pause", "resume", "delete")
            ],
            *[_rule(f"github_read_{tool}", tool) for tool in GITHUB_READS],
            {"rule_id": "sandbox", "capability": "sandbox.execute"},
            {"rule_id": "sandbox_process", "capability": "process.exec",
             "constraints": {"sandbox_required": True}},
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
"""

agent = OmniCoreAgent(
    name="steward",
    system_instruction=SYSTEM,
    model_config={
        "provider": "openai",
        "model": MODEL,
        "api_key": os.environ.get("LLM_API_KEY"),
        "max_tokens": 4000,
        "temperature": 0.2,
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
    agent_config={
        "max_steps": 40,
        "tool_call_timeout": 300,
        "request_limit": 60,
        "enable_workspace_files": True,
        "guardrail_mode": "full",
        "governance_config": {
            "enabled": True,
            "policy": POLICY,
            "budgets": BUDGETS,
            "approval_mode": "suspend",
            "sandbox_config": {"provider": "e2b", "options": {"timeout_seconds": 1200}},
        },
    },
    # Every model prompt and response is kept: the trace is the proof.
    telemetry_config={"capture": "full"},
)
