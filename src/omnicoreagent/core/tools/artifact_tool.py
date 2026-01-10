"""
Artifact Tool for accessing offloaded tool responses.

When tool responses are large (exceed token threshold), they are saved to files
and only a preview is shown in context. This tool allows the agent to retrieve
the full content on demand.

Based on patterns from:
- Cursor's "Turning long tool responses into files"
- Anthropic's "Context efficient tool results"
"""

from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from omnicoreagent.core.tool_response_offloader import ToolResponseOffloader


def build_tool_registry_artifact_tool(
    offloader: ToolResponseOffloader, registry: ToolRegistry
) -> ToolRegistry:
    """
    Register artifact access tools in a ToolRegistry.
    
    These tools allow the agent to retrieve full content from offloaded
    tool responses that were saved to files to reduce context size.
    """
    
    @registry.register_tool(
        name="read_artifact",
        description="""
        Read the full content of an offloaded tool response.
        
        When a tool returns a large response (e.g., web search with 50 results,
        large API response), it gets saved to a file and only a preview is shown.
        Use this tool to retrieve the complete content when you need all the details.
        
        You will see artifact IDs in messages like:
        "[TOOL RESPONSE OFFLOADED] ... Use read_artifact('artifact_id') to load full content"
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "artifact_id": {
                    "type": "string",
                    "description": "The artifact ID shown in the offloaded response message",
                }
            },
            "required": ["artifact_id"],
            "additionalProperties": False,
        },
    )
    def read_artifact(artifact_id: str) -> str:
        content = offloader.read_artifact(artifact_id)
        if content is None:
            return f"Error: Artifact '{artifact_id}' not found. Check the artifact ID and try again."
        return content
    
    @registry.register_tool(
        name="tail_artifact",
        description="""
        Read the last N lines of an offloaded artifact.
        
        Useful for log files or streaming data where you want to see 
        the most recent content without loading everything.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "artifact_id": {
                    "type": "string",
                    "description": "The artifact ID to read",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of lines from the end (default: 50)",
                    "default": 50,
                }
            },
            "required": ["artifact_id"],
            "additionalProperties": False,
        },
    )
    def tail_artifact(artifact_id: str, lines: int = 50) -> str:
        content = offloader.tail_artifact(artifact_id, lines)
        if content is None:
            return f"Error: Artifact '{artifact_id}' not found."
        return content
    
    @registry.register_tool(
        name="search_artifact",
        description="""
        Search for specific text within an offloaded artifact.
        
        Returns matching lines with surrounding context. Useful when looking 
        for specific information in a large tool response without loading 
        the entire file.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "artifact_id": {
                    "type": "string",
                    "description": "The artifact ID to search",
                },
                "query": {
                    "type": "string",
                    "description": "Search term (case-insensitive)",
                }
            },
            "required": ["artifact_id", "query"],
            "additionalProperties": False,
        },
    )
    def search_artifact(artifact_id: str, query: str) -> str:
        content = offloader.search_artifact(artifact_id, query)
        if content is None:
            return f"Error: Artifact '{artifact_id}' not found."
        return content
    
    @registry.register_tool(
        name="list_artifacts",
        description="""
        List all tool responses that have been offloaded in the current session.
        
        Shows artifact IDs, source tools, and tokens saved. Use this to see
        what offloaded data is available for retrieval.
        """,
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )
    def list_artifacts() -> str:
        artifacts = offloader.list_artifacts()
        if not artifacts:
            return "No artifacts have been offloaded in this session."
        
        lines = ["Offloaded artifacts in this session:\n"]
        for a in artifacts:
            lines.append(
                f"• {a['id']}\n"
                f"  Tool: {a['tool']}, Tokens saved: {a['tokens_saved']}\n"
            )
        
        stats = offloader.get_stats()
        lines.append(f"\nTotal tokens saved: {stats['tokens_saved']}")
        
        return "\n".join(lines)
    
    return registry
