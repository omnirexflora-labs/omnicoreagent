# Fixes from the 0.5.0rc4 release-candidate gate

The rc4 gate ran the same six areas against the built wheel. Each unit is
test-first and one commit; 0.5.0 is built and gated again after these land,
and published only when a gate finds nothing to fix.

## Units

- U1 Parallel `execute` calls each see the whole workspace: the bridge copies
  one at a time. A second copy-in skipped files the first was still
  uploading, and its command ran on a partial workspace (area D).
- U2 `generate-dockerfile` writes a `.dockerignore` keeping `.env` and the
  workspace out of the image (it copies the whole build context; the .env
  holding LLM_API_KEY went in), and names an existing one that does not
  exclude `.env` (area D, security).

- U3 A rule whose capability matches no capability is refused when the
  policy loads: a deny rule on `tool.locall.call` built and silently denied
  nothing (area F). The built-in profiles' `memory.*` and `telemetry.*`
  rules stay as published.

- U4 A misspelt rules bucket (`denies`) is refused; its rules were dropped
  and what they meant to deny was allowed in permissive mode (area B).

- U5 A background run waited on with `"wait": true` that pauses for a
  person or a budget answers 200 with its waiting status; it was a 504 "did
  not finish" (area F).

- U6 A background run whose agent saved it completed before shutdown's
  cancel landed is completed, not failed as "worker shutdown": with retries
  the finished work would have run again (area C).

- U7 The package import no longer loads `typing` (most of it under load),
  so the CLI's Ctrl-C handler is in place sooner; an export that fails to
  load says why instead of a bare "cannot import name" (area E).

- U8 The budget warning counts what a person granted; after a top-up it said
  "remaining 0.0" with budget left (area F).

- U9 The command summary a person approves is quoted as the shell reads it,
  one line per command; joined with spaces, a quoted argument read as
  several, and a quoted newline looked like a second command (area B).

- U10 `get_run` and `list_runs` always carry `budget_requests` and
  `outcomes`, as HTTP does; a run with none read without the keys (area F).

- U11 An MCP connect timeout names `connect_timeout`: a server importing
  slowly on a busy host failed at the 30 s default with no pointer (area F).

- U12 Closing an SSE stream cancels its run: the stream watches for the
  client leaving instead of waiting for the server to close it, which through
  the middleware did not reliably happen; with no request timeout a cut
  stream's run stayed running for good. Checked live: cancelled within 5 s
  (area A).

- U13 A provider's masked echo of a registered key (`sk-proj-****4444`)
  is scrubbed like the key: a rejected key's error stored its prefix and last
  characters in the trace (area A, security).

- U14 OmniServe's 403 names the refused capability (it was null), and a
  background budget pause's message rounds the shortfall (area A).

