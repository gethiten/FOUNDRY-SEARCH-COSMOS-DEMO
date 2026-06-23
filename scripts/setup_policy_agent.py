"""Register a Foundry-hosted Policy agent backed by Cosmos DB via an OpenAPI tool.

Unlike the knowledge-base agent (which uses the native Azure AI Search tool),
Foundry has no native "Cosmos DB" knowledge tool. So this hosted agent reaches
Cosmos DB through an **OpenAPI tool**: the agent calls the ``/api/*`` policy /
claims / customer endpoints exposed by this project's FastAPI app, which query
Cosmos DB directly ("structured Cosmos DB RAG").

Because the agent runs inside the Foundry service, the API must be reachable on
a public HTTPS URL. Set POLICY_API_BASE_URL to that URL (a dev tunnel over the
local uvicorn app, or an App Service deployment).

    # expose the local app (separate terminal), then copy the https URL:
    #   winget install Microsoft.devtunnel   # if needed
    #   devtunnel user login
    #   devtunnel host -p 8000 --allow-anonymous
    # set POLICY_API_BASE_URL=https://<tunnel-id>-8000.<region>.devtunnels.ms

Run:
    $env:PYTHONPATH = "src"
    python scripts/setup_policy_agent.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from insurance_rag_agent.config import get_settings  # noqa: E402

load_dotenv(override=False)

INSTRUCTIONS = (
    "You are the Auto-Insurance Policy agent. You answer questions about specific "
    "policies, claims, and customers by calling the policy data API (backed by "
    "Azure Cosmos DB).\n\n"
    "RULES:\n"
    "- ALWAYS call a tool to retrieve real data before answering; never invent "
    "policy IDs, names, amounts, deductibles, or claim details.\n"
    "- Pick the right operation: getPolicy for a policy ID (e.g. POL-001), "
    "searchPoliciesByName for a customer name, getClaims for claims, getCustomer "
    "for a customer ID, listPolicies for filtered lists, getCoverageSummary for "
    "portfolio totals.\n"
    "- If a required identifier is missing, ask the user for it.\n"
    "- For general/educational insurance questions (what is a deductible, how does "
    "comprehensive coverage work), say you only handle policy data and defer to the "
    "knowledge-base agent.\n"
    "- Summarize results clearly; show key fields (policy number, status, vehicles, "
    "coverages, premium, deductibles)."
)


def _openapi_spec(base_url: str) -> dict:
    """Hand-authored OpenAPI 3.0 spec describing the /api/* Cosmos endpoints.

    Kept explicit (rather than scraping FastAPI) so operationIds and the public
    server URL are exactly what the Foundry OpenAPI tool needs.
    """
    obj = {"type": "object"}
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Insurance Policy Data API",
            "version": "1.0.0",
            "description": "Read-only policy, claims, and customer lookups backed by Azure Cosmos DB.",
        },
        "servers": [{"url": base_url.rstrip("/")}],
        "paths": {
            "/api/policies/{policy_id}": {
                "get": {
                    "operationId": "getPolicy",
                    "summary": "Look up a single policy by policy ID or policy number.",
                    "parameters": [
                        {
                            "name": "policy_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Policy identifier such as POL-001 or a policy number like AU-72177252.",
                        }
                    ],
                    "responses": {"200": {"description": "The policy record.", "content": {"application/json": {"schema": obj}}}},
                }
            },
            "/api/policies": {
                "get": {
                    "operationId": "listPolicies",
                    "summary": "List policies, optionally filtered by status or agency.",
                    "parameters": [
                        {"name": "status", "in": "query", "required": False, "schema": {"type": "string"}, "description": "Active, Expired, or Cancelled."},
                        {"name": "agency", "in": "query", "required": False, "schema": {"type": "string"}, "description": "Partial agency-name filter."},
                    ],
                    "responses": {"200": {"description": "Matching policies.", "content": {"application/json": {"schema": obj}}}},
                }
            },
            "/api/policies/search": {
                "get": {
                    "operationId": "searchPoliciesByName",
                    "summary": "Find policies belonging to a customer by name.",
                    "parameters": [
                        {"name": "name", "in": "query", "required": True, "schema": {"type": "string"}, "description": "Full or partial customer name."}
                    ],
                    "responses": {"200": {"description": "Matching policies.", "content": {"application/json": {"schema": obj}}}},
                }
            },
            "/api/customers/{customer_id}": {
                "get": {
                    "operationId": "getCustomer",
                    "summary": "Look up a single customer by customer ID.",
                    "parameters": [
                        {"name": "customer_id", "in": "path", "required": True, "schema": {"type": "string"}, "description": "Customer identifier such as CUST-001."}
                    ],
                    "responses": {"200": {"description": "The customer record.", "content": {"application/json": {"schema": obj}}}},
                }
            },
            "/api/customers/search": {
                "get": {
                    "operationId": "searchCustomers",
                    "summary": "Find customers by name.",
                    "parameters": [
                        {"name": "name", "in": "query", "required": True, "schema": {"type": "string"}, "description": "Full or partial customer name."}
                    ],
                    "responses": {"200": {"description": "Matching customers.", "content": {"application/json": {"schema": obj}}}},
                }
            },
            "/api/claims": {
                "get": {
                    "operationId": "getClaims",
                    "summary": "Look up claims by claim ID, policy ID, or customer ID.",
                    "parameters": [
                        {"name": "claim_id", "in": "query", "required": False, "schema": {"type": "string"}, "description": "Claim identifier such as CLM-001 or CL-2026-1001."},
                        {"name": "policy_id", "in": "query", "required": False, "schema": {"type": "string"}, "description": "Policy ID to find claims for."},
                        {"name": "customer_id", "in": "query", "required": False, "schema": {"type": "string"}, "description": "Customer ID to find claims for."},
                    ],
                    "responses": {"200": {"description": "Matching claims.", "content": {"application/json": {"schema": obj}}}},
                }
            },
            "/api/coverage-summary": {
                "get": {
                    "operationId": "getCoverageSummary",
                    "summary": "Aggregate coverage statistics across all policies.",
                    "responses": {"200": {"description": "Coverage totals.", "content": {"application/json": {"schema": obj}}}},
                }
            },
        },
    }


def main() -> None:
    settings = get_settings()
    base_url = settings.policy_api_base_url
    if not base_url:
        raise SystemExit(
            "POLICY_API_BASE_URL is not set. Expose the FastAPI app on a public HTTPS "
            "URL (dev tunnel or App Service) and put it in .env. See this script's docstring."
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
            "model": settings.policy_agent_model,
            "instructions": INSTRUCTIONS,
            "tools": [
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
                }
            ],
        }
    }

    version = client.agents.create_version(
        agent_name=settings.policy_agent_name,
        body=body,
        description="Policy agent backed by Cosmos DB via an OpenAPI tool.",
    )
    print(f"Created {settings.policy_agent_name} version: {version.id}")
    print(f"OpenAPI server: {base_url}")
    print(json.dumps(version.as_dict(), indent=2, default=str))


if __name__ == "__main__":
    main()
