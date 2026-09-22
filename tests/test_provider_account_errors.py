"""A provider account that cannot pay is not a busy provider.

Found on the repository steward's server during P7: the OpenAI account ran
out of credits. The provider answered 429 with "insufficient_quota"; the
runtime took the 429 for a rate limit and retried the call four times, and
the run's error said only "Model encountered an error, please do retry
again". An exhausted balance and a rejected key are not retried, and the
run says what is wrong.
"""

from __future__ import annotations

import pytest

from omnicoreagent.core.llm import _is_retryable

NO_CREDITS = (
    'litellm.RateLimitError: RateLimitError: OpenAIException - {"error": {"message": '
    '"You have no credits remaining. Add credits to continue using the API.", '
    '"type": "insufficient_quota", "code": "credit_balance_exhausted"}}'
)
BAD_KEY = "litellm.AuthenticationError: OpenAIException - Incorrect API key provided: sk-...abcd (invalid_api_key)"


def test_an_account_that_cannot_pay_or_authenticate_is_not_retried():
    assert not _is_retryable(Exception(NO_CREDITS))
    assert not _is_retryable(Exception(BAD_KEY))
    assert _is_retryable(Exception("litellm.RateLimitError: Rate limit reached for requests (429)"))


@pytest.mark.asyncio
async def test_the_run_says_the_account_has_no_credits(monkeypatch):
    from omnicoreagent.core.agents import llm_step
    from omnicoreagent.core.token_usage import Usage
    from test_llm_step import make_runner, make_session_state

    monkeypatch.setattr(llm_step, "usage", Usage())

    class NoCredits:
        async def llm_call(self, messages, tools=None):
            raise Exception(NO_CREDITS)

    result = await make_runner().run(
        session_state=make_session_state(),
        llm_connection=NoCredits(),
        run_usage=Usage(),
        session_id="broke",
    )

    answer = result.error_result["answer"]
    assert "no credits" in answer.lower() and "insufficient_quota" in answer, answer
    assert result.error_result["termination_reason"] == "provider_error"
