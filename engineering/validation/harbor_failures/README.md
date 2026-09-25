# Harbor tasks that are meant to go wrong

Each one is a way a trial ends badly, to see that this agent ends it honestly
and that `omnicoreagent harbor results` says what happened. Each passes with
Harbor's `oracle` agent.

| Task | Run with | What should happen |
|---|---|---|
| `unknowable-code` | defaults | reward 0; the agent says it cannot find the code rather than invent one |
| `hanging-build` | `--ak command_timeout=15` | the command times out; the agent writes that the build did not finish |
| `hanging-build` | `--ak run_timeout=45 --ak command_timeout=300` | the run ends itself as `timeout` and leaves its result and trajectory |
| `agent-network-allowlist` | `--allow-agent-host api.openai.com` | passes: only the model's host is reachable, and that is enough |
| `agent-network-allowlist` | defaults | fails, saying it cannot connect to the model's host |
| `harbor_task/receipts-subtotal` | `--ak max_steps=2` | ends as `error (max_steps)` |

```bash
omnicoreagent harbor run -p engineering/validation/harbor_failures/hanging-build \
  -m gpt-5.6-terra --ak command_timeout=15
omnicoreagent harbor results jobs
```
