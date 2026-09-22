# Telemetry evidence coverage

This map records what the runtime actually emits on branch
`refactor/native-tool-runtime` after the
[trajectory completion plan](telemetry-trajectory-completion-plan.md)
(Phase C1 at `77cfe15`). Each row names the emitter and the tests that assert
the evidence, not just register an event name. The ten rows of the first table
follow the plan's trajectory checklist; the end-to-end proof of all ten is the
[trajectory acceptance](../validation/trajectory-acceptance.md), run by
`tests/test_trajectory_acceptance.py`.

An earlier version of this map (checkpoint `3aa6cab`) claimed that
`tool_observation` carried its result and observation IDs and cited tests that
never inspected the observation event. That was not true at the time; the
links were added in B5 and are asserted by the observation tests below.

## Trajectory checklist

| # | Evidence | Runtime emitter | Tests |
| --- | --- | --- | --- |
| 1 | Request: query, `trace_id`, `run_id`, `session_id`, surface (`interactive`, `serve`, `background`), tags and provenance | `OmniCoreAgent.run` starts `agent.run` and emits `user_message`; `serve/routes/runs.py` and the background supervisor set the surface | `test_telemetry_run_header.py::test_direct_run_is_interactive`, `::test_served_run_records_the_serve_surface`, `::test_background_run_records_the_background_surface`, `::test_run_accepts_tags_and_provenance`; `test_runtime_telemetry_wiring.py::test_run_records_completed_telemetry_trace` |
| 2 | Harness: model and settings, limits, context/memory/offload configuration, tool catalog and count, system prompt digest (text under full capture), configuration fingerprints, versions | `BaseReactAgent._record_run_configuration` emits `run_configuration` before the first step | `test_telemetry_run_header.py` (all tests, including `::test_run_header_never_records_model_credentials` and `::test_run_header_links_to_the_first_model_context`) |
| 3 | Step: context digest (messages under full capture), tokens incl. cached and reasoning, cost and its source, finish reason, latency, time to first streamed delta, attempts and retries, provider response ID and served model, raw tool-call arguments, purpose | `AgentLlmStepRunner` emits `context_assembly`, `model.call`, `model_call`, `model_response`, `model_error`; retries reported through `MODEL_RETRY_OBSERVER` | `test_telemetry_model_step.py` (all tests); `test_llm_step.py::test_llm_step_context_capture_respects_prompt_policy` |
| 4 | Tool call: name, arguments (redacted), raw malformed arguments and rejection reason, resolved provider, governance decision, status `success` / `error` / `rejected` / `timeout` / `cancelled`, error, result, links to the model turn | `execute_native_turn` emits `tool_requested`, `tool_resolved`, tool spans and results; `GovernedToolRunner` records policy request/decision and timeouts | `test_telemetry_tool_record.py` (all tests); `test_governed_tool_runner.py::test_governance_denies_local_tool_without_executing` |
| 5 | Observation: the exact tool message sent to the model, after guardrail and offload, linked to its result event and span (or to its request when rejected) and to the model call that received it | `execute_native_turn` emits `tool_observation`; the next `model_call` lists `new_observation_event_ids` | `test_telemetry_observation_links.py::test_observation_links_to_its_tool_result_and_span`, `::test_next_model_turn_records_the_observations_it_received`, `::test_observations_stay_in_context_but_are_new_only_once`, `::test_rejected_call_observation_links_to_its_request`; `test_governed_tool_runner.py::test_tool_output_guardrail_scrubs_before_result_telemetry` |
| 6 | Context management: runtime-injected messages (datetime prefix, empty-response retry, loop recovery), compression with before/after sizes, `context_summary` model calls, offloads, memory reads/writes | `BaseReactAgent._record_runtime_message`; `AgentLlmStepRunner` emits `context_compression`; `workspace_offload`; `_store_message_with_telemetry` / `_get_messages_with_telemetry` | `test_telemetry_observation_links.py::test_empty_response_retry_is_a_recorded_runtime_message`, `::test_loop_recovery_is_a_recorded_runtime_message`, `::test_datetime_prefix_is_a_recorded_runtime_message`, `::test_model_calls_record_their_purpose`; `test_llm_step.py::test_llm_step_records_context_compression_telemetry` |
| 7 | Delegation: child trace and run IDs on success, error, and cancellation; `subagent` provider; dynamic spawns with workspace output; child trajectory nested under the delegating call | `SubAgentCallRunner`, `subagent_helpers.finish_delegation`, the dynamic `subagent.run` span | `test_telemetry_delegation_identity.py` (all tests); `test_telemetry_trajectory.py::test_child_run_is_nested_under_the_delegating_tool_call`; `test_runtime_telemetry_wiring.py::test_child_agent_run_shares_store_and_records_parent_trace_link` |
| 8 | Final: answer, terminal status and reason (`timeout` distinct from `cancelled`), link to the model response that produced it | `OmniCoreAgent.run` emits `final_answer` and ends the trace; `run_with_timeout` / `current_stop_reason` | `test_telemetry_attempt_identity.py::test_run_stopped_by_timeout_is_recorded_as_timeout`, `::test_run_cancelled_by_caller_is_still_recorded_as_cancelled`; `test_telemetry_trajectory.py::test_trajectory_reads_the_run_from_request_to_final_answer` |
| 9 | Run totals: steps, model calls, tokens, cost, tool outcomes, compressions, subagents, workspace changes, duration | `summary.summarize_trace` via `OmniCoreAgent._run_summary`, in terminal events and the root span output | `test_telemetry_run_summary.py` (all tests) |
| 10 | Honesty: every missing payload states why; any gap makes the trace `partial`; the reader accounts for every event | `TelemetryRecorder` capture descriptors and `end_trace` gap computation; `build_trajectory` puts unplaced events in `other_events` | `test_telemetry_trajectory.py::test_every_event_is_accounted_for_exactly_once`, `::test_default_capture_keeps_structure_and_states_what_is_missing`; `test_telemetry_foundation.py::test_recorder_marks_trace_evidence_partial_when_capture_is_disabled` |

