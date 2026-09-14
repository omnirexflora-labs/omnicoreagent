# Telemetry evidence coverage

This map records the latest implementation checkpoint on branch
`refactor/native-tool-runtime` at commit `3f8e71c` (delivery policy work). It names runtime emitters
and executable tests; event names in the registry are not treated as coverage
by themselves.

| Boundary | Runtime emitter and evidence | Persistence/export verification |
| --- | --- | --- |
| User request and session identity | `OmniCoreAgent.run` starts `agent.run`, emits `user_message`, and returns `trace_id`/`run_id`; `_store_message_with_telemetry` writes the memory copy. | `tests/test_runtime_telemetry_wiring.py::test_run_records_completed_telemetry_trace`; `tests/test_real_application_smoke.py::test_full_stack_harness_run_uses_tools_workspace_offload_and_telemetry` |
| Context and tool catalog | `AgentLlmStepRunner.run` emits `context.assembly`; `interaction_history.context_evidence` records privacy-safe message/tool digests; prompt payloads are opt-in. | `tests/test_llm_step.py::test_llm_step_context_capture_respects_prompt_policy`; `tests/test_interaction_history.py::test_context_digest_uses_privacy_safe_representation` |
| Model request/response | `AgentLlmStepRunner._call_model` creates `model.call`, `model_call`, `model_response`, and `model_error`; stream stats survive partial terminal states. | `tests/test_llm_step.py::test_llm_step_records_bounded_provider_stream_statistics`; `tests/test_loop_telemetry_instrumentation.py` |
| Requested/resolved tools | `execute_native_turn` emits `tool_requested` and `tool_resolved` with batch/call/model IDs before execution; invalid calls remain represented. | `tests/test_loop_telemetry_instrumentation.py::test_react_loop_records_model_step_and_parallel_tool_telemetry`; governance denial tests in `tests/test_governed_tool_runner.py` |
| Authorization and execution result | `GovernedToolRunner` emits policy request/decision evidence; `native_tools.execute_native_turn` emits tool batch/call/result/error and preserves concurrent call IDs. | `tests/test_governed_tool_runner.py::test_governance_denies_local_tool_without_executing`; `tests/test_governed_tool_runner.py::test_governance_budget_is_atomic_for_parallel_tool_batch` |
| Observation delivered to model | `execute_native_turn` emits `tool_observation` after guardrail/offload transformation, with result and observation IDs; workspace offload is a separate event. | `tests/test_governed_tool_runner.py::test_tool_output_guardrail_scrubs_before_result_telemetry`; `tests/test_real_application_smoke.py::test_full_stack_harness_run_uses_tools_workspace_offload_and_telemetry` |
| Context compression and summaries | `AgentLlmStepRunner` emits `context.compression`/`context_compression`; internal summarization is linked as a normal `model.call`. | `tests/test_llm_step.py::test_llm_step_records_context_compression_telemetry` and context-focused tests in `tests/test_llm_step.py` |
| Memory operations | `OmniCoreAgent._store_message_with_telemetry` and `_get_messages_with_telemetry` emit memory spans/events with stable stored-message digests. | `tests/test_runtime_telemetry_wiring.py` memory instrumentation cases; JSONL restart coverage in `tests/test_telemetry_foundation.py` |
| Workspace and artifacts | Workspace tools emit workspace spans/events; offloaded payloads use `TelemetryPayloadStore` references; artifact tools are classified as workspace reads. | `tests/test_governed_tool_runner.py::test_governance_allows_real_workspace_write_and_read`; `tests/test_telemetry_payloads.py::test_local_payload_store_round_trips_content_addressed_payload` |
| Subagent lineage | `SubAgentCallRunner` emits spawn/result/error on the parent and child `agent.run` uses a linked trace with parent IDs. | `tests/test_subagent_runner.py::test_subagent_runner_records_successful_outputs`; `tests/test_runtime_telemetry_wiring.py::test_child_agent_run_shares_store_and_records_parent_trace_link` |
| Background attempts | `BackgroundEventLog` and manager emit lifecycle spans/events; agent traces carry parent lifecycle IDs and distinct run/attempt identity. | `tests/test_background_agent.py::test_background_agent_trace_is_linked_to_lifecycle_trace`; background restart/recovery tests |
| Serving boundary | `serve.request` traces/events wrap synchronous and SSE requests and correlate session/run/trace IDs. | `tests/test_omniserve_sse.py::test_run_agent_stream_finishes_serve_trace_before_terminal_chunk_close`; telemetry route tests |
| Final answer and terminal state | `OmniCoreAgent.run` emits `final_answer` then finalizes trace with status/termination reason; cancellation/error paths emit terminal state. | `tests/test_runtime_telemetry_wiring.py::test_run_records_completed_telemetry_trace`; cancellation tests in `tests/test_omniserve_sse.py` |
| Capture completeness | `TelemetryCapture`, normalizer, and evidence adapter expose redaction, truncation, offload, disabled, missing, and incomplete states. | `tests/test_telemetry_foundation.py::test_recorder_marks_trace_evidence_partial_when_capture_is_disabled`; `tests/test_telemetry_evidence.py` |
| Portable export/import | `OmniCoreEvidenceAdapter` converts internal traces to the versioned JSON envelope; `GenericTraceEvidenceAdapter` preserves external fields and records unknowns. | `tests/test_telemetry_evidence.py::test_portable_document_is_json_contract_and_can_be_reimported`; rich generic fixture in the same module |
| Delivery and reconnect | `TelemetryStream`/SSE use store-local cursors; provider stream stats are bounded; store/export timeout behavior is explicit. | `tests/test_omniserve_sse.py::test_stream_session_events_resumes_after_supplied_cursor`; `tests/test_telemetry_delivery.py` |

## Known boundaries

The map does not claim that a shell/Python tool reveals every filesystem read
performed internally, that raw model prompts are reconstructable when policy
disables them, or that a truncated payload can be recovered. Those are recorded
as explicit capture gaps. MCP v2 SDK compatibility remains deferred. Harbor
trial/verifier execution and the independent evaluation runner consume this
contract later; they are not runtime telemetry emitters.
