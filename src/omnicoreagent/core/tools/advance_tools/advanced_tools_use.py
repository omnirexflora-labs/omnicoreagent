from typing import List, Any, Optional, Tuple, Dict
from omnicoreagent.core.utils import (
    logger,
    normalize_enriched_tool,
)
import json


from omnicoreagent.core.constants import TOOLS_REGISTRY
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
import time


def tokenize(text: str) -> List[str]:
    """Tokenize text using same logic as document preparation"""
    if not text or not isinstance(text, str):
        return []
    return re.findall(r"\w+", text.lower())


@dataclass
class RetrievalConfig:
    """Configuration for BM25 retrieval parameters"""

    k1: float = 1.5
    b: float = 0.75


@dataclass
class ToolDocument:
    """Structured representation of a tool document"""

    tool_name: str
    tool_description: str
    tool_parameters: dict
    mcp_server_name: str
    tokens: List[str]
    raw_text: str

    def __post_init__(self):
        if not self.tokens:
            self.tokens = tokenize(self.raw_text)


class ToolRetriever:
    """BM25-based tool retrieval system"""

    def __init__(self):
        self.config = RetrievalConfig()

    def _prepare_tool_document(self, tool: Dict[str, Any]) -> Optional[ToolDocument]:
        try:
            raw_tool = tool.get("raw_tool")
            enriched_tool = tool.get("enriched_tool")
            mcp_server_name = tool.get("mcp_server_name")
            tool_name = raw_tool.get("name", "")
            tool_description = raw_tool.get("description", "")
            tool_parameters = raw_tool.get("parameters", {})

            if not tool_name or not mcp_server_name:
                logger.warning(f"Tool missing required fields: {tool}")
                return None

            tokens = list(tokenize(enriched_tool))

            return ToolDocument(
                tool_name=tool_name,
                tool_description=tool_description,
                tool_parameters=tool_parameters,
                mcp_server_name=mcp_server_name,
                tokens=tokens,
                raw_text=enriched_tool,
            )
        except Exception as e:
            logger.error(f"Error preparing tool document: {e}, tool: {tool}")
            return None

    def _compute_idf_scores(self, documents: List[ToolDocument]) -> Dict[str, float]:
        if not documents:
            return {}

        N = len(documents)
        term_doc_freq = defaultdict(int)

        for doc in documents:
            unique_terms = set(doc.tokens)
            for term in unique_terms:
                term_doc_freq[term] += 1

        idf_scores = {}
        for term, df in term_doc_freq.items():
            idf_scores[term] = math.log((N - df + 0.5) / (df + 0.5) + 1)

        return idf_scores

    def bm25_score(
        self, query_tokens: List[str], documents: List[ToolDocument]
    ) -> List[Tuple[float, ToolDocument]]:
        if not query_tokens or not documents:
            return []

        N = len(documents)
        if N == 0:
            return []

        total_doc_length = sum(len(doc.tokens) for doc in documents)
        avgdl = total_doc_length / N if N > 0 else 0

        idf_scores = self._compute_idf_scores(documents)
        scored_docs = []

        for doc in documents:
            if not doc.tokens:
                scored_docs.append((0.0, doc))
                continue

            score = 0.0
            doc_len = len(doc.tokens)
            term_frequencies = Counter(doc.tokens)

            for query_term in query_tokens:
                if query_term not in idf_scores:
                    continue

                tf = term_frequencies.get(query_term, 0)
                if tf == 0:
                    continue

                idf = idf_scores[query_term]
                numerator = tf * (self.config.k1 + 1)
                denominator = tf + self.config.k1 * (
                    1
                    - self.config.b
                    + self.config.b * (doc_len / avgdl if avgdl > 0 else 1)
                )

                score += idf * (numerator / denominator)

            scored_docs.append((score, doc))

        return scored_docs

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant tools using BM25 scoring against MCP_TOOLS_REGISTRY"""
        start_time = time.time()

        if not query or not isinstance(query, str):
            return []

        stored_tools = TOOLS_REGISTRY
        if not stored_tools or not isinstance(stored_tools, dict):
            return []

        query = query.strip()
        if not query:
            return []

        try:
            all_tools = list(stored_tools.values())

            documents = [self._prepare_tool_document(tool=tool) for tool in all_tools]
            documents = [doc for doc in documents if doc]

            if not documents:
                return []

            normalized_query = normalize_enriched_tool(enriched=query)
            query_tokens = tokenize(normalized_query)

            if not query_tokens:
                return []

            scored_docs = self.bm25_score(query_tokens, documents)
            if not scored_docs:
                return []

            scored_docs.sort(key=lambda x: x[0], reverse=True)
            top_docs = scored_docs[:top_k]

            results = []
            for score, doc in top_docs:
                results.append(
                    {
                        "mcp_server_name": doc.mcp_server_name,
                        "raw_tool": {
                            "name": doc.tool_name,
                            "description": doc.tool_description,
                            "parameters": doc.tool_parameters,
                        },
                    }
                )

            logger.debug(
                f"Retrieved {len(results)} tools for query '{query}' "
                f"(normalized: '{normalized_query}') in {time.time() - start_time:.3f}s"
            )
            return results

        except Exception as e:
            logger.error(f"Error in retrieve: {e}", exc_info=True)
            return []


class AdvanceToolsUse:
    """Manages MCP and local tools using in-memory registry and BM25 retrieval."""

    def load_and_process_tools(
        self, mcp_tools: Dict[str, List[Any]] = None, local_tools: Any = None
    ):
        """
        Load all tools from MCP servers into the in-memory registry.
        This overwrites any existing tools in MCP_TOOLS_REGISTRY.
        """
        logger.info("Starting tool load and process...")
        TOOLS_REGISTRY.clear()
        if mcp_tools:
            for server_name, tools in mcp_tools.items():
                logger.info(f"[{server_name}] Processing {len(tools)} tools")
                for tool in tools:
                    try:
                        name = getattr(tool, "name", None) or tool.get("name")
                        name = str(name)
                        description = (
                            getattr(tool, "description", None)
                            or tool.get("description")
                            or ""
                        )
                        input_schema = (
                            getattr(tool, "inputSchema", None)
                            or tool.get("inputSchema")
                            or {}
                        )
                        args = (
                            input_schema.get("properties", {})
                            if isinstance(input_schema, dict)
                            else {}
                        )

                        tool_payload = {
                            "name": name,
                            "description": str(description),
                            "parameters": args,
                        }

                        enriched = f"{name} {description} {json.dumps(args)}"

                        document = {
                            "mcp_server_name": server_name,
                            "raw_tool": tool_payload,
                            "enriched_tool": normalize_enriched_tool(enriched=enriched),
                        }

                        TOOLS_REGISTRY[name] = document

                    except Exception as exc:
                        logger.error(
                            f"[{server_name}] Error processing tool {getattr(tool, 'name', None)}: {exc}"
                        )

        if local_tools:
            local_tools_list = local_tools.get_available_tools()
            if local_tools_list:
                for tool in local_tools_list:
                    if isinstance(tool, dict):
                        name = tool.get("name", "unknown")
                        description = (
                            tool.get("description", "").replace("\n", " ").strip()
                        )
                        input_schema = tool.get("inputSchema", {})
                        args = input_schema.get("properties", {})

                        tool_payload = {
                            "name": name,
                            "description": str(description),
                            "parameters": args,
                        }

                        enriched = f"{name} {description} {json.dumps(args)}"

                        document = {
                            "mcp_server_name": "local_tools",
                            "raw_tool": tool_payload,
                            "enriched_tool": normalize_enriched_tool(enriched=enriched),
                        }

                        TOOLS_REGISTRY[name] = document

        logger.info(f"Loaded {len(TOOLS_REGISTRY)} tools into registry.")

    async def tools_retrieval(self, query: str):
        """
        Retrieve tools using BM25 against the loaded registry.
        """
        retriever = ToolRetriever()
        results = await retriever.retrieve(query=query)
        return results if results else ["No tools found"]