## Storage, delivery, and contract

| Evidence | Implementation | Tests |
| --- | --- | --- |
| Durable default storage, capture presets, one store per file | `construction.py` (workspace `telemetry/traces.jsonl`), `shared_jsonl_telemetry_store`, `CAPTURE_PRESETS` | `test_telemetry_defaults.py` (all tests) |
| JSONL integrity: restored cursors, corrupt lines counted and marked partial, prune and compaction, serialized writes, one lock per event loop | `JsonlTelemetryStore`, `_LoopLocks` | `test_telemetry_store_integrity.py` (all tests) |
| Retention: independent trace and payload windows, automatic once per agent, observable status | `prune_telemetry`, `telemetry_retention_status`, `payloads.payload_references`, `GET /telemetry/retention` | `test_telemetry_retention.py` (all tests) |
| Lineage sharing: background and delegated runs use one store; build-time recorders rebound | `BackgroundAgentManager.register_agent`, `_adopt_telemetry_store`, `_bind_telemetry_components` | `test_telemetry_lineage_sharing.py` (all tests); `test_background_agent.py::test_background_agent_trace_is_linked_to_lifecycle_trace` |
| Background attempts: attempt ID and number, timeouts, cleanup under cancellation | background supervisor, `deadline.py` | `test_telemetry_attempt_identity.py` (all tests) |
| Delivery: SSE resume replays once, large backlog then live, `Last-Event-ID`, bounded duplicate tracking | `serve/sse.py`, `serve/routes/sessions.py` | `test_omniserve_sse.py::test_stream_session_events_resumes_after_supplied_cursor`, `::test_stream_session_events_resumes_large_backlog_then_follows_live`, `::test_sse_seen_events_memory_is_bounded` |
| Privacy: redaction before storage; identifiers, digests, UUIDs, and dates never altered | `privacy.py`, `redaction.py` | `test_privacy_boundaries.py::test_privacy_filter_never_corrupts_generated_identifiers_or_digests`, `::test_privacy_filter_never_alters_hyphenated_identifiers`, `::test_privacy_filter_keeps_dates_and_timestamps_intact`, `::test_privacy_filter_still_redacts_standalone_card_numbers` |
| Trajectory reader | `trajectory.build_trajectory`, `agent.get_trajectory`, `/telemetry/runs/{run_id}/trajectory`, `/telemetry/traces/{trace_id}/trajectory` | `test_telemetry_trajectory.py` (all tests) |
| MCP: server status, calls, errors, reconnects | Run header `mcp_servers` from `MCPClient.server_status()`; `mcp.tool.call` spans; `mcp_reconnect` events on the affected call; trajectory `server` and `reconnects`; `/ready` `mcp_servers` | `test_telemetry_mcp.py` (all tests); the trajectory acceptance's MCP step (structured success, tool error, protocol error, per-call timeout, malformed arguments) |
| Provider continuation: presence recorded, opaque values never stored | `core/continuation.py` (`mask_opaque`, `continuation_summary`), recorder payload, error-text, and digest paths; `model_call.continuation` | `test_continuation_context_telemetry.py` (all tests); `test_history_continuation.py::test_opaque_values_are_never_pattern_scanned` |
| Portable evidence contract | `OmniCoreEvidenceAdapter`, `GenericTraceEvidenceAdapter`, packaged JSON Schema | `test_telemetry_portable_contract.py` (all tests); `test_telemetry_evidence.py` |

## Known boundaries

- A shell or Python tool's internal filesystem reads are not individually
  recorded.
- Model prompts and responses are not reconstructable under the default
  capture policy; the trace records them as `not_recorded` and is `partial`.
- A truncated payload cannot be recovered; the truncation is recorded.
- The raw tool result before guardrail scrubbing is kept only as a hash.
- MCP resources, prompts, and server-initiated sampling, elicitation, and roots
  are not supported, so they are not recorded
  ([MCP v2 completion plan](mcp-v2-completion-plan.md)).
- Harbor trials, verifiers, and the evaluation layer consume this contract
  later; they are not runtime emitters.
