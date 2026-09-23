# Traces a trainer can learn from

Status: agreed with the maintainer 2026-09-22 ("i want us to support 1-4").
Prompted by rLLM's [real-time RL post](https://rllm-project.com/post.html?post=realtime_rl.md):
a coding agent trained from **one rollout per task**, each run's reward
centred against the mean reward of a batch of other tasks (Qwen3-Coder-30B,
MigrationBench, 43% -> 59.2%, matching GRPO for the same number of rollouts).
Their reward is what the steward already produces: the project builds, its
tests pass, and no test was deleted.

## What such a trainer needs from one run, and where we stand

| Needed | Today |
| --- | --- |
| The exact prompt and messages at every step | Recorded at `capture: "full"`, complete since the telemetry storage work (T1, T2) |
| Tool calls, raw arguments, results, the observation delivered | Recorded, linked step by step |
| One reward per run, which usually arrives later | Missing: a run's outcome cannot be attached after it ends |
| The probability of each token the model chose | Missing; needed to reuse a run recorded by a slightly older policy (their run without it collapsed to 4%) |
| The exact token ids the model saw | Missing; re-tokenizing the text is itself a source of mismatch |
| Which policy produced the run | Partly: the model's name, not the checkpoint it served |
| The whole batch in a trainer's format | Missing |

Only useful with a model you serve yourself (vLLM and the like): a hosted
API will not return token probabilities, and its weights are not yours to
train. The runtime's job is to make the trace sufficient when it is.

## Units

- **R0. Full capture is the default.** `capture: "full"` becomes the
  default recording policy (the maintainer's decision, 2026-09-22);
  `capture: "default"` stays for a privacy-first deployment. Personal data is
  still redacted from telemetry.
- **R1. A run's outcome, attached whenever it is known.**
  `agent.record_outcome(run_id, reward=…, source=…, detail=…)` and
  `POST /runs/{run_id}/outcome`: kept on the run record and recorded in its
  trace, for a run that has ended minutes or days ago. More than one outcome
  may be attached (a reviewer's approval, then the merge).
- **R2. The policy that produced the run.** Each model call records what the
  provider says served it (model, revision or fingerprint); the run header
  carries it, so a trainer can tell how stale a trajectory is.
- **R3. Token ids and log-probabilities, when the provider returns them.**
  Opt-in (`record_token_details`), because they are large; recorded on each
  model response beside its text.
- **R4. The export.** `agent.training_records(...)` and
  `omnicoreagent traces export --format jsonl`: one record per run —
  messages as the model saw them, tool calls, token details when present,
  the outcomes, the policy version, cost and status.

Each unit: a test that fails first, the fix, the suite as CI runs it, a PR.

## Execution log

| Unit | Status | Commit | Notes |
| --- | --- | --- | --- |
| R0 | Done | `8029f02` | `capture: "full"` is the default; `capture: "default"` is the privacy-first preset. Twelve tests and the acceptance script now name the preset they mean. |
| R1 | Done | `8248dde` | `agent.record_outcome(run_id, source=, reward=, label=, detail=)` and `POST /runs/{run_id}/outcome`: kept on the run's record, in its trace as a `run_outcome` event, and in its trajectory. A run may gather several; a run that ended long ago still takes one. |
| R2 | Done | `8248dde` | Each model call records `policy_version`: the model the provider served, its fingerprint, version and service tier when it reports them. |
| R3 | Done | `8248dde` | `model_config`'s `logprobs`/`top_logprobs` ask for the tokens the model chose and their probabilities (a provider that refuses names the setting, as with any other); `telemetry_config.record_token_details` records them, off by default. A model's own token list is kept as the provider names it, not read as a credential. |
| R4 | Done | `8248dde` | `agent.training_records(run_id=/session_id=/trace_ids=)`: one record per finished run — its messages, tools, responses, token details, tool calls and observations, policy version, totals and outcomes. A run recorded without model prompts is left out. A command-line export is not included; the records are JSON. |
