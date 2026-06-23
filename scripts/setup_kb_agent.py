"""Register a Foundry-hosted Knowledge-Base agent with the Azure AI Search tool.

This wires the agent directly to your Azure AI Search index via a Foundry
project *connection*, so the agent performs hybrid (vector + semantic) RAG
natively and is visible/traceable in the Foundry portal.

Prerequisites:
  * An Azure AI Search index created by scripts/setup_search_index.py.
  * A Foundry project connection to that Search service. Its connection id
    goes in AZURE_SEARCH_CONNECTION_ID (full ARM-style connection id).

Run:
    $env:PYTHONPATH = "src"
    python scripts/setup_kb_agent.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from insurance_rag_agent.config import get_settings  # noqa: E402

load_dotenv(override=False)

INSTRUCTIONS = (
    "You are the Auto-Insurance Knowledge-Base agent. You answer conceptual and "
    "educational questions about auto insurance: coverages, deductibles, premiums, "
    "the claims process, and terminology.\n\n"
    "RULES:\n"
    "- ALWAYS search the knowledge base (Azure AI Search) before answering.\n"
    "- Ground every answer in retrieved passages and cite the source document.\n"
    "- For specific policy/claim/customer DATA (IDs, names, amounts), say you only "
    "handle general knowledge and defer to the policy data tools.\n"
    "- Keep answers concise and accurate."
)


def main() -> None:
    settings = get_settings()
    connection_id = os.getenv("AZURE_SEARCH_CONNECTION_ID", "")
    if not connection_id:
        raise SystemExit(
            "AZURE_SEARCH_CONNECTION_ID is not set. Create a Foundry project "
            "connection to your Azure AI Search service and paste its connection id."
        )

    client = AIProjectClient(
        endpoint=settings.foundry_project_endpoint,
        credential=DefaultAzureCredential(),
    )

    body = {
        "definition": {
            "kind": "prompt",
            "model": settings.foundry_kb_agent_model,
            "instructions": INSTRUCTIONS,
            "tools": [
                {
                    "type": "azure_ai_search",
                    "azure_ai_search": {
                        "indexes": [
                            {
                                "project_connection_id": connection_id,
                                "index_name": settings.search_index,
                                "query_type": "vector_semantic_hybrid",
                                "top_k": settings.search_top_k,
                            }
                        ]
                    },
                }
            ],
        }
    }

    version = client.agents.create_version(
        agent_name=settings.kb_agent_name,
        body=body,
        description="Knowledge-base agent with Azure AI Search (vector + semantic hybrid).",
    )
    print(f"Created {settings.kb_agent_name} version: {version.id}")
    print(json.dumps(version.as_dict(), indent=2, default=str))


if __name__ == "__main__":
    main()
