from omnicoreagent.core.tools.local_tools_registry import ToolRegistry
from typing import Callable
from omnicoreagent.core.utils import logger
from omnicoreagent.core.tools.advance_tools.advanced_tools_use import AdvanceToolsUse


tools_retriever_local_tool = ToolRegistry()


@tools_retriever_local_tool.register_tool(
    name="tools_retriever",
    description="""
   Advcance Tool Discovery and Retrieval System

    This is the primary tool discovery mechanism that searches through the Toolshed Knowledge Base 
    using advanced semantic similarity matching. It employs BM25 to find the most 
    relevant tools based on user intent, functionality requirements, and contextual needs.

    MANDATORY USAGE: This tool MUST be used before claiming any functionality is unavailable. 
    It is the gateway to discovering all available capabilities in the system.

    Core Functionality:
    - Searches through semantically enriched tool documents using BM25
    - Matches user queries against tool descriptions, parameters, synthetic questions, and key topics
    - Returns ranked list of most relevant tools for any given user request
    - Enables dynamic tool discovery without hardcoded tool knowledge

    When to Use:
    - ANY user request that involves taking actions (send, create, update, delete, etc.)
    - ANY request for accessing information (get, retrieve, check, view, etc.)
    - When user asks "Can you..." or "Do you have..." functionality questions
    - Before responding with limitations or claiming lack of capabilities
    - For complex multi-step tasks that may require multiple tools
    - When unsure what tools might help accomplish a user's goal

    Query Optimization Guidelines:
    - Use natural language that mirrors how users actually request functionality
    - Include action verbs, target objects, and contextual parameters
    - Add synonyms and related terms for broader semantic matching
    - For complex requests, consider multiple focused queries
    - Think about how tools would describe themselves in their documentation

    Expected Output:
    Returns a ranked list of tool objects, each containing:
    - Tool name and expanded readable name
    - Comprehensive description of functionality
    - Parameter schema with detailed argument descriptions
    - Relevance score indicating semantic similarity match
    - Usage examples and key topics for context

    This tool is essential for maintaining dynamic, extensible functionality discovery
    without requiring hardcoded knowledge of available tools.
    """,
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": """
                Semantic search query for tool discovery. This should be a well-crafted, 
                natural language query that captures the user's intent and desired functionality.

                Query Crafting Best Practices:
                
                1. Include Action Verbs: Start with or include the main action the user wants 
                   to perform (send, get, create, analyze, check, update, delete, etc.)
                
                2. Specify Target Objects: Include what the action applies to (email, file, 
                   data, calendar, user, document, etc.)
                
                3. Add Context and Parameters: Include relevant context like data types, 
                   locations, time periods, or specific attributes
                
                4. Use Natural Language: Write as if describing the functionality to another 
                   person, not as keyword search terms
                
                5. Include Synonyms: Add related terms and alternative ways to express 
                   the same concept for better semantic matching
                
                Examples of Well-Crafted Queries:
                - "send email message to recipient with subject and body content"
                - "get current weather conditions and temperature forecast for location"
                - "analyze sales data with statistics calculations and generate insights"
                - "create calendar appointment event with date time and participants"
                - "backup save copy files documents to secure storage location"
                - "search find information in database or documents by keyword"
                
                Poor Query Examples (avoid these):
                - "email" (too vague, missing action and context)
                - "data" (unclear intent, no action specified)
                - "file stuff" (imprecise language, unclear functionality)
                
                The quality of this query directly impacts tool discovery accuracy. 
                A well-crafted semantic query will find relevant tools even if they 
                use different terminology, while a poor query may miss available functionality.
                
                For complex multi-step requests, consider sending multiple focused queries 
                to discover different aspects of the required functionality.
                """,
                "minLength": 5,
                "maxLength": 500,
                "examples": [
                    "send email message to recipient with subject and body content",
                    "get current weather conditions and temperature forecast for location",
                    "analyze process sales data statistics with calculations and insights",
                    "create schedule meeting appointment calendar event with participants",
                    "backup save copy files documents to storage location securely",
                    "search find retrieve information from database documents by query",
                ],
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
async def tools_retriever(
    query: str,
    top_k: int,
    similarity_threshold: float,
):
    """
    Semantic Tool Discovery and Retrieval System

    Searches the Toolshed Knowledge Base using advanced semantic similarity matching
    to discover tools that can fulfill user requests and intentions.

    This function performs BM25 search against semantically enriched
    tool documents, returning ranked results based on relevance to the input query.

    Parameters
    ----------
    query : str
        Natural language query describing desired functionality or user intent.
        Should be semantically rich with action verbs, objects, and context.


    Returns
    -------
    list
        Ranked list of relevant tool objects, each containing:
        - tool_name: Identifier for the tool
        - expanded_name: Human-readable tool name
        - description: Comprehensive functionality description
        - parameters: Detailed parameter schema and descriptions
        - relevance_score: BM25 match score (0-1)



    """
    tool_retriever = await AdvanceToolsUse().tools_retrieval(
        query=query,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
    )

    return {"status": "success", "data": str(tool_retriever)}
