"""Quick local smoke test for both RAG sources (no Foundry required).

Verifies:
  * Cosmos DB policy/claims retrieval
  * Azure AI Search knowledge-base retrieval

Run:
    $env:PYTHONPATH = "src"
    python scripts/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from insurance_rag_agent.providers.cosmos_provider import get_cosmos_provider  # noqa: E402
from insurance_rag_agent.providers.search_provider import get_search_provider  # noqa: E402

load_dotenv(override=False)


def main() -> None:
    print("== Cosmos DB ==")
    cosmos = get_cosmos_provider()
    policy = cosmos.get_policy("POL-001")
    print("POL-001:", policy.get("policyNumber") if policy else "NOT FOUND",
          "->", policy.get("customerName") if policy else "")
    claims = cosmos.get_claims(policy_id="POL-001")
    print(f"Claims for POL-001: {len(claims)}")
    print("Coverage summary:", cosmos.coverage_summary())

    print("\n== Azure AI Search ==")
    search = get_search_provider()
    hits = search.search("What is a deductible and how does it work?")
    for h in hits:
        print(f"- [{h['source']}] score={h['score']}: {h['content'][:80]}...")


if __name__ == "__main__":
    main()
