"""The ``omnicoreagent`` command line.

    omnicoreagent run --agent agent.py --instruction "fix the failing test" \\
        --approval-mode deny --output-dir ./out

``run`` executes one instruction with the core install alone: no server, no
serve extra. It is the entry point evaluation harnesses such as Harbor call
inside a task container. Exit codes name the terminal state:

    0 success            3 awaiting approval   5 timeout
    1 failed or error    4 awaiting budget     6 interrupted
    2 usage or agent file error
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from omnicoreagent.cli.agent_file import AgentFileError, load_agent
from omnicoreagent.cli.headless import (
    APPROVAL_MODES,
    BUDGET_MODES,
    ApprovalPolicy,
    ApprovalPolicyError,
    ExitCode,
    HeadlessRequest,
    _package_version,
    execute_headless,
    write_outputs,
)


class _StartupError(click.ClickException):
    exit_code = int(ExitCode.USAGE)


def _read_instruction(instruction: str | None, instruction_file: str | None) -> str:
    if (instruction is None) == (instruction_file is None):
        raise click.UsageError("Give exactly one of --instruction or --instruction-file")
    if instruction is not None:
        text = instruction
    elif instruction_file == "-":
        text = sys.stdin.read()
    else:
        try:
            text = Path(instruction_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise click.UsageError(f"Cannot read instruction file: {exc}") from exc
    if not text.strip():
        raise click.UsageError("The instruction is empty")
    return text


@click.group()
@click.version_option(version=_package_version(), prog_name="omnicoreagent")
def cli():
    """OmniCoreAgent command line."""


@cli.command()
@click.option("--agent", "-a", "agent_path", required=True,
              help="Python file defining 'agent' or 'create_agent()'.")
@click.option("--instruction", "-i", default=None, help="The instruction to run.")
@click.option("--instruction-file", "-f", default=None,
              help="Read the instruction from a file, or '-' for stdin.")
@click.option("--session-id", default=None, help="Session to run in (default: new).")
@click.option("--run-id", default=None, help="Run ID (default: generated).")
@click.option("--tag", "tags", multiple=True, help="Label recorded on the trace. Repeatable.")
@click.option("--provenance", "provenance_pairs", multiple=True, metavar="KEY=VALUE",
              help="Trace provenance, e.g. trial_id=abc. Unknown keys go to external_ids.")
@click.option("--approval-mode", type=click.Choice(APPROVAL_MODES), default="stop",
              show_default=True, help="How approvals the policy asks for are answered.")
@click.option("--approvals-file", default=None,
              help="JSON rules for --approval-mode scripted.")
@click.option("--budget-mode", type=click.Choice(BUDGET_MODES), default="stop",
              show_default=True, help="stop: exit when a budget runs out; deny: end the run cleanly.")
@click.option("--timeout", type=float, default=None, help="Deadline in seconds for the whole run.")
@click.option("--max-approval-rounds", type=int, default=20, show_default=True,
              help="Most pause-and-resume cycles before giving up.")
@click.option("--output-dir", "-o", default=None,
              help="Write result.json and trajectory.json here.")
@click.option("--json", "as_json", is_flag=True, help="Print result.json to stdout.")
def run(agent_path, instruction, instruction_file, session_id, run_id, tags,
        provenance_pairs, approval_mode, approvals_file, budget_mode, timeout,
        max_approval_rounds, output_dir, as_json):
    """Run one instruction unattended and exit with its terminal state."""
    from omnicoreagent.cli.headless import build_provenance

    text = _read_instruction(instruction, instruction_file)
    if approval_mode == "scripted" and not approvals_file:
        raise click.UsageError("--approval-mode scripted needs --approvals-file")
    if approvals_file and approval_mode != "scripted":
        raise click.UsageError("--approvals-file is only used with --approval-mode scripted")
    try:
        approvals = (
            ApprovalPolicy.from_file(approvals_file)
            if approval_mode == "scripted"
            else ApprovalPolicy(mode=approval_mode)
        )
        provenance = build_provenance(list(provenance_pairs))
        request = HeadlessRequest(
            instruction=text,
            session_id=session_id,
            run_id=run_id,
            tags=list(tags),
            provenance=provenance,
            approvals=approvals,
            budget_mode=budget_mode,
            timeout=timeout,
            max_approval_rounds=max_approval_rounds,
        )
    except (ApprovalPolicyError, ValueError) as exc:
        raise click.UsageError(str(exc)) from exc

    try:
        agent = load_agent(agent_path)
    except AgentFileError as exc:
        raise _StartupError(str(exc)) from exc

    async def main():
        try:
            return await execute_headless(agent, request)
        finally:
            try:
                await agent.cleanup()
            except Exception as exc:
                click.echo(f"warning: agent cleanup failed: {exc}", err=True)

    outcome = asyncio.run(main())
    if output_dir:
        write_outputs(outcome, output_dir)
    if as_json:
        click.echo(json.dumps(outcome.result_document(), indent=2, default=str))
    elif outcome.response is not None:
        click.echo(outcome.response if isinstance(outcome.response, str) else json.dumps(
            outcome.response, default=str))
    click.echo(
        f"status={outcome.status} exit={outcome.exit_code} run_id={outcome.run_id}"
        + (f" error={outcome.error}" if outcome.error else ""),
        err=True,
    )
    sys.exit(outcome.exit_code)


from omnicoreagent.cli.harbor import harbor_command  # noqa: E402

cli.add_command(harbor_command)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
