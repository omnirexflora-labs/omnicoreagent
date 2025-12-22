from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tools.advance_tools.advanced_tools_use import AdvanceToolsUse


tools_retriever_local_tool = ToolRegistry()


@tools_retriever_local_tool.register_tool(
    name="tools_retriever",
    description="""
Searches the system's tool catalog using semantic BM25 matching to discover available capabilities.

Use this to find tools that can fulfill user requests. Search before claiming any functionality 
is unavailable. Returns up to 5 relevant tools.
    """,
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": """
Semantic search query describing the desired functionality.

Include: [ACTION VERB] + [TARGET OBJECT] + [RELEVANT CONTEXT]

Examples:
- "send email message with attachments to recipients"
- "get weather forecast temperature for location"
- "create calendar event with date time participants"
- "search documents files by keyword content"
- "analyze text extract keywords sentiment"

Length: 30-300 characters optimal for best BM25 matching.
                """,
                "minLength": 30,
                "maxLength": 500,
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
async def tools_retriever(
    query: str,
):
    """
    Discover available tools using BM25 semantic search.

    Parameters
    ----------
    query : str
        Natural language query: [action] [object] [context]
        Example: "send email with attachments to recipient"

    Returns
    -------
    dict
        {
            "status": "success" | "error",
            "data": List of up to 5 tools with descriptions and parameters
        }
    """
    tool_retriever = await AdvanceToolsUse().tools_retrieval(
        query=query,
    )

    return {"status": "success", "data": str(tool_retriever)}
