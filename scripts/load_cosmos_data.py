"""Load policies, claims, and customers JSON into Azure Cosmos DB for NoSQL.

Creates the database and containers if they don't exist, then upserts every
record. Uses Microsoft Entra ID (DefaultAzureCredential) by default; set
COSMOS_KEY in the environment to use an account key instead.

Run:
    $env:PYTHONPATH = "src"
    python scripts/load_cosmos_data.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from azure.cosmos import CosmosClient, PartitionKey
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from insurance_rag_agent.config import get_settings  # noqa: E402

load_dotenv(override=False)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# container name -> (json file, partition key path, id field)
CONTAINERS = {
    "policies": ("policies.json", "/customerId", "policyId"),
    "claims": ("claims.json", "/policyId", "claimId"),
    "customers": ("customers.json", "/customerId", "customerId"),
}


def _client(settings) -> CosmosClient:
    if settings.cosmos_key:
        return CosmosClient(settings.cosmos_endpoint, credential=settings.cosmos_key)
    return CosmosClient(settings.cosmos_endpoint, credential=DefaultAzureCredential())


def main() -> None:
    settings = get_settings()
    if not settings.cosmos_endpoint:
        raise SystemExit("COSMOS_ENDPOINT is not set. Configure .env first.")

    client = _client(settings)
    print(f"Ensuring database '{settings.cosmos_database}' ...")
    db = client.create_database_if_not_exists(id=settings.cosmos_database)

    container_names = {
        "policies": settings.cosmos_policies_container,
        "claims": settings.cosmos_claims_container,
        "customers": settings.cosmos_customers_container,
    }

    for key, (filename, pk_path, id_field) in CONTAINERS.items():
        container_id = container_names[key]
        print(f"Ensuring container '{container_id}' (pk={pk_path}) ...")
        container = db.create_container_if_not_exists(
            id=container_id,
            partition_key=PartitionKey(path=pk_path),
        )

        data = json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))
        records = data if isinstance(data, list) else next(
            (v for v in data.values() if isinstance(v, list)), []
        )

        count = 0
        for record in records:
            # Cosmos requires a string 'id'. Use the natural key.
            record["id"] = str(record[id_field])
            container.upsert_item(record)
            count += 1
        print(f"  Upserted {count} records into '{container_id}'.")

    print("\nCosmos DB load complete.")


if __name__ == "__main__":
    main()
