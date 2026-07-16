"""Application configuration loaded from environment variables."""

from dataclasses import dataclass, field
import os

from dotenv import load_dotenv

# Load .env before the dataclass field defaults below are evaluated (they read
# os.getenv at class-definition time). Without this, importing config before
# calling load_dotenv() yields empty settings.
load_dotenv(override=False)


@dataclass
class Settings:
    app_env: str = os.getenv("APP_ENV", "dev")

    # --- Foundry ---
    foundry_project_endpoint: str = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
    foundry_chat_model: str = os.getenv("FOUNDRY_CHAT_MODEL", "gpt-5-mini")
    # The gpt-5 family supports only the code interpreter and file search tools in the
    # Foundry Agent Service, so the hosted KB agent (which uses the Azure AI Search tool)
    # must run on a tool-compatible model such as gpt-4.1-mini.
    foundry_kb_agent_model: str = os.getenv("FOUNDRY_KB_AGENT_MODEL", "gpt-4.1-mini")
    foundry_embedding_model: str = os.getenv("FOUNDRY_EMBEDDING_MODEL", "text-embedding-3-large")
    foundry_agent_name: str = os.getenv("FOUNDRY_AGENT_NAME", "insurance-rag-agent")
    kb_agent_name: str = os.getenv("KB_AGENT_NAME", "kb-search-agent")
    orchestrator_agent_name: str = os.getenv("ORCHESTRATOR_AGENT_NAME", "orchestrator-agent")
    # Router agent that holds both the Azure AI Search tool and the policy OpenAPI
    # tool and routes each question to the right one. Runs on a tool-compatible model.
    orchestrator_agent_model: str = os.getenv("ORCHESTRATOR_AGENT_MODEL", "gpt-4.1-mini")
    # Delegating orchestrator: a prompt agent whose OpenAPI tool calls back to this
    # app's /api/agents/{kb,policy} endpoints, which in turn INVOKE the two leaf
    # Foundry agents (true agent-to-agent delegation). This is the agent the chat UI
    # talks to via POST /api/chat. Runs on a tool-compatible model.
    insurance_orchestrator_agent_name: str = os.getenv(
        "INSURANCE_ORCHESTRATOR_AGENT_NAME", "insurance-orchestrator"
    )
    insurance_orchestrator_agent_model: str = os.getenv(
        "INSURANCE_ORCHESTRATOR_AGENT_MODEL", "gpt-4.1-mini"
    )
    # Hosted policy agent: queries Cosmos DB policy/claims data via an OpenAPI tool
    # that calls the /api/* endpoints of this app. Runs on a tool-compatible model.
    policy_agent_name: str = os.getenv("POLICY_AGENT_NAME", "policy-cosmos-agent")
    policy_agent_model: str = os.getenv("POLICY_AGENT_MODEL", "gpt-4.1-mini")
    # Foundry HOSTED (containerized, custom-code) agents that the chat UI demos.
    # The insurance-orchestrator's /api/agents/{kb,policy} endpoints invoke these.
    # - hosted KB agent: queries Azure AI Search directly (keyless via the project MI).
    # - hosted policy agent: calls this app's /api/* Cosmos endpoints over HTTP.
    hosted_kb_agent_name: str = os.getenv("HOSTED_KB_AGENT_NAME", "kb-hosted-agent")
    hosted_policy_agent_name: str = os.getenv("HOSTED_POLICY_AGENT_NAME", "policy-hosted-agent")
    # Public base URL where the FastAPI app is reachable by the Foundry service
    # (e.g. a dev tunnel or App Service URL). Must be HTTPS and internet-reachable.
    policy_api_base_url: str = os.getenv("POLICY_API_BASE_URL", "")

    # --- Azure AI Search (knowledge-base RAG) ---
    search_endpoint: str = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    search_index: str = os.getenv("AZURE_SEARCH_INDEX", "insurance-kb")
    search_api_key: str = os.getenv("AZURE_SEARCH_API_KEY", "")
    search_semantic_config: str = os.getenv("AZURE_SEARCH_SEMANTIC_CONFIG", "insurance-kb-semantic")
    search_vector_field: str = os.getenv("AZURE_SEARCH_VECTOR_FIELD", "contentVector")
    search_top_k: int = int(os.getenv("AZURE_SEARCH_TOP_K", "5"))

    # --- Azure Blob Storage (knowledge-base source documents for the Search indexer) ---
    # The Search indexer pulls documents from this container, chunks them, and
    # generates embeddings (integrated vectorization) before indexing.
    storage_blob_endpoint: str = os.getenv("AZURE_STORAGE_BLOB_ENDPOINT", "")
    storage_resource_id: str = os.getenv("AZURE_STORAGE_RESOURCE_ID", "")
    kb_container: str = os.getenv("AZURE_STORAGE_KB_CONTAINER", "kb-docs")

    # --- Azure AI Speech (voice input/output) ---
    # The browser Speech SDK does STT/TTS against the AI Services (Cognitive
    # Services) account. The backend mints a short-lived, keyless Entra token
    # for it (no keys shipped to the client). Region + resource ID identify the
    # multi-service account; leave empty to disable the voice UI.
    speech_region: str = os.getenv("SPEECH_REGION", "")
    speech_resource_id: str = os.getenv("SPEECH_RESOURCE_ID", "")
    speech_recognition_language: str = os.getenv("SPEECH_RECOGNITION_LANGUAGE", "en-US")
    speech_synthesis_voice: str = os.getenv("SPEECH_SYNTHESIS_VOICE", "en-US-JennyNeural")

    # --- Foundry Voice Live (real-time speech-to-speech agent) ---
    # A separate live-voice mode that streams audio to the Voice Live API in
    # agent mode, which drives the existing insurance-orchestrator Foundry agent
    # (STT + LLM/tool-calling + TTS, fully managed). The browser can't set a
    # WebSocket Authorization header, so the backend relays frames to Voice Live
    # and adds a keyless Entra bearer token (managed identity). This is additive
    # and does NOT affect the typed /api/chat ontology-router flow. Leave the
    # endpoint empty to disable the live-voice mode.
    voicelive_endpoint: str = os.getenv("VOICELIVE_ENDPOINT", "")
    voicelive_api_version: str = os.getenv("VOICELIVE_API_VERSION", "2026-04-10")
    voicelive_project_name: str = os.getenv("VOICELIVE_PROJECT_NAME", "")
    # Which Foundry agent the voice session talks to (defaults to the delegating
    # orchestrator so voice reaches the same KB + policy tools as typed chat).
    voicelive_agent_name: str = os.getenv(
        "VOICELIVE_AGENT_NAME", os.getenv("INSURANCE_ORCHESTRATOR_AGENT_NAME", "insurance-orchestrator")
    )
    # Standard Azure neural voice (DragonHD voices aren't available in every
    # region, so default to a broadly-available standard voice).
    voicelive_voice: str = os.getenv("VOICELIVE_VOICE", "en-US-AvaNeural")

    # --- Azure OpenAI / embeddings ---
    azure_openai_endpoint: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    embedding_deployment: str = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    embedding_dimensions: int = int(os.getenv("AZURE_OPENAI_EMBEDDING_DIMENSIONS", "3072"))

    # --- Azure Cosmos DB (policy & claims RAG) ---
    cosmos_endpoint: str = os.getenv("COSMOS_ENDPOINT", "")
    cosmos_key: str = os.getenv("COSMOS_KEY", "")
    cosmos_database: str = os.getenv("COSMOS_DATABASE", "insurance")
    cosmos_policies_container: str = os.getenv("COSMOS_POLICIES_CONTAINER", "policies")
    cosmos_claims_container: str = os.getenv("COSMOS_CLAIMS_CONTAINER", "claims")
    cosmos_customers_container: str = os.getenv("COSMOS_CUSTOMERS_CONTAINER", "customers")

    # --- Knowledge-base source docs ---
    kb_docs: list[str] = field(
        default_factory=lambda: [
            p.strip()
            for p in os.getenv(
                "KB_DOCS",
                "data/auto_insurance_knowledge_base.docx,data/auto_insurance_glossary.md",
            ).split(",")
            if p.strip()
        ]
    )


def get_settings() -> Settings:
    return Settings()
