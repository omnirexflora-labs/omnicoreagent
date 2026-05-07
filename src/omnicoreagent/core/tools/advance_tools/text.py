import re


def normalize_enriched_tool(enriched: str) -> str:
    """Normalize enriched tool text for BM25 indexing and retrieval."""
    if not enriched:
        return ""

    text = enriched.lower()
    text = re.sub(r'[{}\[\]":\',]', " ", text)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"_", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
