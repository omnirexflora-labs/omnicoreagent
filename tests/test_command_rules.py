"""Command rules are prefix rules (engineering/architecture/simple-policy-plan.md).

Only a plain chain, simple commands of literal words joined by &&, ||, ;, |
or a newline, is split into its commands; anything else is one unreadable
command. A deny or ask rule applies when any command of a readable line
begins with its prefix; an allow rule only when every command does and is
named without a path. An unreadable line meeting a policy with any command
rule for that capability is asked about (refused in strict), in every mode:
a deny rule fails closed. What a command can touch is the sandbox's job;
real-shell agreement is checked in test_command_parse_differential.py.
"""

from __future__ import annotations

import pytest

from omnicoreagent.governance.commands import attach_command, parse_command
from omnicoreagent.governance.errors import PolicyLoadError
from omnicoreagent.governance.evaluator import PolicyEvaluator
from omnicoreagent.governance.models import AuthorityRequest
from omnicoreagent.governance.policy import policy_from_mapping


def _policy(mode="permissive", deny=(), ask=(), allow=()):
    def rules(items, effect):
        return [{"rule_id": f"{effect}_{i}", "capability": "process.exec", **item} for i, item in enumerate(items)]

    return policy_from_mapping({"name": "p", "mode": mode, "rules": {
        "deny": rules(deny, "deny"), "ask": rules(ask, "ask"), "allow": rules(allow, "allow")}})


def _decide(policy, script):
    request = AuthorityRequest(capability="process.exec", execution_surface="sandbox", target={"resource": "sh"})
    attach_command(request, ["sh", "-c", script])
    decision = PolicyEvaluator().evaluate(policy, request)
    return decision.effect.value, decision.reason_code.value


DENY_RM = {"command": {"prefix": ["rm"]}}
DENY_PUSH = {"command": {"prefix": ["git", ["push", "reset"]]}}


# --- splitting -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "programs"),
    [
        ("git status", ["git"]),
        ("git status && rm -rf x", ["git", "rm"]),
        ("ls | head -5; pwd", ["ls", "head", "pwd"]),
        ("ls\npwd", ["ls", "pwd"]),
        ("git commit -m 'two words'", ["git"]),
        ('"rm" -rf x', ["rm"]),
        ("/bin/rm -rf x", ["rm"]),
        ("git push # a comment", ["git"]),
    ],
)
def test_a_plain_chain_is_split_into_its_commands(text, programs):
    parsed = parse_command(["sh", "-c", text])
    assert not parsed.opaque and [c.program for c in parsed.commands] == programs


@pytest.mark.parametrize(
    "text",
    [
        "echo x > f", "echo x >& f", "cat < f", "rm -rf $(pwd)", "rm -rf `pwd`", "echo $HOME",
        'echo "$HOME"', "rm *.py", "cat ~/.ssh/id_rsa", "r\\m -rf x", "FOO=1 make",
        "(cd x && rm -rf y)", "{ ls; }", "for f in a; do rm $f; done", "f() { rm x; }",
        "cat <<EOF\nx\nEOF", "eval 'rm -rf x'", "source ./x.sh", "sh -c 'rm -rf x'",
        "bash -lc 'rm -rf x'", "rm -rf x &", "{rm,-rf,x}", "time rm -rf x", "! rm x",
        "$'rm' -rf x", "r\\\nm -rf x", "x" * 10_001,
        # A shell reading commands from its input (the rc8 gate, S).
        "echo 'rm -rf x' | sh", "cat script.sh | bash", "bash", "sh -s", "bash -s -- a",
    ],
)
def test_anything_else_is_unreadable(text):
    parsed = parse_command(["sh", "-c", text])
    assert parsed.opaque and parsed.opaque_reasons
    assert parsed.summary == [parsed.summary[0]], "an unreadable line is shown whole"


def test_a_command_run_without_a_shell_is_one_command():
    parsed = parse_command(["git", "status"])
    assert not parsed.opaque and [c.argv for c in parsed.commands] == [("git", "status")]


