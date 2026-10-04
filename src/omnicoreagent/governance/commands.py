"""The commands a shell command line runs, for prefix rules on command text.

Every command reaches the policy as ``sh -c <text>`` (the execute tool runs
it so), so a rule needs the commands in the text. This splits a **plain
chain**, simple commands of literal words joined by ``&&``, ``||``, ``;``,
``|`` or a newline, into those commands, and nothing more. Anything else
makes the whole line **unreadable**: a redirect, ``$(...)`` or backticks, a
variable or ``~``, a glob, an escape, a leading ``NAME=value``, a subshell,
a heredoc, a function, a loop, or a program that runs text as a command
(``eval``, ``source``, ``sh -c``).

This is the design Codex and Claude Code use, and why (engineering/
architecture/simple-policy-plan.md): through 0.5.0rc7 a parser that looked
inside wrappers, ``xargs``, ``find -exec`` and nested shells was found short
at every release gate. Rules here decide when to ask; what a command can
touch is the sandbox's job. A rule never allows an unreadable command, and
one meeting a policy with command rules is asked about (refused in strict).

The parse is pure: no process is started and nothing is read from disk.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shlex
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Sequence

# Longer than this is unreadable: a command a person could not review either.
MAX_COMMAND_CHARS = 10_000
SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "mksh", "ash", "busybox"})
# Programs that run text given to them as a command: what they run is not
# the words a rule sees, so the line is unreadable.
_RUNS_TEXT = frozenset({
    "eval", "source", ".", "exec", "command", "builtin",
    # Builtins that run, or arrange to run, a string they are given: `trap
    # 'rm -rf x' EXIT`, `alias ls='rm -rf x'`, `hash -p ./rm ls`, `mapfile -C
    # 'rm'`, `let 'a[$(rm)]=1'` each ran rm through a line read as plain
    # (the rc7 gate, area S).
    "trap", "alias", "hash", "fc", "bind", "complete", "compgen", "mapfile",
    "readarray", "let", "read", "enable",
})
# Inside quotes these are text to the shell, but bash runs them again when
# the word reaches an arithmetic context: `printf -v 'a[$(rm)]' x`, `test -v
# 'a[$(rm)]'` (the rc7 gate, area S). A literal word that spells a
# substitution is not plain.
_SUBSTITUTION = re.compile(r"\$\(|`")
# bash keywords read as a program by the grammar, which run the command after
# them: `time rm` and `coproc rm` run rm (found running the corpus under bash).
_KEYWORDS = frozenset({"time", "coproc", "function", "select"})
_GLOB = re.compile(r"[*?\[]")


@dataclass(frozen=True)
class SimpleCommand:
    """One program the line runs, with its arguments, as literal words."""

    argv: tuple[str, ...]
    program: str                     # the program's name, without its directory
    bare: bool = True                # named without a path (`git`, not ./git or /tmp/git)
    text: str = ""                   # as written


@dataclass(frozen=True)
class ParsedCommand:
    commands: tuple[SimpleCommand, ...]
    opaque: bool                     # unreadable: not a plain chain
    opaque_reasons: tuple[str, ...]
    digest: str                      # of the exact argv: binds an approval to this command
    text: str = ""

    @property
    def summary(self) -> list[str]:
        """What a person approving it reads: each command of a plain chain on
        its own line, or the whole text when it is unreadable, since then the
        text is what runs. Control and invisible characters are shown, never
        acted on."""
        if self.opaque or not self.commands:
            return [_visible(self.text)]
        return [_visible(c.text) if c.text else " ".join(_shown(a) for a in c.argv) for c in self.commands]


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Shown escaped too: a right-to-left override or a zero-width character can
# make what the approver reads differ from what runs (the rc7 security review).
_INVISIBLE = {"Cc", "Cf", "Zl", "Zp"}


def _visible(text: str) -> str:
    """The command as written, on one line: a control character is shown, never
    acted on, so a quoted newline cannot pass for a second command."""
    return "".join(
        {"\n": "\\n", "\t": "\\t", "\r": "\\r"}.get(ch)
        or (f"\\x{ord(ch):02x}" if _CONTROL.match(ch) else None)
        or (f"\\u{ord(ch):04x}" if unicodedata.category(ch) in _INVISIBLE else ch)
        for ch in text
    ).strip()


def _shown(argument: str) -> str:
    if not _CONTROL.search(argument):
        return shlex.quote(argument)
    # bash's $'...' form: a control character is written, never acted on.
    escaped = "".join(
        {"\n": "\\n", "\t": "\\t", "\r": "\\r", "\\": "\\\\", "'": "\\'"}.get(ch)
        or (f"\\x{ord(ch):02x}" if _CONTROL.match(ch) else None)
        or (f"\\u{ord(ch):04x}" if unicodedata.category(ch) in _INVISIBLE else ch)
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


class _Unreadable(Exception):
    """The line is not a plain chain; the message says why."""


def parse_command(argv: Sequence[str]) -> ParsedCommand:
    """The commands ``argv`` runs. Never raises: what it cannot read is opaque."""
    argv = [str(a) for a in argv]
    digest = command_digest(argv)
    script = shell_script(argv)
    text = script if script is not None else " ".join(_shown(a) for a in argv)
    try:
        if script is None:
            commands = [_simple(argv, text)]
        else:
            commands = _split(script)
        return ParsedCommand(tuple(commands), False, (), digest, text)
    except _Unreadable as why:
        return ParsedCommand((), True, (str(why),), digest, text)
    except Exception as exc:  # the parser must never be the reason a run crashes
        return ParsedCommand((), True, (f"parser failed: {exc.__class__.__name__}",), digest, text)


def _split(script: str) -> list[SimpleCommand]:
    if len(script) > MAX_COMMAND_CHARS:
        raise _Unreadable(f"longer than {MAX_COMMAND_CHARS} characters")
    if "\\\n" in script:
        # The grammar reads `r\<newline>m` as two words; the shell runs rm.
        raise _Unreadable("a line continuation")
    source = script.encode("utf-8")
    root = _parser().parse(source).root_node
    if root.has_error:
        raise _Unreadable("the shell grammar cannot read it")
    commands: list[SimpleCommand] = []
    for child in root.children:
        _chain(child, source, commands)
    if not commands:
        raise _Unreadable("no command")
    return commands


def _chain(node, source: bytes, out: list[SimpleCommand]) -> None:
    """A plain chain: commands joined by &&, ||, ;, | or a newline."""
    kind = node.type
    if not node.is_named:
        if _text(node, source) in {"&&", "||", ";", "|", "\n"}:
            return
        raise _Unreadable(f"`{_text(node, source)}`")
    if kind == "comment":
        return
    if kind in {"list", "pipeline"}:
        for child in node.children:
            _chain(child, source, out)
        return
    if kind == "command":
        out.append(_command(node, source))
        return
    raise _Unreadable(_WHY.get(kind, kind.replace("_", " ")))


_WHY = {
    "redirected_statement": "a redirect",
    "variable_assignment": "a variable set before the command",
    "subshell": "a subshell",
    "compound_statement": "a { ... } group",
    "function_definition": "a function",
    "for_statement": "a loop",
    "while_statement": "a loop",
    "if_statement": "an if",
    "case_statement": "a case",
    "negated_command": "a negated command",
    "heredoc_redirect": "a heredoc",
}


def _command(node, source: bytes) -> SimpleCommand:
    words: list[str] = []
    for child in node.children:
        if child.type in {"command_name"}:
            words.append(_word(child.children[0] if child.children else child, source))
        elif child.type == "variable_assignment":
            raise _Unreadable("a variable set before the command")
        elif child.type in {"file_redirect", "herestring_redirect", "heredoc_redirect"}:
            raise _Unreadable("a redirect")
        else:
            words.append(_word(child, source))
    if not words:
        raise _Unreadable("no command")
    return _simple(words, _text(node, source))


def _simple(argv: Sequence[str], text: str) -> SimpleCommand:
    argv = tuple(argv)
    if not argv or not argv[0]:
        raise _Unreadable("no command")
    program = posixpath.basename(argv[0])
    if program in _RUNS_TEXT:
        raise _Unreadable(f"`{program}` runs text as a command")
    if argv[0] in _KEYWORDS:
        raise _Unreadable(f"the shell keyword `{program}`")
    if program in SHELLS and any(a.startswith("-") and "c" in a[1:] for a in argv[1:] if not a.startswith("--")):
        raise _Unreadable(f"`{program} -c` runs text as a command")
    return SimpleCommand(argv=argv, program=program, bare="/" not in argv[0], text=text)


def _word(node, source: bytes) -> str:
    """A literal word, or unreadable: what the shell would make of anything
    else (expansion, substitution, globbing, escapes) is not the text."""
    kind = node.type
    raw = _text(node, source)
    if kind in {"word", "number"}:
        if "\\" in raw:
            raise _Unreadable("an escape")
        if raw.startswith("~"):
            raise _Unreadable("`~`")
        if _GLOB.search(raw):
            raise _Unreadable("a glob")
        if "{" in raw:
            # bash expands {rm,-rf,x} into rm -rf x.
            raise _Unreadable("a brace expansion")
        return raw
    if kind == "raw_string":
        if _SUBSTITUTION.search(raw):
            raise _Unreadable("a command substitution inside quotes")
        return raw[1:-1]
    if kind == "string":
        if any(child.is_named and child.type != "string_content" for child in node.children):
            raise _Unreadable("an expansion inside quotes")
        if "\\" in raw:
            raise _Unreadable("an escape")
        if _SUBSTITUTION.search(raw):
            raise _Unreadable("a command substitution inside quotes")
        return raw[1:-1]
    if kind == "concatenation":
        return "".join(_word(child, source) for child in node.children)
    if kind in {"command_substitution", "process_substitution"}:
        raise _Unreadable("a command substitution")
    if kind in {"simple_expansion", "expansion", "arithmetic_expansion"}:
        raise _Unreadable("a variable")
    if kind == "ansi_c_string":
        raise _Unreadable("a $'...' string")
    raise _Unreadable(kind.replace("_", " "))


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


# --- Matching rules to commands ------------------------------------------------------

_PARSED = "_omnicoreagent_parsed_command"


def attach_command(request, argv: Sequence[str]):
    """Parse ``argv`` and attach it to a ``process.exec`` request.

    The parse rides on the request as an attribute, not a field, so it is never
    serialized. The metadata, which governance events record as it is, gets no
    argument: only the program names, whether it is unreadable, and the digest
    that binds an approval to the exact command. Arguments can hold secrets
    (``curl -H "Authorization: ..."``); the full commands go only on an
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
    """An approval's metadata: the request's, plus the commands a person
    approving must see (not ``sh`` and an argument count)."""
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


def command_matches(matcher, command: SimpleCommand) -> bool:
    """Whether a command begins with a rule's prefix: its program's name, then
    each later word, each a literal or a list of alternatives."""
    words = [command.program, *command.argv[1:]]
    if len(words) < len(matcher.prefix):
        return False
    return all(
        word in (token if isinstance(token, list) else [token])
        for word, token in zip(words, matcher.prefix)
    )


def allowable(matcher, command: SimpleCommand) -> bool:
    """An allow rule allows a program named without a path only: ./git or
    /tmp/git is not the git a rule on git means."""
    return command.bare


def rule_example_holds(effect: str, matcher, example: str) -> bool:
    """Whether a rule matches its own example, as it would be evaluated."""
    parsed = parse_command(["sh", "-c", example])
    if parsed.opaque:
        return False
    if effect == "allow":
        return bool(parsed.commands) and all(
            allowable(matcher, c) and command_matches(matcher, c) for c in parsed.commands
        )
    return any(command_matches(matcher, c) for c in parsed.commands)
