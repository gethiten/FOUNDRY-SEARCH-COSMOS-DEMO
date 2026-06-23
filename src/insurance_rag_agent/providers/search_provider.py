"""Azure AI Search provider — knowledge-base retrieval (agentic RAG).

Performs hybrid retrieval: keyword + vector (integrated vectorization) +
semantic reranking. Authentication prefers Microsoft Entra ID
(DefaultAzureCredential); falls back to an admin/query key if set.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import (
    QueryType,
    VectorizableTextQuery,
)

from insurance_rag_agent.config import Settings

logger = logging.getLogger("insurance_rag.search")


class SearchKnowledgeProvider:
    """Knowledge-base retrieval over an Azure AI Search index."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        credential = (
            AzureKeyCredential(settings.search_api_key)
            if settings.search_api_key
            else DefaultAzureCredential()
        )
        self._client = SearchClient(
            endpoint=settings.search_endpoint,
            index_name=settings.search_index,
            credential=credential,
        )

    def search(self, query: str, top: int | None = None) -> list[dict]:
        """Hybrid (keyword + vector) search with semantic reranking."""
        top = top or self._settings.search_top_k
        vector_query = VectorizableTextQuery(
            text=query,
            k_nearest_neighbors=top,
            fields=self._settings.search_vector_field,
        )
        results = self._client.search(
            search_text=query,
            vector_queries=[vector_query],
            query_type=QueryType.SEMANTIC,
            semantic_configuration_name=self._settings.search_semantic_config,
            top=top,
            select=["id", "title", "content", "source"],
        )
        hits: list[dict] = []
        for r in results:
            hits.append(
                {
                    "id": r.get("id"),
                    "title": r.get("title"),
                    "content": r.get("content"),
                    "source": r.get("source"),
                    "score": r.get("@search.reranker_score") or r.get("@search.score"),
                }
            )
        return hits

    def search_text(self, query: str, top: int | None = None) -> str:
        """Return retrieved chunks formatted as a grounding context string."""
        hits = self.search(query, top)
        if not hits:
            return "No relevant knowledge-base content found."
        blocks = []
        for h in hits:
            title = h.get("title") or h.get("source") or "KB"
            blocks.append(f"[{title}]\n{h.get('content', '').strip()}")
        return "\n\n---\n\n".join(blocks)


@lru_cache(maxsize=1)
def get_search_provider() -> SearchKnowledgeProvider:
    from insurance_rag_agent.config import get_settings

    return SearchKnowledgeProvider(get_settings())