def test_the_summary_shows_each_command_and_escapes_what_could_mislead():
    assert parse_command(["sh", "-c", "git status && rm -rf x"]).summary == ["git status", "rm -rf x"]
    assert parse_command(["sh", "-c", "echo a > f"]).summary == ["echo a > f"]
    assert parse_command(["sh", "-c", "echo safe‮ txt"]).summary == ["echo safe\\u202e txt"]


# --- deciding --------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["permissive", "interactive", "strict"])
def test_a_deny_rule_applies_to_any_command_of_a_readable_line(mode):
    policy = _policy(mode, deny=[DENY_RM])
    assert _decide(policy, "ls && rm -rf build")[0] == "deny"
    assert _decide(policy, "/bin/rm x")[0] == "deny"
    assert _decide(policy, "git push; echo rm")[1] != "matched_deny", "a later word is not the program"


@pytest.mark.parametrize(("mode", "effect"), [("permissive", "ask"), ("interactive", "ask"), ("strict", "deny")])
def test_an_unreadable_line_is_asked_about_where_command_rules_exist(mode, effect):
    policy = _policy(mode, deny=[DENY_RM], allow=[{"conditions": {"execution_surface": "sandbox"}}])
    assert _decide(policy, 'eval "rm -rf x"') == (effect, "command_opaque")
    assert _decide(policy, "echo x > f") == (effect, "command_opaque")


def test_without_command_rules_an_unreadable_line_is_decided_as_before():
    policy = _policy("permissive", allow=[{"conditions": {"execution_surface": "sandbox"}}])
    assert _decide(policy, "echo x > f")[0] == "allow"


def test_an_allow_rule_needs_every_command_and_a_bare_program():
    policy = _policy("strict", allow=[{"command": {"prefix": ["git", ["status", "log"]]}}, {"command": {"program": "ls"}}])
    assert _decide(policy, "git status && ls -la")[0] == "allow"
    assert _decide(policy, "git status && git push")[0] == "deny"
    assert _decide(policy, "./git status")[0] == "deny"
    assert _decide(policy, "git status > out")[0] == "deny"


def test_ask_rules_and_alternatives():
    policy = _policy("permissive", ask=[DENY_PUSH])
    assert _decide(policy, "git push origin main")[0] == "ask"
    assert _decide(policy, "git reset --hard")[0] == "ask"
    assert _decide(policy, "git status")[0] == "allow"


def test_wrappers_are_not_looked_inside():
    # Accepted (simple-policy plan, decision 3): `sudo rm` is a command named
    # sudo. A rule on sudo covers it; the sandbox is the boundary.
    policy = _policy("strict", deny=[DENY_RM], allow=[{"command": {"program": "sudo"}}])
    assert _decide(policy, "sudo rm -rf x")[0] == "allow"
    assert _decide(_policy("strict", deny=[{"command": {"program": "sudo"}}]), "sudo rm -rf x")[0] == "deny"


# --- writing rules ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ({"program": "rm", "args_any": ["-r"]}, "args_any was removed"),
        ({"prefix": ["git"], "redirect": True}, "redirect was removed"),
        ({"prefix": ["make"], "env": ["CI"]}, "env was removed"),
        ({"prefix": ["r*"]}, "literal"),
        ({"prefix": []}, "non-empty"),
        ({"prefix": ["git"], "program": "git"}, "a prefix, or a program"),
    ],
)
def test_a_rule_in_the_old_form_is_refused_with_the_new_one(command, message):
    with pytest.raises((PolicyLoadError, ValueError), match=message):
        _policy(deny=[{"command": command}])


def test_examples_are_checked_when_the_policy_loads():
    _policy(deny=[{"command": {"prefix": ["rm"]}, "examples": {"match": ["ls && rm x"], "not_match": ["echo rm"]}}])
    with pytest.raises((PolicyLoadError, ValueError), match="does not match its example"):
        _policy(deny=[{"command": {"prefix": ["rm"]}, "examples": {"match": ["sudo rm x"]}}])
