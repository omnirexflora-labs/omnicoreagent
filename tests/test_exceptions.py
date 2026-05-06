from omnicoreagent.exceptions import (
    AgentRunException,
    CheckTrigger,
    InputCheckError,
    ModelAuthenticationError,
    ModelProviderError,
    ModelRateLimitError,
    OmniCoreAgentError,
    OutputCheckError,
    RemoteServerUnavailableError,
    RetryAgentRun,
    StopAgentRun,
)


def test_agent_run_exceptions_capture_stop_semantics():
    retry = RetryAgentRun("retry", user_message="try again")
    stop = StopAgentRun("stop", agent_message="done")
    base = AgentRunException("base")

    assert retry.error_id == "retry_agent_run_error"
    assert retry.stop_execution is False
    assert retry.user_message == "try again"
    assert stop.error_id == "stop_agent_run_error"
    assert stop.stop_execution is True
    assert stop.agent_message == "done"
    assert base.error_id == "agent_run_error"


def test_model_errors_preserve_error_ids_and_metadata():
    auth = ModelAuthenticationError("bad key", model_name="gpt")
    provider = ModelProviderError("provider failed", model_id="model-1")
    rate_limit = ModelRateLimitError("slow down")
    remote = RemoteServerUnavailableError(
        "server down",
        base_url="http://localhost:9999",
        original_error=ConnectionError("refused"),
    )

    assert str(OmniCoreAgentError("boom")) == "boom"
    assert auth.status_code == 401
    assert auth.error_id == "model_authentication_error"
    assert auth.model_name == "gpt"
    assert provider.status_code == 502
    assert provider.error_id == "model_provider_error"
    assert provider.model_id == "model-1"
    assert rate_limit.status_code == 429
    assert rate_limit.error_id == "model_rate_limit_error"
    assert remote.status_code == 503
    assert remote.base_url == "http://localhost:9999"


def test_input_and_output_check_errors_accept_enum_or_string_triggers():
    input_error = InputCheckError(
        "blocked",
        check_trigger=CheckTrigger.PROMPT_INJECTION,
        additional_data={"source": "guardrail"},
    )
    output_error = OutputCheckError("blocked", check_trigger="custom")

    assert input_error.type == "input_check_error"
    assert input_error.error_id == "prompt_injection"
    assert input_error.additional_data == {"source": "guardrail"}
    assert output_error.type == "output_check_error"
    assert output_error.error_id == "custom"
