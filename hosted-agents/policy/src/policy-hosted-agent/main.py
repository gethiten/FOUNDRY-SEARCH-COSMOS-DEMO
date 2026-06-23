# Copyright (c) Microsoft. All rights reserved.
"""Hosted Policy-Data agent.

Mirrors the prompt `policy-cosmos-agent`: answers questions about specific
policies, customers, claims and coverage by calling the insurance backend
REST API (Cosmos DB behind FastAPI on App Service).
"""

import json
import os

import httpx
from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from pydantic import Field
from typing_extensions import Annotated

# Load environment variables from .env file
load_dotenv()

_API_BASE_URL = os.environ.get("POLICY_API_BASE_URL", "").rstrip("/")
_credential = DefaultAzureCredential()


def _get(path: str, params: dict | None = None) -> str:
    """Call the backend API and return the JSON body as text."""
    url = f"{_API_BASE_URL}{path}"
    try:
        resp = httpx.get(url, params=params or {}, timeout=30.0)
    except Exception as exc:  # network failure
        return f"Error calling backend API: {exc}"
    if resp.status_code == 404:
        return json.dumps({"error": "not_found", "detail": resp.text})
    if resp.status_code >= 400:
        return json.dumps({"error": f"http_{resp.status_code}", "detail": resp.text})
    return resp.text


@tool(approval_mode="never_require")
def get_policy(
    policy_id: Annotated[str, Field(description="Policy ID or policy number, e.g. POL-001 or AU-72177252.")],
) -> str:
    """Look up a single policy (vehicles, coverages, premium) by ID or number."""
    return _get(f"/api/policies/{policy_id}")


@tool(approval_mode="never_require")
def search_policies_by_name(
    name: Annotated[str, Field(description="Full or partial customer name.")],
) -> str:
    """Find policies belonging to a customer by full or partial name."""
    return _get("/api/policies/search", {"name": name})


@tool(approval_mode="never_require")
def list_policies(
    status: Annotated[str, Field(description="Optional status filter: Active, Expired, or Cancelled.")] = "",
    agency: Annotated[str, Field(description="Optional agency name filter.")] = "",
) -> str:
    """List policies, optionally filtered by status or agency."""
    params = {}
    if status:
        params["status"] = status
    if agency:
        params["agency"] = agency
    return _get("/api/policies", params)


@tool(approval_mode="never_require")
def get_customer(
    customer_id: Annotated[str, Field(description="Customer ID, e.g. CUST-051.")],
) -> str:
    """Look up a single customer by customer ID."""
    return _get(f"/api/customers/{customer_id}")


@tool(approval_mode="never_require")
def search_customers_by_name(
    name: Annotated[str, Field(description="Full or partial customer name.")],
) -> str:
    """Find customers by full or partial name."""
    return _get("/api/customers/search", {"name": name})


@tool(approval_mode="never_require")
def get_claims(
    claim_id: Annotated[str, Field(description="Optional claim ID.")] = "",
    policy_id: Annotated[str, Field(description="Optional policy ID to list its claims.")] = "",
    customer_id: Annotated[str, Field(description="Optional customer ID to list their claims.")] = "",
) -> str:
    """Look up claims by claim ID, policy ID, or customer ID."""
    params = {}
    if claim_id:
        params["claim_id"] = claim_id
    if policy_id:
        params["policy_id"] = policy_id
    if customer_id:
        params["customer_id"] = customer_id
    return _get("/api/claims", params)


@tool(approval_mode="never_require")
def get_coverage_summary() -> str:
    """Aggregate coverage statistics across all policies."""
    return _get("/api/coverage-summary")


INSTRUCTIONS = (
    "You are the Insurance Policy-Data agent. You answer questions about "
    "specific policies, customers, claims, and coverage by calling the backend "
    "data tools.\n\n"
    "RULES:\n"
    "- ALWAYS call the appropriate tool to fetch real data before answering; "
    "never invent policy numbers, names, deductibles, or claim details.\n"
    "- Resolve names to IDs when needed (search_policies_by_name / "
    "search_customers_by_name) before fetching details.\n"
    "- If a record is not found, say so plainly.\n"
    "- For general / educational insurance questions (definitions, how coverage "
    "works), explain you only handle specific policy data and defer to the "
    "knowledge-base agent.\n"
    "- Be concise and cite the concrete data you retrieved."
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
        tools=[
            get_policy,
            search_policies_by_name,
            list_policies,
            get_customer,
            search_customers_by_name,
            get_claims,
            get_coverage_summary,
        ],
        # History is managed by the hosting infrastructure; no server-side store.
        default_options={"store": False},
    )

    server = ResponsesHostServer(agent)
    server.run()


if __name__ == "__main__":
    main()
