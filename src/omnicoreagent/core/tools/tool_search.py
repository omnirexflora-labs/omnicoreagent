from typing import List, Tuple, Dict


import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass


def tokenize(text: str) -> List[str]:
    """Tokenize text using same logic as document preparation"""
    if not text or not isinstance(text, str):
        return []
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text).replace("_", " ")
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
