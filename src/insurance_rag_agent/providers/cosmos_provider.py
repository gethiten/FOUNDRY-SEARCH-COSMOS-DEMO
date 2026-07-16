"""Azure Cosmos DB provider — policy, claims, and customer retrieval (RAG).

Uses the Cosmos DB for NoSQL SDK. Authentication prefers Microsoft Entra ID
(DefaultAzureCredential); falls back to an account key if COSMOS_KEY is set.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

from insurance_rag_agent.config import Settings

logger = logging.getLogger("insurance_rag.cosmos")


class CosmosPolicyProvider:
    """Read-side data provider backed by Cosmos DB for NoSQL."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        if settings.cosmos_key:
            self._client = CosmosClient(settings.cosmos_endpoint, credential=settings.cosmos_key)
        else:
            self._client = CosmosClient(settings.cosmos_endpoint, credential=DefaultAzureCredential())
        db = self._client.get_database_client(settings.cosmos_database)
        self._policies = db.get_container_client(settings.cosmos_policies_container)
        self._claims = db.get_container_client(settings.cosmos_claims_container)
        self._customers = db.get_container_client(settings.cosmos_customers_container)

    # -- internal query helper -------------------------------------------------
    @staticmethod
    def _query(container, sql: str, params: list[dict] | None = None) -> list[dict]:
        # azure-cosmos>=4.9 removed the ``enable_cross_partition_query`` kwarg;
        # cross-partition queries are enabled by default.
        items = container.query_items(
            query=sql,
            parameters=params or [],
        )
        return list(items)

    # -- policies --------------------------------------------------------------
    def get_policy(self, policy_id: str) -> dict | None:
        pid = policy_id.strip().upper()
        rows = self._query(
            self._policies,
            "SELECT * FROM c WHERE UPPER(c.policyId) = @id OR UPPER(c.policyNumber) = @id",
            [{"name": "@id", "value": pid}],
        )
        if not rows:
            return None
        policy = rows[0]
        cust = self.get_customer(policy.get("customerId", ""))
        policy["customerName"] = cust.get("fullName", "N/A") if cust else "N/A"
        return policy

    def list_policies(self, status: str | None = None, agency: str | None = None) -> list[dict]:
        clauses, params = [], []
        if status:
            clauses.append("LOWER(c.status) = @status")
            params.append({"name": "@status", "value": status.lower()})
        if agency:
            clauses.append("CONTAINS(LOWER(c.agency), @agency)")
            params.append({"name": "@agency", "value": agency.lower()})
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return self._query(self._policies, f"SELECT * FROM c{where}", params)

    def search_policies_by_name(self, name: str) -> list[dict]:
        customers = self.search_customers_by_name(name)
        ids = [c["customerId"] for c in customers]
        if not ids:
            return []
        placeholders = ", ".join(f"@id{i}" for i in range(len(ids)))
        params = [{"name": f"@id{i}", "value": cid} for i, cid in enumerate(ids)]
        return self._query(
            self._policies,
            f"SELECT * FROM c WHERE c.customerId IN ({placeholders})",
            params,
        )

    # -- customers -------------------------------------------------------------
    def get_customer(self, customer_id: str) -> dict | None:
        if not customer_id:
            return None
        rows = self._query(
            self._customers,
            "SELECT * FROM c WHERE c.customerId = @id",
            [{"name": "@id", "value": customer_id}],
        )
        return rows[0] if rows else None

    def search_customers_by_name(self, name: str) -> list[dict]:
        return self._query(
            self._customers,
            "SELECT * FROM c WHERE CONTAINS(LOWER(c.fullName), @name)",
            [{"name": "@name", "value": name.lower()}],
        )

    # -- claims ----------------------------------------------------------------
    def get_claims(
        self,
        claim_id: str | None = None,
        policy_id: str | None = None,
        customer_id: str | None = None,
    ) -> list[dict]:
        if claim_id:
            cid = claim_id.strip().upper()
            return self._query(
                self._claims,
                "SELECT * FROM c WHERE UPPER(c.claimId) = @id OR UPPER(c.claimNumber) = @id",
                [{"name": "@id", "value": cid}],
            )
        clauses, params = [], []
        if policy_id:
            # accept either policyId or policyNumber
            policy = self.get_policy(policy_id)
            resolved = policy.get("policyId") if policy else policy_id
            clauses.append("c.policyId = @pid")
            params.append({"name": "@pid", "value": resolved})
        if customer_id:
            clauses.append("c.customerId = @cid")
            params.append({"name": "@cid", "value": customer_id})
        where = (" WHERE " + " OR ".join(clauses)) if clauses else ""
        return self._query(self._claims, f"SELECT * FROM c{where}", params)

    # -- coverage summary ------------------------------------------------------
    def coverage_summary(self) -> dict:
        policies = self._query(self._policies, "SELECT * FROM c")
        claims = self._query(self._claims, "SELECT VALUE COUNT(1) FROM c")
        customers = self._query(self._customers, "SELECT VALUE COUNT(1) FROM c")
        total_vehicles = 0
        total_premium = 0
        coverage = {"liability": 0, "collision": 0, "comprehensive": 0, "roadside": 0, "rental": 0}
        status_counts: dict[str, int] = {}
        for p in policies:
            status_counts[p.get("status", "Unknown")] = status_counts.get(p.get("status", "Unknown"), 0) + 1
            prem = p.get("premium", {})
            if isinstance(prem, dict):
                total_premium += prem.get("writtenPremium", 0) or 0
            for v in p.get("vehicles", []):
                total_vehicles += 1
                cov = v.get("coverages", {})
                if cov.get("liability"):
                    coverage["liability"] += 1
                if cov.get("collision"):
                    coverage["collision"] += 1
                if cov.get("comprehensive"):
                    coverage["comprehensive"] += 1
                if cov.get("roadsideAssistance"):
                    coverage["roadside"] += 1
                if cov.get("rentalReimbursement"):
                    coverage["rental"] += 1
        return {
            "totalPolicies": len(policies),
            "totalVehicles": total_vehicles,
            "totalCustomers": customers[0] if customers else 0,
            "totalClaims": claims[0] if claims else 0,
            "totalWrittenPremium": f"${total_premium:,.0f}",
            "statusBreakdown": status_counts,
            "coverageStats": coverage,
        }


@lru_cache(maxsize=1)
def get_cosmos_provider() -> CosmosPolicyProvider:
    from insurance_rag_agent.config import get_settings

    return CosmosPolicyProvider(get_settings())
