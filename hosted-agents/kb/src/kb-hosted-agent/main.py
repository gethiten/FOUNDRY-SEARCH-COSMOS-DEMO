# Copyright (c) Microsoft. All rights reserved.
"""Hosted Knowledge-Base agent.

Mirrors the prompt `kb-search-agent`: answers conceptual / educational
auto-insurance questions grounded in the Azure AI Search index (`insurance-kb`).
Search is queried keyless via the hosted agent's managed identity.
"""

import os

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizableTextQuery
from dotenv import load_dotenv
from pydantic import Field
from typing_extensions import Annotated

# Load environment variables from .env file
load_dotenv()

_SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT", "")
_SEARCH_INDEX = os.environ.get("AZURE_SEARCH_INDEX", "insurance-kb")
_SEMANTIC_CONFIG = os.environ.get("AZURE_SEARCH_SEMANTIC_CONFIG", "insurance-kb-semantic")
_VECTOR_FIELD = os.environ.get("AZURE_SEARCH_VECTOR_FIELD", "contentVector")
_TOP_K = int(os.environ.get("AZURE_SEARCH_TOP_K", "5"))
_SEARCH_API_KEY = os.environ.get("AZURE_SEARCH_API_KEY", "")

_credential = DefaultAzureCredential()
# Use the Search query key when provided (reliable, no RBAC propagation); fall
# back to the managed identity (keyless) otherwise.
_search_credential = AzureKeyCredential(_SEARCH_API_KEY) if _SEARCH_API_KEY else _credential
_search_client = SearchClient(
    endpoint=_SEARCH_ENDPOINT,
    index_name=_SEARCH_INDEX,
    credential=_search_credential,
)


@tool(approval_mode="never_require")
def search_knowledge_base(
    query: Annotated[str, Field(description="The conceptual insurance question to look up.")],
) -> str:
    """Search the auto-insurance knowledge base for grounding passages.

    Use for general / educational questions (what a deductible is, how
    comprehensive coverage works, the claims process, terminology).
    """
    vector_query = VectorizableTextQuery(
        text=query, k_nearest_neighbors=_TOP_K, fields=_VECTOR_FIELD
    )
    try:
        results = _search_client.search(
            search_text=query,
            vector_queries=[vector_query],
            query_type="semantic",
            semantic_configuration_name=_SEMANTIC_CONFIG,
            top=_TOP_K,
        )
        docs = list(results)
    except Exception:
        # Fall back to plain hybrid search if semantic config is unavailable.
        results = _search_client.search(
            search_text=query, vector_queries=[vector_query], top=_TOP_K
        )
        docs = list(results)

    if not docs:
        return "No relevant passages found in the knowledge base."

    chunks = []
    for d in docs:
        source = d.get("source") or d.get("title") or "knowledge-base"
        content = (d.get("content") or "").strip()
        if content:
            chunks.append(f"[source: {source}]\n{content}")
    return "\n\n---\n\n".join(chunks) if chunks else "No relevant passages found."


INSTRUCTIONS = (
    "You are the Auto-Insurance Knowledge-Base agent. You answer general and "
    "educational insurance questions (what a deductible is, how comprehensive "
    "coverage works, the claims process, terminology).\n\n"
    "RULES:\n"
    "- ALWAYS call search_knowledge_base before answering and ground your answer "
    "strictly in the returned passages; never invent facts or definitions.\n"
    "- Cite the source document(s) you used.\n"
    "- If the knowledge base has no relevant passage, say so plainly.\n"
    "- For specific policy/claim/customer data lookups, explain that you only "
    "handle general knowledge and defer to the policy agent.\n"
    "- Keep answers concise and clear."
)


def main():
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=_credential,
    )

    agent = Agent(
        client=client,
        instructions=INSTRUCTIONS,
        tools=[search_knowledge_base],
        # History will be managed by the hosting infrastructure, thus there
        # is no need to store history by the service. Learn more at:
        # https://developers.openai.com/api/reference/resources/responses/methods/create
        default_options={"store": False},
    )

    server = ResponsesHostServer(agent)
    server.run()


if __name__ == "__main__":
    main()
