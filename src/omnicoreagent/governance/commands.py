"""What a shell command would really run, for policy rules on command text.

Every command reaches the policy as ``sh`` (the execute tool runs ``sh -c
<text>``), so no rule could name ``rm -rf`` (found recording real footage of
0.4.3, 2026-09-29). This parses the text with tree-sitter's bash grammar into
the simple commands it contains, wherever they sit: in lists and pipelines,
inside ``$(...)``, ``<(...)``, heredocs, loops and functions, behind wrappers
(``sudo``, ``timeout``, ``env``...), inside a literal ``sh -c '...'``, and after
``xargs`` or ``find -exec``.

A parse cannot prove everything: a program built from a variable, ``eval`` or
``source``, piping into a shell, text the grammar cannot read. Such a command is
**opaque**. A deny or ask rule still applies to whatever did parse in it; an
allow rule never applies to it. Parsing narrows intent; a sandbox is the
boundary (see engineering/architecture/command-policy-plan.md).

The parse is pure: no process is started and nothing is read from disk.
"""

from __future__ import annotations

import hashlib
import shlex
import re
import json
import posixpath
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Sequence

# Longer than this is opaque: a command a person could not review either.
MAX_COMMAND_CHARS = 10_000
# A literal ``sh -c '...'`` is parsed inside, this many shells deep; deeper is opaque.
MAX_SHELL_DEPTH = 3

SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "mksh", "ash", "busybox"})
# Programs that run a program read from their input when given no script.
INTERPRETERS = SHELLS | frozenset(
    {"python", "python3", "perl", "ruby", "node", "php", "lua", "tclsh", "osascript", "pwsh"}
)
SYSTEM_DIRS = ("/bin/", "/usr/bin/", "/usr/local/bin/", "/sbin/", "/usr/sbin/")
# Wrappers that run the rest of their arguments as a command, and how many
# arguments each of their options takes (unknown options take none).
WRAPPERS: dict[str, dict[str, int]] = {
    "sudo": {"-u": 1, "-g": 1, "-C": 1, "-D": 1, "-h": 1, "-p": 1, "-r": 1, "-t": 1, "-U": 1},
    "doas": {"-u": 1, "-C": 1},
    "env": {"-u": 1, "-C": 1, "-S": 1, "--unset": 1, "--chdir": 1},
    "timeout": {"-s": 1, "-k": 1, "--signal": 1, "--kill-after": 1},
    "nice": {"-n": 1, "--adjustment": 1},
    "ionice": {"-c": 1, "-n": 1, "-p": 1},
    "stdbuf": {"-i": 1, "-o": 1, "-e": 1},
    "nohup": {},
    "time": {"-f": 1, "-o": 1},
    "command": {},
    "exec": {"-a": 1},
    "builtin": {},
    "chroot": {},
    "setsid": {},
    "unbuffer": {},
}
# Of those, the ones whose first operand is not the command.
_OPERAND_BEFORE_COMMAND = {"timeout": 1, "chroot": 1}
_NOT_A_PROGRAM = frozenset({"eval", "source", ".", "alias"})


@dataclass(frozen=True)
class SimpleCommand:
    """One program the command would run, with its arguments."""

    argv: tuple[str, ...]
    program: str
    path_kind: str = "bare"          # "bare", "system_abs" (/usr/bin/...), or "other"
    plain: bool = True               # literal program and arguments, nothing expanded
    redirects_to_file: bool = False  # output sent to a file (not /dev/null)
    env_assignments: tuple[str, ...] = ()
    via: tuple[str, ...] = ()        # how it was reached: "sudo", "sh -c", "$()", "xargs"...
    text: str = ""                   # as written, with its settings and redirects


@dataclass(frozen=True)
class ParsedCommand:
    commands: tuple[SimpleCommand, ...]
    opaque: bool
    opaque_reasons: tuple[str, ...]
    digest: str                      # of the exact argv: binds an approval to this command
    text: str = ""

    @property
    def summary(self) -> list[str]:
        """What a person approving it should see: each program with its arguments,
        quoted as the shell reads them, one line each.

        Joined with spaces, `git commit -m 'add c'` read as `git commit -m add
        c`, and a quoted newline looked like a second command (the 0.5.0rc4
        gate).
        """
        return [_visible(c.text) if c.text else " ".join(_shown(a) for a in c.argv) for c in self.commands]


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _visible(text: str) -> str:
    """The command as written, on one line: a control character is shown, never
    acted on, so a quoted newline cannot pass for a second command. Written as
    the shell reads it, redirects, settings and `~` included: rebuilt from its
    arguments it hid `>> ~/.ssh/authorized_keys` and `GIT_SSH_COMMAND=...`
    (the 0.5.0rc5 gate)."""
    return "".join(
        {"\n": "\\n", "\t": "\\t", "\r": "\\r"}.get(ch)
        or (f"\\x{ord(ch):02x}" if _CONTROL.match(ch) else ch)
        for ch in text
    ).strip()


