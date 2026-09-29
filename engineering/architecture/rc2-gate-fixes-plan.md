# Fixes from the 0.5.0rc2 release-candidate gate

The rc2 gate ran the same six areas as rc1 (docs, control, durability,
execution, record, a stranger's app) against the built wheel. Every rc1
finding re-checked is fixed. This plan lists what rc2 found; each unit is
test-first and one commit. 0.5.0 is built and gated again (rc3) after these
land, and published only when a gate finds nothing to fix.

## Units

- S1 (blocker) A strict policy that names no `workspace.files.*` rule made
  every `execute` fail: the bridge treated `UnknownCapabilityError` (strict,
  nothing matched) as a failure, not a refusal. The file is now skipped as
  "not permitted by policy", as documented. Area D, reproduced by the docs'
  own strict example.
- S2 An approval's `decision` is recorded when the person decides, not only
  when a resume applies it (areas B and F: `decision: null` in the decide
  answer and the run record). The documented `used` status is kept; the
  decision says whether it was an approval or a denial.
- S3 An approved sandbox set-up (network, file system, environment) holds
  for the rest of the run. Every sandbox session asks for its set-up and a
  resume opens a new session, so a run that paused after its network was
  approved asked the same question again (area D). A tool call's approval is
  still spent once.
- S4 A call the policy or a person refused reads `denied` in the run record
  (`rejected` for invalid arguments), as it does in the trace; it said
  `error` (area F).
- S5 `GET /runs` and `GET /runs/{run_id}` include `heartbeat_at`,
  `lease_seconds`, `attempt` and `previous_attempts`, so an operator over HTTP
  can tell a dead `running` run from a live one (area F); an unknown `status`
  filter is a 422 naming the valid ones, not an empty list (area B).
- S6 A misspelt key in a rule's `target`, `conditions` or `constraints` is
  named like one in `command` (it was a raw `__init__` TypeError); a
  malformed JSON policy file says the line and column (areas B and F).

