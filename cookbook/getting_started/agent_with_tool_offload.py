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
import os
import json
from dotenv import load_dotenv

from omnicoreagent import OmniCoreAgent
from omnicoreagent.core.tools import LocalToolsIntegration

load_dotenv()


# Create a tool that returns a large response
def search_web(query: str) -> dict:
    """
    Simulate a web search that returns many results.
    In production, this could be a real search API that returns 50+ results.
    """
    # Simulate a large response (e.g., 50 search results)
    results = []
    for i in range(50):
        results.append({
            "title": f"Result {i+1}: Information about {query}",
            "url": f"https://example.com/article-{i+1}",
            "snippet": f"This is a detailed snippet about {query}. " * 10,  # Make it long
            "metadata": {
                "date": "2024-01-15",
                "author": f"Author {i+1}",
                "category": "Technology",
                "tags": ["AI", "search", query, f"tag{i}"],
                "relevance_score": 0.95 - (i * 0.01),
            }
        })
    
    return {
        "query": query,
        "total_results": 1250,
        "page": 1,
        "results": results,
        "metadata": {
            "search_time_ms": 234,
            "api_version": "2.0",
        }
    }


def fetch_document(document_id: str) -> dict:
    """
    Simulate fetching a large document (e.g., a PDF or report).
    """
    # Simulate a large document content
    content_lines = [f"Line {i}: This is paragraph {i} of the document about {document_id}. " * 5 
                     for i in range(100)]
    
    return {
        "document_id": document_id,
        "title": f"Comprehensive Report: {document_id}",
        "content": "\n".join(content_lines),
        "metadata": {
            "pages": 25,
            "word_count": 15000,
            "created": "2024-01-10",
        }
    }


async def main():
    print("=" * 60)
    print("🗂️  Tool Response Offloading Demo")
    print("=" * 60)
    
    # Register tools
    tools_integration = LocalToolsIntegration()
    tools_integration.add_tool(search_web)
    tools_integration.add_tool(fetch_document)
    
    # Create agent WITH tool offloading enabled
    agent = OmniCoreAgent(
        name="OffloadingAgent",
        system_instruction="""You are a research assistant that uses web search tools.
When tool results are offloaded to files, you'll see a preview with a file reference.
Use the read_artifact tool if you need to see the full content.""",
        model_config={
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "api_key": os.getenv("LLM_API_KEY"),
        },
        local_tools=tools_integration,
        agent_config={
            "max_steps": 10,
            "tool_call_timeout": 30,
            
            # === TOOL RESPONSE OFFLOADING ===
            # This is the key configuration!
            "tool_offload": {
                "enabled": True,              # Enable offloading
                "threshold_tokens": 300,      # Offload if >300 tokens (low for demo)
                "threshold_bytes": 1000,      # Or >1KB
                "max_preview_tokens": 100,    # Show first ~100 tokens in context
                "storage_dir": ".omnicoreagent_artifacts",
            },
        },
        debug=True,
    )
    
    print("\n📊 Tool Offload Configuration:")
    print("  • Threshold: 300 tokens (or 1KB)")
    print("  • Preview: First 100 tokens")
    print("  • Storage: .omnicoreagent_artifacts/")
    
    print("\n🔄 Running agent with large tool response...")
    print("-" * 60)
    
    # This query will trigger search_web which returns a LARGE response
    response = await agent.run(
        query="Search for 'artificial intelligence trends 2024' and summarize the top 3 results",
        session_id="offload_demo_session"
    )
    
    print("\n" + "=" * 60)
    print("📝 Agent Response:")
    print("=" * 60)
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
