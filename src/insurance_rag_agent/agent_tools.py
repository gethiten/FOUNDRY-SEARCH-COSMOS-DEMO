"""Function tools exposed to the Foundry agent for agentic RAG.

The LLM decides which tool to call:
  * Knowledge / conceptual questions  -> search_knowledge_base (Azure AI Search)
  * Policy / claims / customer data    -> the Cosmos DB lookup tools
"""

from __future__ import annotations

import contextvars
import json
from typing import Annotated

from insurance_rag_agent.providers.cosmos_provider import get_cosmos_provider
from insurance_rag_agent.providers.search_provider import get_search_provider

# Per-request record of knowledge-base sources used by search_knowledge_base.
# Reset at the start of each request and read back to populate the API response.
_kb_sources: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "kb_sources", default=None
)


def reset_kb_sources() -> None:
    _kb_sources.set([])


def get_kb_sources() -> list[dict]:
    return _kb_sources.get() or []


def search_knowledge_base(
    query: Annotated[str, "The auto-insurance concept or question to look up in the knowledge base."],
) -> str:
    """Search the auto-insurance knowledge base (Azure AI Search) for conceptual,
    educational, or 'how does X work' questions. Returns grounded passages."""
    hits = get_search_provider().search(query)
    recorded = _kb_sources.get()
    if recorded is not None:
        for h in hits:
            recorded.append(
                {
                    "source": h.get("source"),
                    "title": h.get("title"),
                    "snippet": (h.get("content") or "")[:240],
                    "score": h.get("score"),
                }
            )
    if not hits:
        return "No relevant knowledge-base content found."
    blocks = []
    for h in hits:
        label = h.get("title") or h.get("source") or "KB"
        blocks.append(f"[{label}]\n{(h.get('content') or '').strip()}")
    return "\n\n---\n\n".join(blocks)



def lookup_policy(
    policy_id: Annotated[str, "Policy identifier such as POL-001 or a policy number like AU-72177252."],
) -> str:
    """Look up a single auto-insurance policy (vehicles, coverages, premium) from Cosmos DB."""
    policy = get_cosmos_provider().get_policy(policy_id)
    if not policy:
        return json.dumps({"error": "Policy not found", "policy_id": policy_id})
    return json.dumps(policy, default=str)


def list_policies(
    status: Annotated[str, "Optional status filter: Active, Expired, or Cancelled."] = "",
    agency: Annotated[str, "Optional agency-name filter (partial match)."] = "",
) -> str:
    """List auto-insurance policies from Cosmos DB, optionally filtered by status or agency."""
    rows = get_cosmos_provider().list_policies(status or None, agency or None)
    return json.dumps({"count": len(rows), "policies": rows}, default=str)


def search_policies_by_name(
    name: Annotated[str, "Full or partial customer name."],
) -> str:
    """Find policies belonging to a customer by name (Cosmos DB)."""
    rows = get_cosmos_provider().search_policies_by_name(name)
    return json.dumps({"count": len(rows), "policies": rows}, default=str)


def lookup_customer(
    customer_id: Annotated[str, "Customer identifier such as CUST-001."] = "",
    name: Annotated[str, "Customer name to search for."] = "",
) -> str:
    """Look up customer details by ID or name (Cosmos DB)."""
    provider = get_cosmos_provider()
    if customer_id:
        cust = provider.get_customer(customer_id)
        return json.dumps(cust or {"error": "Customer not found", "customer_id": customer_id}, default=str)
    if name:
        rows = provider.search_customers_by_name(name)
        return json.dumps({"count": len(rows), "customers": rows}, default=str)
    return json.dumps({"error": "customer_id or name is required"})


def lookup_claims(
    claim_id: Annotated[str, "Claim identifier such as CLM-001 or claim number CL-2026-1001."] = "",
    policy_id: Annotated[str, "Policy ID to find claims for."] = "",
    customer_id: Annotated[str, "Customer ID to find claims for."] = "",
) -> str:
    """Look up claims from Cosmos DB by claim ID, policy ID, or customer ID."""
    rows = get_cosmos_provider().get_claims(claim_id or None, policy_id or None, customer_id or None)
    return json.dumps({"count": len(rows), "claims": rows}, default=str)


def get_coverage_summary() -> str:
    """Get aggregate coverage statistics across all policies (Cosmos DB)."""
    return json.dumps(get_cosmos_provider().coverage_summary(), default=str)


# Tools the Foundry agent can call. Order is for readability only.
AGENT_TOOLS = [
    search_knowledge_base,
    lookup_policy,
    list_policies,
    search_policies_by_name,
    lookup_customer,
    lookup_claims,
    get_coverage_summary,
]
