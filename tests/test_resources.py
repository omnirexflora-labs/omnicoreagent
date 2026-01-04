from unittest.mock import AsyncMock

import pytest

from omnicoreagent.mcp_clients_connection.resources import (
    find_resource_server,
    list_resources,
    read_resource,
)


# Mock data for testing
class MockResource:
    def __init__(self, uri, name):
        self.uri = uri
        self.name = name


MOCK_RESOURCES = {
    "server1": [
        MockResource("resource1", "Resource 1"),
        MockResource("resource2", "Resource 2"),
    ],
    "server2": [
        MockResource("resource3", "Resource 3"),
    ],
}

MOCK_SESSIONS = {
    "server1": {
        "session": None,
        "connected": True,
    },
    "server2": {
        "session": None,
        "connected": True,
    },
}


@pytest.mark.asyncio
async def test_list_resources():
    """Test listing resources from servers"""

    # Mock the list_resources method
    async def mock_list_resources():
        class MockResponse:
            def __init__(self, resources):
                self.resources = resources  # Use MockResource objects directly

        return MockResponse(MOCK_RESOURCES["server1"])

    # Update mock sessions with mock method
    test_sessions = MOCK_SESSIONS.copy()
    test_sessions["server1"]["session"] = AsyncMock()
    test_sessions["server1"]["session"].list_resources.side_effect = mock_list_resources

    resources = await list_resources(
        server_names=["server1", "server2"], sessions=test_sessions
    )

    # Assertions
    assert len(resources) == 2
    assert all(hasattr(res, "uri") and hasattr(res, "name") for res in resources)
    assert {res.uri for res in resources} == {"resource1", "resource2"}


@pytest.mark.asyncio
async def test_find_resource_server():
    """Test finding a resource server"""
    # Test existing resource
    server_name, found = await find_resource_server("resource1", MOCK_RESOURCES)
    assert found is True
    assert server_name == "server1"

    # Test non-existing resource
    server_name, found = await find_resource_server("non-existent", MOCK_RESOURCES)
    assert found is False
    assert server_name == ""


# @pytest.mark.asyncio
# async def test_read_resource_usage_limit():
#     """Test reading a resource when usage limits are exceeded"""

#     # Mock the read_resource method
#     async def mock_read_resource(*args, **kwargs):
#         uri = args[0]  # Extract the URI from the arguments
#         return f"Content of {uri}"

#     # Mock LLM call to return mock response
#     async def mock_llm_call(messages):
#         return type(
#             "MockResponse",
#             (object,),
#             {
#                 "choices": [
#                     type(
#                         "MockChoice",
#                         (object,),
#                         {
#                             "message": type(
#                                 "MockMessage",
#                                 (object,),
#                                 {"content": "Processed content"},
#                             )()
#                         },
#                     )()
#                 ]
#             },
#         )()

#     # Ensure that the real `UsageLimits` raises the ValueError when request_limit is 0 or negative
#     with pytest.raises(ValueError, match="request_limit must be positive if specified"):
#         # Trigger the real UsageLimits with invalid request_limit
#         usage_limits = UsageLimits(request_limit=0)

#     # Now proceed with the mock behavior for the rest of the test case
#     class MockUsageLimits:
#         def __init__(self, request_limit=None, total_tokens_limit=None):
#             self.request_limit = request_limit
#             self.total_tokens_limit = total_tokens_limit

#         def check_before_request(self, usage):
#             if self.request_limit <= 0:
#                 raise UsageLimitExceeded("Request limit exceeded.")

#         def check_tokens(self, usage):
#             pass  # Mock the token check

#         def remaining_tokens(self, usage):
#             return 0

#     usage_limits = MockUsageLimits(request_limit=0)  # No requests allowed

#     # Update mock sessions with mock method
#     test_sessions = MOCK_SESSIONS.copy()
#     test_sessions["server1"]["session"] = type(
#         "MockSession", (), {"read_resource": mock_read_resource}
#     )()

#     # Test that usage limit exceeded error is raised
#     content = await read_resource(
#         uri="resource1",
#         sessions=test_sessions,
#         available_resources=MOCK_RESOURCES,
#         llm_call=mock_llm_call,
#         request_limit=0,
#         debug=False,
#     )

#     assert content == "Usage limit error: Request limit exceeded."


@pytest.mark.asyncio
async def test_read_resource_with_error():
    """Test reading a resource when an error occurs"""

    # Mock the read_resource method to raise an exception
    async def mock_read_resource(*args, **kwargs):
        raise Exception("Unexpected error while reading resource.")

    # Mock LLM call to return mock response
    async def mock_llm_call(messages):
        return type(
            "MockResponse",
            (object,),
            {
                "choices": [
                    type(
                        "MockChoice",
                        (object,),
                        {
                            "message": type(
                                "MockMessage",
                                (object,),
                                {"content": "Processed content"},
                            )()
                        },
                    )()
                ]
            },
        )()

    # Update mock sessions with mock method
    test_sessions = MOCK_SESSIONS.copy()
    test_sessions["server1"]["session"] = type(
        "MockSession", (), {"read_resource": mock_read_resource}
    )()

    # Test that the error is handled correctly
    content = await read_resource(
        uri="resource1",
        sessions=test_sessions,
        available_resources=MOCK_RESOURCES,
        llm_call=mock_llm_call,
        debug=False,
    )
    assert content == "Error reading resource: Unexpected error while reading resource."