def _shown(argument: str) -> str:
    if not _CONTROL.search(argument):
        return shlex.quote(argument)
    # bash's $'...' form: a control character is written, never acted on.
    escaped = "".join(
        {"\n": "\\n", "\t": "\\t", "\r": "\\r", "\\": "\\\\", "'": "\\'"}.get(ch)
        or (f"\\x{ord(ch):02x}" if _CONTROL.match(ch) else ch)
        for ch in argument
    )
    return f"$'{escaped}'"


def command_digest(argv: Sequence[str]) -> str:
    return hashlib.sha256(json.dumps(list(argv), ensure_ascii=False).encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _parser():
    import tree_sitter
    import tree_sitter_bash

    return tree_sitter.Parser(tree_sitter.Language(tree_sitter_bash.language()))


def shell_script(argv: Sequence[str]) -> str | None:
    """The script of ``sh -c <script>`` (any shell, flags such as ``-lc``), else None."""
    if not argv or posixpath.basename(argv[0]) not in SHELLS:
        return None
    for i, arg in enumerate(argv[1:], start=1):
        if arg == "--":
            return None
        if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
            return argv[i + 1] if i + 1 < len(argv) else None
        if not arg.startswith("-"):
            return None
    return None


def parse_command(argv: Sequence[str]) -> ParsedCommand:
    """Parse what ``argv`` would run. Never raises: what it cannot read is opaque."""
    argv = [str(a) for a in argv]
    digest = command_digest(argv)
    state = _State()
    try:
        script = shell_script(argv)
        if script is None:
            _from_argv(argv, via=(), plain=True, state=state, depth=0)
        else:
            # The shell the runtime wraps every command in is not one of its
            # commands; a shell the command itself starts is.
            _parse_script(script, via=(), state=state, depth=1)
    except Exception as exc:  # the parser must never be the reason a run crashes
        state.opaque(f"parser failed: {exc.__class__.__name__}")
    text = script if (script := shell_script(argv)) is not None else " ".join(argv)
    return ParsedCommand(
        commands=tuple(state.commands),
        opaque=bool(state.reasons),
        opaque_reasons=tuple(dict.fromkeys(state.reasons)),
        digest=digest,
        text=text,
    )


@dataclass
class _State:
    commands: list[SimpleCommand] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def add(self, command: SimpleCommand | None) -> None:
        if command is not None:
            self.commands.append(command)

    def opaque(self, reason: str) -> None:
        self.reasons.append(reason)


def _parse_script(script: str, *, via: tuple[str, ...], state: _State, depth: int) -> None:
    if depth > MAX_SHELL_DEPTH:
        state.opaque("shells nested too deep")
        return
    if len(script) > MAX_COMMAND_CHARS:
        state.opaque(f"too long to review (over {MAX_COMMAND_CHARS} characters)")
        return
    source = script.encode("utf-8")
    tree = _parser().parse(source)
    if tree.root_node.has_error:
        state.opaque("parse error: the shell grammar cannot read it")
    _walk(tree.root_node, source, via=via, state=state, depth=depth)


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _walk(node, source: bytes, *, via: tuple[str, ...], state: _State, depth: int) -> None:
    kind = node.type
    if kind == "command":
        _command(node, source, via=via, state=state, depth=depth, redirect=False, text=_text(node, source))
        return
    if kind == "redirected_statement":
        body = node.child_by_field_name("body")
        redirects = [r for r in node.children if r.type == "file_redirect"]
        to_file = any(_writes_to_file(r, source) for r in redirects)
        for child in node.children:
            # Nodes are new objects on each access: compare by position.
            is_body = body is not None and (child.start_byte, child.end_byte) == (body.start_byte, body.end_byte)
            if is_body and child.type == "command":
                _command(
                    child, source, via=via, state=state, depth=depth, redirect=to_file,
                    text=_text(node, source),
                )
            elif is_body:
                # A list, pipeline or group redirected as a whole
                # (`a && b > f`, `{ a; b; } > f`): the redirect applies to the
                # commands inside. Carried only onto a single command, it was
                # dropped here: the approver read `echo k` for `echo k >>
                # ~/.ssh/authorized_keys`, and an allow rule without
                # `redirect` let the write through (the 0.5.0rc6 gate).
                first = len(state.commands)
                _walk(child, source, via=via, state=state, depth=depth)
                suffix = " ".join(_text(r, source) for r in redirects)
                targets = _redirect_targets(child)
                for index in range(first, len(state.commands)):
                    command = state.commands[index]
                    # Within the part the redirect reaches; when unsure, the
                    # write is shown rather than hidden.
                    if targets is not None and command.text and not any(
                        command.text in t for t in targets
                    ):
                        continue
                    state.commands[index] = replace(
                        command,
                        redirects_to_file=command.redirects_to_file or to_file,
                        text=f"{command.text} {suffix}".strip() if command.text else command.text,
                    )
            else:
                _walk(child, source, via=via, state=state, depth=depth)
        return
    if kind == "function_definition":
        state.opaque("defines a function: what runs depends on calls made later")
    elif kind in {"command_substitution"}:
        via = via + ("$()",)
    elif kind == "process_substitution":
        via = via + ("<()",)
    elif kind == "pipeline":
        _pipeline(node, source, state=state)
    for child in node.children:
        _walk(child, source, via=via, state=state, depth=depth)


def _redirect_targets(body) -> set[str] | None:
    """Which commands of a redirected body the redirect reaches: in a list
    (`a && b > f`) only the last; in a group or a pipeline's last stage, all
    of the group. None means every command in the body."""
    if body.type == "list":
        last = body.children[-1] if body.children else None
        return None if last is None else {_node_text_key(last)}
    if body.type == "pipeline":
        stages = [c for c in body.children if c.is_named]
        return None if not stages else {_node_text_key(stages[-1])}
    return None


def _node_text_key(node) -> str:
    return node.text.decode("utf-8", "replace") if node.text is not None else ""


def _pipeline(node, source: bytes, *, state: _State) -> None:
    """A stage after the first that is a shell or interpreter reading its input
    runs a program nobody can see (``... | base64 -d | sh``)."""
    stages = [c for c in node.children if c.type in {"command", "redirected_statement"}]
    for stage in stages[1:]:
        command = stage if stage.type == "command" else stage.child_by_field_name("body")
        if command is None or command.type != "command":
            continue
        words = _words(command, source)
        if not words or words[0] is None:
            continue
        program = posixpath.basename(words[0])
        operands = [w for w in words[1:] if w is None or not w.startswith("-")]
        if program in INTERPRETERS and not operands and shell_script(words) is None:
            state.opaque(f"pipes into {program}, which reads a program from its input")


def _writes_to_file(redirect, source: bytes) -> bool:
    operator = next((c for c in redirect.children if not c.is_named), None)
    op = _text(operator, source) if operator is not None else ""
    if "<" in op and ">" not in op:
        return False
    destination = redirect.child_by_field_name("destination") or redirect.children[-1]
    target = _literal(destination, source)
    if op.startswith(">&") or op.endswith("&") and target is not None and target.isdigit():
        return False
    return target != "/dev/null"


def _words(command, source: bytes) -> list[str | None]:
    """The command's words as literals (None where a word is not literal)."""
    words: list[str | None] = []
    name = command.child_by_field_name("name")
    if name is not None:
        words.append(_literal(name, source))
    for child in command.children_by_field_name("argument"):
        words.append(_literal(child, source))
    return words


def _command(
    node, source: bytes, *, via, state: _State, depth: int, redirect: bool, text: str = ""
) -> None:
    env = tuple(
        _text(c.child_by_field_name("name") or c.children[0], source)
        for c in node.children
        if c.type == "variable_assignment"
    )
    words = _words(node, source)
    if words and words[0] is None:
        state.opaque("dynamic program: its name comes from an expansion")
    elif words:
        plain = all(w is not None for w in words)
        argv = [w if w is not None else _text_of_argument(node, i, source) for i, w in enumerate(words)]
        _from_argv(
            argv, via=via, plain=plain, state=state, depth=depth, env=env, redirect=redirect, text=text
        )
    # Substitutions inside the arguments (or the assignments) run too.
    for child in node.children:
        if child.type not in {"command_name", "word", "number", "raw_string"}:
            _walk(child, source, via=via, state=state, depth=depth)
    name = node.child_by_field_name("name")
    if name is not None:
        for child in name.children:
            if child.type != "word":
                _walk(child, source, via=via, state=state, depth=depth)


def _text_of_argument(node, index: int, source: bytes) -> str:
    parts = [node.child_by_field_name("name"), *node.children_by_field_name("argument")]
    return _text(parts[index], source) if index < len(parts) and parts[index] is not None else ""


def _from_argv(
    argv: Sequence[str],
    *,
    via: tuple[str, ...],
    plain: bool,
    state: _State,
    depth: int,
    env: tuple[str, ...] = (),
    redirect: bool = False,
    text: str = "",
) -> SimpleCommand | None:
    if not argv:
        return None
    first = argv[0]
    program = posixpath.basename(first)
    if "/" not in first:
        path_kind = "bare"
    elif first.startswith(SYSTEM_DIRS):
        path_kind = "system_abs"
    else:
        path_kind = "other"
    command = SimpleCommand(
        argv=tuple(argv), program=program, path_kind=path_kind, plain=plain,
        redirects_to_file=redirect, env_assignments=env, via=via, text=text,
    )
    state.add(command)
    # What this program runs in turn is a command too.
    if program in _NOT_A_PROGRAM:
        state.opaque(f"{program}: what it runs is decided when it runs")
        if program == "eval" and plain and len(argv) > 1:
            _parse_script(" ".join(argv[1:]), via=via + ("eval",), state=state, depth=depth + 1)
    elif program in SHELLS:
        script = shell_script(argv)
        dash_c = any(a.startswith("-") and not a.startswith("--") and "c" in a[1:] for a in argv[1:])
        if dash_c and (script is None or not plain):
            state.opaque("dynamic script given to a shell")
        elif script is not None:
            _parse_script(script, via=via + (f"{program} -c",), state=state, depth=depth + 1)
    elif program in WRAPPERS:
        inner = _unwrap(program, list(argv[1:]))
        if inner:
            _from_argv(inner, via=via + (program,), plain=plain, state=state, depth=depth)
    elif program == "xargs":
        inner = _after_options(list(argv[1:]), {"-I": 1, "-L": 1, "-n": 1, "-P": 1, "-d": 1, "-E": 1, "-s": 1, "-a": 1})
        if inner:
            _from_argv(inner, via=via + ("xargs",), plain=False, state=state, depth=depth)
    elif program == "find":
        args = list(argv[1:])
        for i, arg in enumerate(args):
            if arg in {"-exec", "-execdir", "-ok", "-okdir"}:
                end = next((j for j in range(i + 1, len(args)) if args[j] in {";", "+"}), len(args))
                if end > i + 1:
                    _from_argv(args[i + 1 : end], via=via + ("find -exec",), plain=False, state=state, depth=depth)
    return command


def _after_options(args: list[str], takes: dict[str, int]) -> list[str]:
    i = 0
    while i < len(args) and args[i].startswith("-") and args[i] != "-":
        if args[i] == "--":
            return args[i + 1 :]
        i += 1 + takes.get(args[i], 0)
    return args[i:]


def _unwrap(wrapper: str, args: list[str]) -> list[str]:
    rest = _after_options(args, WRAPPERS[wrapper])
    if wrapper == "env":
        while rest and "=" in rest[0] and not rest[0].startswith("="):
            rest = rest[1:]
    skip = _OPERAND_BEFORE_COMMAND.get(wrapper, 0)
    return rest[skip:]


def _literal(node, source: bytes) -> str | None:
    """The literal value of a word, or None if it depends on an expansion."""
    kind = node.type
    if kind in {"command_name"}:
        return _literal(node.children[0], source) if node.children else None
    if kind in {"word", "number"}:
        return _unescape(_text(node, source))
    if kind == "raw_string":
        return _text(node, source)[1:-1]
    if kind == "string":
        out = []
        for child in node.children:
            if child.type in {'"'}:
                continue
            if child.type == "string_content":
                out.append(_unescape_double(_text(child, source)))
            else:
                return None
        return "".join(out)
    if kind == "concatenation":
        parts = [_literal(c, source) for c in node.children]
        return None if any(p is None for p in parts) else "".join(parts)
    return None


def _unescape(word: str) -> str:
    out, i = [], 0
    while i < len(word):
        if word[i] == "\\" and i + 1 < len(word):
            out.append(word[i + 1])
            i += 2
        else:
            out.append(word[i])
            i += 1
    return "".join(out)


def _unescape_double(content: str) -> str:
    out, i = [], 0
    while i < len(content):
        if content[i] == "\\" and i + 1 < len(content) and content[i + 1] in '"\\$`\n':
            out.append(content[i + 1])
            i += 2
        else:
            out.append(content[i])
            i += 1
    return "".join(out)


# --- Matching rules to commands ------------------------------------------------------

_PARSED = "_omnicoreagent_parsed_command"


def attach_command(request, argv: Sequence[str]):
    """Parse ``argv`` and attach it to a ``process.exec`` request.

    The parse rides on the request as an attribute, not a field, so it is never
    serialized. The metadata, which governance events record as it is, gets no
    argument: only the program names, whether it is opaque, and the digest that
    binds an approval to the exact command. Arguments can hold secrets
    (``curl -H "Authorization: ..."``); the full sub-commands go only on an
    approval, which a person reads and which traces keep under the capture
    policy (``approval_metadata``).
    """
    argv = list(argv)
    parsed = parse_command(argv)
    object.__setattr__(request, _PARSED, parsed)
    request.metadata["command"] = {
        "name": argv[0] if argv else "",
        "argc": len(argv),
        "digest": parsed.digest,
        "programs": [c.program for c in parsed.commands],
        "opaque": parsed.opaque,
    }
    return request


def approval_metadata(request) -> dict[str, Any]:
    """An approval's metadata: the request's, plus the sub-commands a person
    approving a command must see (not ``sh`` and an argument count)."""
    metadata = dict(request.metadata)
    parsed = parsed_command(request)
    if parsed is not None:
        metadata["command"] = {
            **(metadata.get("command") or {}),
            "summary": parsed.summary,
            "opaque_reasons": list(parsed.opaque_reasons),
        }
    return metadata


def parsed_command(request) -> ParsedCommand | None:
    return getattr(request, _PARSED, None)


def _globs(value) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def _arguments(argv: Sequence[str]) -> list[str]:
    """Arguments after the program, with short-option clusters expanded:
    ``-rf`` also counts as ``-r`` and ``-f``."""
    out: list[str] = []
    for arg in argv[1:]:
        out.append(arg)
        if len(arg) > 2 and arg.startswith("-") and not arg.startswith("--") and arg[1:].isalpha():
            out.extend(f"-{letter}" for letter in arg[1:])
    return out


def command_matches(matcher, command: SimpleCommand) -> bool:
    """Whether one simple command matches a rule's ``command`` matcher."""
    from fnmatch import fnmatchcase

    if matcher.program is not None and not any(fnmatchcase(command.program, g) for g in _globs(matcher.program)):
        return False
    if matcher.prefix is not None:
        words = [command.program, *command.argv[1:]]
        if len(words) < len(matcher.prefix):
            return False
        for word, token in zip(words, matcher.prefix):
            if not any(fnmatchcase(word, g) for g in _globs(token)):
                return False
    if matcher.args_any is not None:
        arguments = _arguments(command.argv)
        if not any(fnmatchcase(a, g) for a in arguments for g in matcher.args_any):
            return False
    return True


def allowable(matcher, command: SimpleCommand) -> bool:
    """Whether an allow rule may allow this command at all: literal words, a
    program found on the system path (not ./git or /tmp/git), no output to a
    file unless the rule says so, no leading variables it does not name."""
    if not command.plain or command.path_kind == "other":
        return False
    if command.redirects_to_file and not matcher.redirect:
        return False
    return set(command.env_assignments) <= set(matcher.env or [])


def rule_example_holds(effect: str, matcher, example: str) -> bool:
    """Whether a rule matches its own example, as it would be evaluated."""
    parsed = parse_command(["sh", "-c", example])
    if effect == "allow":
        return (
            not parsed.opaque
            and bool(parsed.commands)
            and all(allowable(matcher, c) and command_matches(matcher, c) for c in parsed.commands)
        )
    return any(command_matches(matcher, c) for c in parsed.commands)
