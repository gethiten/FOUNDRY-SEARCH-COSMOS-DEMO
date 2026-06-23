"""Provision the Azure AI Search pull-based RAG pipeline for the knowledge base.

Instead of chunking + embedding documents in Python and pushing them, this
script wires up Azure AI Search to read the source documents straight from
Azure Blob Storage and do everything itself (integrated vectorization):

  1. Index            — chunk-level index (one doc per chunk) with a vector
                        field + AzureOpenAIVectorizer + semantic config.
  2. Data source      — keyless blob connection (ResourceId) to the kb-docs
                        container; the Search managed identity reads the blobs.
  3. Skillset         — SplitSkill (chunking) -> AzureOpenAIEmbeddingSkill
                        (embeddings) -> index projections (one row per chunk).
  4. Indexer          — runs the skillset over the blobs and populates the
                        index. Re-run it (or schedule it) whenever blobs change.

Upload the source documents first with ``scripts/upload_kb_to_blob.py``.

Auth uses Microsoft Entra ID (DefaultAzureCredential) for the Search control
plane. The Search service's own managed identity is used (keyless) to read
blobs and call Azure OpenAI for embeddings. Set AZURE_SEARCH_API_KEY to use a
key for the Search control plane instead.

Run:
    $env:PYTHONPATH = "src"
    python scripts/setup_search_index.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient
from azure.search.documents.indexes.models import (
    AzureOpenAIEmbeddingSkill,
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    HnswAlgorithmConfiguration,
    IndexProjectionMode,
    InputFieldMappingEntry,
    OutputFieldMappingEntry,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchIndexer,
    SearchIndexerDataContainer,
    SearchIndexerDataSourceConnection,
    SearchIndexerIndexProjection,
    SearchIndexerIndexProjectionSelector,
    SearchIndexerIndexProjectionsParameters,
    SearchIndexerSkillset,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    SplitSkill,
    VectorSearch,
    VectorSearchProfile,
)
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from insurance_rag_agent.config import get_settings  # noqa: E402

load_dotenv(override=False)

CHUNK_CHARS = 1800
CHUNK_OVERLAP = 200

SETTINGS = get_settings()
INDEX_NAME = SETTINGS.search_index
DATA_SOURCE_NAME = f"{INDEX_NAME}-blob-ds"
SKILLSET_NAME = f"{INDEX_NAME}-skillset"
INDEXER_NAME = f"{INDEX_NAME}-indexer"
SEMANTIC_CONFIG = SETTINGS.search_semantic_config
VECTOR_FIELD = SETTINGS.search_vector_field
VECTOR_PROFILE = "kb-hnsw-profile"
VECTOR_ALGO = "kb-hnsw"
VECTORIZER_NAME = "kb-openai-vectorizer"


def _credential():
    if SETTINGS.search_api_key:
        return AzureKeyCredential(SETTINGS.search_api_key)
    return DefaultAzureCredential()


# ---------------------------------------------------------------------------
# 1. Index (chunk-level)
# ---------------------------------------------------------------------------
def build_index(client: SearchIndexClient) -> None:
    fields = [
        # Key per chunk. Index projections require the key field to use the
        # 'keyword' analyzer.
        SearchField(
            name="id",
            type=SearchFieldDataType.String,
            key=True,
            analyzer_name="keyword",
        ),
        # Parent (blob) key — filterable, required by index projections.
        SimpleField(
            name="parent_id",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(
            name="source",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
        ),
        SearchField(
            name=VECTOR_FIELD,
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=SETTINGS.embedding_dimensions,
            vector_search_profile_name=VECTOR_PROFILE,
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name=VECTOR_ALGO)],
        profiles=[
            VectorSearchProfile(
                name=VECTOR_PROFILE,
                algorithm_configuration_name=VECTOR_ALGO,
                vectorizer_name=VECTORIZER_NAME,
            )
        ],
        vectorizers=[
            AzureOpenAIVectorizer(
                vectorizer_name=VECTORIZER_NAME,
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=SETTINGS.azure_openai_endpoint,
                    deployment_name=SETTINGS.embedding_deployment,
                    model_name=SETTINGS.embedding_deployment,
                ),
            )
        ],
    )

    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name=SEMANTIC_CONFIG,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                ),
            )
        ]
    )

    index = SearchIndex(
        name=INDEX_NAME,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )
    client.create_or_update_index(index)
    print(f"[index]      '{INDEX_NAME}' created/updated.")


# ---------------------------------------------------------------------------
# 2. Data source (keyless blob)
# ---------------------------------------------------------------------------
def build_data_source(client: SearchIndexerClient) -> None:
    if not SETTINGS.storage_resource_id:
        raise SystemExit(
            "AZURE_STORAGE_RESOURCE_ID is not set. Deploy the storage account "
            "and copy its resource id into .env."
        )
    # Keyless: the Search service's managed identity authenticates to storage
    # (needs 'Storage Blob Data Reader'). The ResourceId form selects MI auth.
    connection_string = f"ResourceId={SETTINGS.storage_resource_id};"
    data_source = SearchIndexerDataSourceConnection(
        name=DATA_SOURCE_NAME,
        type="azureblob",
        connection_string=connection_string,
        container=SearchIndexerDataContainer(name=SETTINGS.kb_container),
    )
    client.create_or_update_data_source_connection(data_source)
    print(f"[datasource] '{DATA_SOURCE_NAME}' -> container '{SETTINGS.kb_container}' (keyless).")


# ---------------------------------------------------------------------------
# 3. Skillset (split -> embed -> project chunks)
# ---------------------------------------------------------------------------
def build_skillset(client: SearchIndexerClient) -> None:
    split_skill = SplitSkill(
        name="split-into-chunks",
        text_split_mode="pages",
        maximum_page_length=CHUNK_CHARS,
        page_overlap_length=CHUNK_OVERLAP,
        context="/document",
        inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
        outputs=[OutputFieldMappingEntry(name="textItems", target_name="pages")],
    )

    embedding_skill = AzureOpenAIEmbeddingSkill(
        name="embed-chunks",
        context="/document/pages/*",
        resource_url=SETTINGS.azure_openai_endpoint,
        deployment_name=SETTINGS.embedding_deployment,
        model_name=SETTINGS.embedding_deployment,
        dimensions=SETTINGS.embedding_dimensions,
        inputs=[InputFieldMappingEntry(name="text", source="/document/pages/*")],
        outputs=[OutputFieldMappingEntry(name="embedding", target_name="text_vector")],
    )

    index_projection = SearchIndexerIndexProjection(
        selectors=[
            SearchIndexerIndexProjectionSelector(
                target_index_name=INDEX_NAME,
                parent_key_field_name="parent_id",
                source_context="/document/pages/*",
                mappings=[
                    InputFieldMappingEntry(name="content", source="/document/pages/*"),
                    InputFieldMappingEntry(
                        name=VECTOR_FIELD, source="/document/pages/*/text_vector"
                    ),
                    InputFieldMappingEntry(
                        name="title", source="/document/metadata_storage_name"
                    ),
                    InputFieldMappingEntry(
                        name="source", source="/document/metadata_storage_name"
                    ),
                ],
            )
        ],
        parameters=SearchIndexerIndexProjectionsParameters(
            projection_mode=IndexProjectionMode.SKIP_INDEXING_PARENT_DOCUMENTS
        ),
    )

    skillset = SearchIndexerSkillset(
        name=SKILLSET_NAME,
        description="Chunk KB docs and generate embeddings (integrated vectorization).",
        skills=[split_skill, embedding_skill],
        index_projection=index_projection,
    )
    client.create_or_update_skillset(skillset)
    print(f"[skillset]   '{SKILLSET_NAME}' created/updated.")


# ---------------------------------------------------------------------------
# 4. Indexer
# ---------------------------------------------------------------------------
def build_indexer(client: SearchIndexerClient) -> None:
    indexer = SearchIndexer(
        name=INDEXER_NAME,
        data_source_name=DATA_SOURCE_NAME,
        target_index_name=INDEX_NAME,
        skillset_name=SKILLSET_NAME,
    )
    client.create_or_update_indexer(indexer)
    print(f"[indexer]    '{INDEXER_NAME}' created/updated. Running...")
    client.run_indexer(INDEXER_NAME)


def wait_for_indexer(client: SearchIndexerClient, timeout_s: int = 600) -> None:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        status = client.get_indexer_status(INDEXER_NAME)
        last_result = status.last_result
        state = last_result.status if last_result else status.status
        if state != last:
            print(f"[indexer]    status: {state}")
            last = state
        if last_result and last_result.status in ("success", "transientFailure"):
            if last_result.item_count is not None:
                print(
                    f"[indexer]    processed items: {last_result.item_count}, "
                    f"failed: {last_result.failed_item_count}"
                )
            if last_result.errors:
                print("[indexer]    errors:")
                for err in last_result.errors[:5]:
                    print(f"             - {err.error_message}")
            return
        time.sleep(10)
    print("[indexer]    timed out waiting for completion; check the portal.")


def main() -> int:
    if not SETTINGS.search_endpoint:
        print("ERROR: AZURE_SEARCH_ENDPOINT is not set.")
        return 1

    cred = _credential()
    index_client = SearchIndexClient(endpoint=SETTINGS.search_endpoint, credential=cred)
    indexer_client = SearchIndexerClient(endpoint=SETTINGS.search_endpoint, credential=cred)

    build_index(index_client)
    build_data_source(indexer_client)
    build_skillset(indexer_client)
    build_indexer(indexer_client)
    wait_for_indexer(indexer_client)

    count = index_client.get_search_client(INDEX_NAME).get_document_count()
    print(f"\nDone. Index '{INDEX_NAME}' now holds {count} chunk(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
