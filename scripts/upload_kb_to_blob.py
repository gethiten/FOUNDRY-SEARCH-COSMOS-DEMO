"""Upload knowledge-base source documents to Azure Blob Storage.

The Azure AI Search indexer (see ``setup_search_index.py``) pulls these blobs,
chunks them, generates embeddings via integrated vectorization, and projects the
chunks into the search index. This script only needs to run when the source
documents change.

Auth is keyless: it uses ``DefaultAzureCredential``, so the signed-in identity
needs the ``Storage Blob Data Contributor`` role on the storage account.

Usage (from the repo root, with the venv active):
    $env:PYTHONPATH = "src"
    python scripts/upload_kb_to_blob.py
"""

from __future__ import annotations

import mimetypes
import os
import sys

from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from insurance_rag_agent.config import get_settings


def main() -> int:
    settings = get_settings()

    if not settings.storage_blob_endpoint:
        print("ERROR: AZURE_STORAGE_BLOB_ENDPOINT is not set in your environment/.env.")
        return 1

    docs = [d for d in settings.kb_docs if d]
    if not docs:
        print("ERROR: No KB documents configured (KB_DOCS).")
        return 1

    credential = DefaultAzureCredential()
    service = BlobServiceClient(account_url=settings.storage_blob_endpoint, credential=credential)
    container = service.get_container_client(settings.kb_container)

    try:
        container.create_container()
        print(f"Created container '{settings.kb_container}'.")
    except ResourceExistsError:
        pass

    uploaded = 0
    for path in docs:
        if not os.path.isfile(path):
            print(f"WARNING: skipping missing file: {path}")
            continue

        blob_name = os.path.basename(path)
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            container.upload_blob(
                name=blob_name,
                data=fh,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type),
            )
        print(f"Uploaded {path} -> {settings.kb_container}/{blob_name} ({content_type})")
        uploaded += 1

    print(f"\nDone. Uploaded {uploaded} document(s) to {settings.storage_blob_endpoint}{settings.kb_container}")
    print("Next: run scripts/setup_search_index.py to (re)build the indexer and index.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
