"""
Agent with Tool Response Offloading

This example demonstrates how OmniCoreAgent automatically offloads large tool
responses to the file system, keeping only a preview in the LLM context.

This pattern (inspired by Cursor and Anthropic engineering blogs) can reduce
context overhead by up to 98% for tool-heavy agents!

Key Benefits:
    - Large API responses don't bloat context window
    - Agent can still access full data on demand
    - Reduces costs and prevents token limit errors
"""

import asyncio

from omnicoreagent import OmniCoreAgent, ToolRegistry

from _bootstrap import model_config, require_llm_api_key, response_text


# Create a tool that returns a large response
def search_web(query: str) -> dict:
    """
    Simulate a web search that returns many results.
    In production, this could be a real search API that returns 50+ results.
    """
    # Simulate a large response (e.g., 50 search results)
    results = []
    for i in range(50):
        results.append(
            {
                "title": f"Result {i + 1}: Information about {query}",
                "url": f"https://example.com/article-{i + 1}",
                "snippet": f"This is a detailed snippet about {query}. "
                * 10,  # Make it long
                "metadata": {
                    "date": "2024-01-15",
                    "author": f"Author {i + 1}",
                    "category": "Technology",
                    "tags": ["AI", "search", query, f"tag{i}"],
                    "relevance_score": 0.95 - (i * 0.01),
                },
            }
        )

    return {
        "status": "success",
        "data": {
            "query": query,
            "total_results": 1250,
            "page": 1,
            "results": results,
            "metadata": {
                "search_time_ms": 234,
                "api_version": "2.0",
            },
        },
        "message": "Search results retrieved successfully",
    }


def fetch_document(document_id: str) -> dict:
    """
    Simulate fetching a large document (e.g., a PDF or report).
    """
    # Simulate a large document content
    content_lines = [
        f"Line {i}: This is paragraph {i} of the document about {document_id}. " * 5
        for i in range(100)
    ]

    return {
        "status": "success",
        "data": {
            "document_id": document_id,
            "title": f"Comprehensive Report: {document_id}",
            "content": "\n".join(content_lines),
            "metadata": {
                "pages": 25,
                "word_count": 15000,
                "created": "2024-01-10",
            },
        },
        "message": "Document fetched successfully",
    }


async def main():
    require_llm_api_key()

    print("=" * 60)
    print("🗂️  Tool Response Offloading Demo")
    print("=" * 60)

    # Register tools
    tools = ToolRegistry()
    tools.register_tool("search_web")(search_web)
    tools.register_tool("fetch_document")(fetch_document)

    # Create agent WITH tool offloading enabled
    agent = OmniCoreAgent(
        name="OffloadingAgent",
        system_instruction="""You are a research assistant that uses web search tools.
When tool results are offloaded to files, you'll see a preview with a file reference.
Use the read_artifact tool if you need to see the full content.""",
        model_config=model_config(max_tokens=900),
        local_tools=tools,
        agent_config={
            "max_steps": 10,
            "tool_call_timeout": 30,
            # === TOOL RESPONSE OFFLOADING ===
            # This is the key configuration!
            "tool_offload": {
                "enabled": True,  # Enable offloading
                "threshold_tokens": 300,  # Offload if >300 tokens (low for demo)
                "threshold_bytes": 1000,  # Or >1KB
                "max_preview_tokens": 100,  # Show first ~100 tokens in context
            },
            # Offloaded artifacts are written through the workspace. Use
            # workspace_config or OMNICOREAGENT_WORKSPACE_DIR to choose the
            # local/S3/R2 storage location.
        },
        debug=True,
    )

    print("\n📊 Tool Offload Configuration:")
    print("  • Threshold: 300 tokens (or 1KB)")
    print("  • Preview: First 100 tokens")
    print("  • Storage: workspace artifacts")

    print("\n🔄 Running agent with large tool response...")
    print("-" * 60)

    # This query triggers two independent large tool calls so offloading is visible.
    response = await agent.run(
        query=(
            "Search for 'artificial intelligence trends 2024' and fetch document "
            "'ai-trends-report'. Use both tools together, then summarize the top 3 points."
        ),
        session_id="offload_demo_session",
    )

    print("\n" + "=" * 60)
    print("📝 Agent Response:")
    print("=" * 60)
    response = response_text(response)
    print(response[:1000] if len(response) > 1000 else response)

    # Show offloading stats
    print("\n📊 Offloading Statistics:")
    print("-" * 60)
    stats = agent.agent.tool_offloader.get_stats()
    print(f"  • Artifacts created: {stats['offload_count']}")
    print(f"  • Tokens saved: {stats['tokens_saved']}")

    # List artifacts
    artifacts = agent.agent.tool_offloader.list_artifacts()
    if artifacts:
        print("\n📁 Offloaded Artifacts:")
        for a in artifacts:
            print(f"  • {a['id']}")
            print(f"    Tool: {a['tool']}, Tokens saved: {a['tokens_saved']}")

    print("\n✅ Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
