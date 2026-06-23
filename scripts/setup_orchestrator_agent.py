"""Register the Foundry-hosted Orchestrator (router) agent.

This is a single *prompt* agent that holds BOTH capabilities and routes each
question to the right one:

  * the native **Azure AI Search** tool (same as the knowledge-base agent) for
    conceptual / educational insurance questions, and
  * the **OpenAPI** policy_data tool (same as the policy agent) for specific
    policy / claim / customer data from Azure Cosmos DB.

Rather than delegating to the two separate agents at runtime (Foundry's prompt
agents have no native "connected agent" tool), it combines their tools so the
model itself decides which to call — the standard multi-tool router pattern.

Prerequisites (already needed by the other two agents):
  * AZURE_SEARCH_CONNECTION_ID — Foundry project connection to Azure AI Search.
  * POLICY_API_BASE_URL — public HTTPS URL of the FastAPI /api/* app.

Run:
    $env:PYTHONPATH = "src"
    python scripts/setup_orchestrator_agent.py
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
from setup_policy_agent import _openapi_spec  # noqa: E402  (reuse the spec builder)

load_dotenv(override=False)

INSTRUCTIONS = (
    "You are the Auto-Insurance Orchestrator. You answer any auto-insurance "
    "question by routing it to the correct tool.\n\n"
    "ROUTING RULES:\n"
    "- For SPECIFIC policy, claim, or customer DATA (policy IDs like POL-001, "
    "policy numbers, customer names, deductibles for a named policy, claim "
    "status, premiums, coverage totals), call the policy_data OpenAPI tool. "
    "Pick the right operation: getPolicy, searchPoliciesByName, getCustomer, "
    "searchCustomers, getClaims, listPolicies, getCoverageSummary.\n"
    "- For GENERAL or EDUCATIONAL questions (what is a deductible, how does "
    "comprehensive coverage work, the claims process, terminology), call the "
    "Azure AI Search knowledge-base tool and ground your answer in the retrieved "
    "passages, citing the source document.\n"
    "- If a question needs both (e.g. 'explain the deductible on POL-001'), call "
    "the policy_data tool for the data AND the knowledge-base tool for the "
    "concept, then combine them.\n\n"
    "GENERAL RULES:\n"
    "- ALWAYS call a tool to get real data or grounded knowledge before "
    "answering; never invent policy IDs, names, amounts, or definitions.\n"
    "- If a required identifier is missing, ask the user for it.\n"
    "- Be concise and clearly state which facts came from policy data vs. the "
    "knowledge base."
)


def main() -> None:
    settings = get_settings()

    connection_id = os.getenv("AZURE_SEARCH_CONNECTION_ID", "")
    if not connection_id:
        raise SystemExit(
            "AZURE_SEARCH_CONNECTION_ID is not set. Create a Foundry project "
            "connection to your Azure AI Search service and paste its connection id."
        )

    base_url = settings.policy_api_base_url
    if not base_url:
        raise SystemExit(
            "POLICY_API_BASE_URL is not set. Expose the FastAPI app on a public "
            "HTTPS URL (App Service) and put it in .env."
        )
    if not base_url.lower().startswith("https://"):
        raise SystemExit("POLICY_API_BASE_URL must be an HTTPS URL reachable by the Foundry service.")

    client = AIProjectClient(
        endpoint=settings.foundry_project_endpoint,
        credential=DefaultAzureCredential(),
    )

    body = {
        "definition": {
            "kind": "prompt",
            "model": settings.orchestrator_agent_model,
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
                },
                {
                    "type": "openapi",
                    "openapi": {
                        "name": "policy_data",
                        "description": (
                            "Look up auto-insurance policies, claims, and customers "
                            "from Azure Cosmos DB."
                        ),
                        "spec": _openapi_spec(base_url),
                        "auth": {"type": "anonymous"},
                    },
                },
            ],
        }
    }

    version = client.agents.create_version(
        agent_name=settings.orchestrator_agent_name,
        body=body,
        description="Router agent: Azure AI Search (knowledge) + Cosmos OpenAPI (policy data).",
    )
    print(f"Created {settings.orchestrator_agent_name} version: {version.id}")
    print(f"Search connection: {connection_id}")
    print(f"OpenAPI server: {base_url}")
    print(json.dumps(version.as_dict(), indent=2, default=str))


if __name__ == "__main__":
    main()
