"""Insurance Agentic-RAG API.

A single Foundry agent (gpt-5-mini) performs agentic RAG over two grounded
data sources via function tools:

  * Azure AI Search  -> knowledge-base / conceptual questions
  * Azure Cosmos DB  -> policy, claims, and customer data lookups

The model decides which tool(s) to call per turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from agent_framework import Agent
from agent_framework_foundry import FoundryChatClient
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from insurance_rag_agent.agent_tools import AGENT_TOOLS, get_kb_sources, reset_kb_sources
from insurance_rag_agent.config import Settings, get_settings
from insurance_rag_agent.models import Citation, QueryRequest, QueryResponse
from insurance_rag_agent.ontology import route
from insurance_rag_agent.providers.cosmos_provider import get_cosmos_provider

load_dotenv(override=False)

logger = logging.getLogger("insurance_rag")
logging.basicConfig(level=logging.INFO)

# --- Optional Application Insights / gen_ai tracing ------------------------
_ai_conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
if _ai_conn:
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=_ai_conn)
        logger.info("Application Insights telemetry enabled")
    except Exception:
        logger.warning("Failed to configure Application Insights", exc_info=True)
    try:
        from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

        OpenAIInstrumentor().instrument()
    except Exception:
        logger.warning("OpenAI gen_ai instrumentor not available", exc_info=True)


AGENT_INSTRUCTIONS = (
    "You are the Insurance Assistant, an agentic-RAG assistant for an auto-insurance company.\n"
    "You ground every answer in two authoritative tools and NEVER invent facts:\n\n"
    "1. search_knowledge_base — Azure AI Search over the auto-insurance knowledge base. "
    "Use for conceptual / educational questions (what is a deductible, how does comprehensive "
    "coverage work, claims process, terminology).\n"
    "2. Cosmos DB data tools — lookup_policy, list_policies, search_policies_by_name, "
    "lookup_customer, lookup_claims, get_coverage_summary. Use for specific policy, claim, "
    "or customer data.\n\n"
    "RULES:\n"
    "- Choose the RIGHT tool(s) based on the question. Conceptual -> knowledge base; "
    "data lookups -> Cosmos DB tools.\n"
    "- A question may need BOTH (e.g. 'what is my deductible on POL-001 and what does a "
    "deductible mean'): call the data tool AND the knowledge base, then combine.\n"
    "- NEVER guess policy_id, customer_id, or claim_id. If missing, ask the user.\n"
    "- Cite the knowledge base when you used it. Keep answers concise and helpful.\n"
)

app = FastAPI(title="Insurance Agentic-RAG API", version="1.0.0")


# ---------------------------------------------------------------------------
# Hosted-agent invocation (true agent-to-agent delegation).
#
# The delegating orchestrator agent reaches the two leaf agents through this
# app's /api/agents/{kb,policy} endpoints (its OpenAPI tool). Those endpoints
# INVOKE the hosted Foundry agents via the project's OpenAI-compatible Responses
# API (agent-scoped base URL). On App Service this uses the system-assigned
# managed identity, which needs the "Azure AI User" role on the Foundry project.
# ---------------------------------------------------------------------------
_project_client: AIProjectClient | None = None
_agent_clients: dict[str, object] = {}


def _get_project_client(settings: Settings) -> AIProjectClient:
    global _project_client
    if _project_client is None:
        if not settings.foundry_project_endpoint:
            raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT is not configured.")
        _project_client = AIProjectClient(
            endpoint=settings.foundry_project_endpoint,
            credential=DefaultAzureCredential(),
            allow_preview=True,
        )
    return _project_client


def _invoke_agent(settings: Settings, agent_name: str, question: str) -> str:
    """Invoke a hosted Foundry prompt agent and return its final text answer."""
    client = _agent_clients.get(agent_name)
    if client is None:
        client = _get_project_client(settings).get_openai_client(agent_name=agent_name)
        _agent_clients[agent_name] = client
    response = client.responses.create(input=question, extra_body={})
    return getattr(response, "output_text", None) or ""


class AgentAsk(BaseModel):
    question: str


class AgentAnswer(BaseModel):
    answer: str
    agent: str | None = None
    routed_to: list[str] | None = None
    matched_concepts: list[str] | None = None


def _build_agent(settings: Settings) -> Agent:
    if not settings.foundry_project_endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT is not configured.")
    client = FoundryChatClient(
        project_endpoint=settings.foundry_project_endpoint,
        model=settings.foundry_chat_model,
        credential=DefaultAzureCredential(),
    )
    return Agent(
        client,
        name=settings.foundry_agent_name,
        instructions=AGENT_INSTRUCTIONS,
        tools=AGENT_TOOLS,
    )


@app.on_event("startup")
async def _startup() -> None:
    app.state.settings = get_settings()
    try:
        app.state.agent = _build_agent(app.state.settings)
        logger.info("Insurance RAG agent initialized (model=%s)", app.state.settings.foundry_chat_model)
    except Exception:
        app.state.agent = None
        logger.warning("Agent not initialized at startup; will retry per request", exc_info=True)


@app.get("/health")
def health() -> dict:
    s: Settings = getattr(app.state, "settings", None) or get_settings()
    return {
        "status": "ok",
        "model": s.foundry_chat_model,
        "search_index": s.search_index,
        "cosmos_database": s.cosmos_database,
        "agent_ready": getattr(app.state, "agent", None) is not None,
    }


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    settings: Settings = getattr(app.state, "settings", None) or get_settings()
    agent: Agent | None = getattr(app.state, "agent", None)
    if agent is None:
        try:
            agent = _build_agent(settings)
            app.state.agent = agent
        except Exception as exc:  # pragma: no cover
            raise HTTPException(status_code=503, detail=f"Agent unavailable: {exc}") from exc

    # Provide any known identifiers as context to reduce clarifying round-trips.
    context_bits = []
    if req.policy_id:
        context_bits.append(f"policy_id={req.policy_id}")
    if req.customer_id:
        context_bits.append(f"customer_id={req.customer_id}")
    prompt = req.question
    if context_bits:
        prompt = f"{req.question}\n\n(Known context: {', '.join(context_bits)})"

    reset_kb_sources()
    try:
        result = await agent.run(prompt)
    except Exception as exc:
        logger.exception("Agent run failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    text = getattr(result, "text", None) or str(result)

    # De-duplicate knowledge-base sources captured during tool calls.
    citations: list[Citation] = []
    seen: set[str] = set()
    for s in get_kb_sources():
        key = f"{s.get('source')}|{s.get('snippet')}"
        if key in seen:
            continue
        seen.add(key)
        citations.append(Citation(**s))
    sources = sorted({c.source for c in citations if c.source})
    return QueryResponse(answer=text, sources=sources, citations=citations)


# ---------------------------------------------------------------------------
# Cosmos DB policy data — discrete read-only REST endpoints.
#
# These power the hosted Foundry *policy* agent's OpenAPI tool: the agent calls
# these operations directly to look up policy/claim/customer records in Cosmos
# DB (structured "Cosmos DB RAG"). They return plain JSON.
#
# NOTE: these endpoints are unauthenticated for the demo and serve SYNTHETIC
# data only. Do not expose real customer data this way — put the API behind an
# access-controlled dev tunnel, API key, or Entra ID before using real records.
# ---------------------------------------------------------------------------
@app.get("/api/policies")
def api_list_policies(status: str = "", agency: str = "") -> dict:
    """List policies, optionally filtered by status (Active/Expired/Cancelled) or agency."""
    rows = get_cosmos_provider().list_policies(status or None, agency or None)
    return {"count": len(rows), "policies": rows}


@app.get("/api/policies/search")
def api_search_policies(name: str) -> dict:
    """Find policies belonging to a customer by full or partial name."""
    rows = get_cosmos_provider().search_policies_by_name(name)
    return {"count": len(rows), "policies": rows}


@app.get("/api/policies/{policy_id}")
def api_get_policy(policy_id: str) -> dict:
    """Look up a single policy (vehicles, coverages, premium) by policy ID or number."""
    policy = get_cosmos_provider().get_policy(policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail=f"Policy not found: {policy_id}")
    return policy


@app.get("/api/customers/search")
def api_search_customers(name: str) -> dict:
    """Find customers by full or partial name."""
    rows = get_cosmos_provider().search_customers_by_name(name)
    return {"count": len(rows), "customers": rows}


@app.get("/api/customers/{customer_id}")
def api_get_customer(customer_id: str) -> dict:
    """Look up a single customer by customer ID."""
    cust = get_cosmos_provider().get_customer(customer_id)
    if not cust:
        raise HTTPException(status_code=404, detail=f"Customer not found: {customer_id}")
    return cust


@app.get("/api/claims")
def api_get_claims(claim_id: str = "", policy_id: str = "", customer_id: str = "") -> dict:
    """Look up claims by claim ID, policy ID, or customer ID."""
    rows = get_cosmos_provider().get_claims(claim_id or None, policy_id or None, customer_id or None)
    return {"count": len(rows), "claims": rows}


@app.get("/api/coverage-summary")
def api_coverage_summary() -> dict:
    """Aggregate coverage statistics across all policies."""
    return get_cosmos_provider().coverage_summary()


# ---------------------------------------------------------------------------
# Agent delegation endpoints.
#
# These are called by the delegating orchestrator agent's OpenAPI tool. Each
# one invokes a single hosted leaf agent and returns its answer. They run as
# sync endpoints so FastAPI executes them in a worker thread (the Responses SDK
# call is blocking), which keeps the event loop free and allows the re-entrant
# orchestrator -> tool -> leaf-agent call pattern to work concurrently.
# ---------------------------------------------------------------------------
@app.post("/api/agents/kb", response_model=AgentAnswer)
def api_ask_kb(req: AgentAsk) -> AgentAnswer:
    """Ask the hosted knowledge-base agent (Azure AI Search) a conceptual question."""
    s: Settings = getattr(app.state, "settings", None) or get_settings()
    try:
        answer = _invoke_agent(s, s.hosted_kb_agent_name, req.question)
    except Exception as exc:
        logger.exception("kb-agent invocation failed")
        raise HTTPException(status_code=502, detail=f"kb agent failed: {exc}") from exc
    return AgentAnswer(answer=answer, agent=s.hosted_kb_agent_name)


@app.post("/api/agents/policy", response_model=AgentAnswer)
def api_ask_policy(req: AgentAsk) -> AgentAnswer:
    """Ask the hosted policy agent (Cosmos data via REST) a policy/claim/customer question."""
    s: Settings = getattr(app.state, "settings", None) or get_settings()
    try:
        answer = _invoke_agent(s, s.hosted_policy_agent_name, req.question)
    except Exception as exc:
        logger.exception("policy-agent invocation failed")
        raise HTTPException(status_code=502, detail=f"policy agent failed: {exc}") from exc
    return AgentAnswer(answer=answer, agent=s.hosted_policy_agent_name)


@app.post("/api/chat", response_model=AgentAnswer)
def api_chat(req: AgentAsk) -> AgentAnswer:
    """Entry point for the chat UI.

    Routes deterministically with the insurance domain ontology (ontology.route)
    to the specialist agent(s) that own the concepts in the question, invokes
    them, and merges the result. This replaces the extra LLM orchestrator turn:
    routing is explainable, governable, and fast (no nested model hop).
    """
    s: Settings = getattr(app.state, "settings", None) or get_settings()
    decision = route(req.question)
    agent_name_by_key = {
        "policy": s.hosted_policy_agent_name,
        "kb": s.hosted_kb_agent_name,
    }
    label_by_key = {"policy": "Policy data", "kb": "Knowledge base"}
    answers: list[tuple[str, str]] = []
    try:
        for key in decision.agents:
            answers.append((key, _invoke_agent(s, agent_name_by_key[key], req.question)))
    except Exception as exc:
        logger.exception("ontology-routed agent invocation failed")
        raise HTTPException(status_code=502, detail=f"agent failed: {exc}") from exc

    if len(answers) == 1:
        combined = answers[0][1]
    else:
        combined = "\n\n".join(f"**{label_by_key[k]}:** {a}" for k, a in answers)

    return AgentAnswer(
        answer=combined,
        agent="ontology-router",
        routed_to=[agent_name_by_key[k] for k in decision.agents],
        matched_concepts=decision.concepts,
    )


@app.post("/api/orchestrator", response_model=AgentAnswer)
def api_orchestrator(req: AgentAsk) -> AgentAnswer:
    """Optional: the LLM delegating orchestrator agent (kept for comparison).

    Invokes the Foundry orchestrator agent, which decides whether to call the
    knowledge-base agent, the policy agent, or both via the /api/agents/*
    endpoints. Slower and subject to nested-call latency; the chat UI uses the
    deterministic ontology router at /api/chat instead.
    """
    s: Settings = getattr(app.state, "settings", None) or get_settings()
    try:
        answer = _invoke_agent(s, s.insurance_orchestrator_agent_name, req.question)
    except Exception as exc:
        logger.exception("orchestrator invocation failed")
        raise HTTPException(status_code=502, detail=f"orchestrator failed: {exc}") from exc
    return AgentAnswer(answer=answer, agent=s.insurance_orchestrator_agent_name)


# ---------------------------------------------------------------------------
# Voice: keyless Speech token for the browser Speech SDK.
#
# The browser does speech-to-text and text-to-speech directly against the AI
# Services account, but we never ship a key. This endpoint mints a short-lived
# Entra token (via the app's managed identity) in the SDK's Entra format
# "aad#<resourceId>#<aadToken>". The MI needs the "Cognitive Services Speech
# User" role on the AI Services account.
# ---------------------------------------------------------------------------
_speech_credential: DefaultAzureCredential | None = None


@app.get("/api/speech/token")
def api_speech_token() -> dict:
    """Return a short-lived, keyless Speech auth token + region for the browser SDK."""
    global _speech_credential
    s: Settings = getattr(app.state, "settings", None) or get_settings()
    if not s.speech_region or not s.speech_resource_id:
        raise HTTPException(status_code=404, detail="Speech is not configured.")
    if _speech_credential is None:
        _speech_credential = DefaultAzureCredential()
    try:
        aad = _speech_credential.get_token("https://cognitiveservices.azure.com/.default")
    except Exception as exc:
        logger.exception("failed to acquire Speech token")
        raise HTTPException(status_code=502, detail=f"speech token failed: {exc}") from exc
    return {
        "token": f"aad#{s.speech_resource_id}#{aad.token}",
        "region": s.speech_region,
        "language": s.speech_recognition_language,
        "voice": s.speech_synthesis_voice,
    }


# ---------------------------------------------------------------------------
# Foundry Voice Live (real-time speech-to-speech) — server-side relay.
#
# A separate live-voice mode from the browser-Speech mic above. The browser
# opens a WebSocket to /api/voicelive/ws and speaks the raw Voice Live protocol
# (session.update, input_audio_buffer.append, response.audio.delta, ...). The
# backend opens an upstream Voice Live WebSocket in AGENT mode against the
# insurance-orchestrator agent and adds a keyless Entra bearer token (managed
# identity) — browsers can't set a WebSocket Authorization header, so the relay
# is required. Frames are forwarded transparently in both directions. This is
# additive and does NOT touch the typed /api/chat ontology-router flow.
# ---------------------------------------------------------------------------
_voicelive_credential = None  # azure.identity.aio.DefaultAzureCredential (lazy)


@app.get("/api/voicelive/config")
def api_voicelive_config() -> dict:
    """Report whether the live-voice mode is configured (used by the UI to show it)."""
    s: Settings = getattr(app.state, "settings", None) or get_settings()
    enabled = bool(s.voicelive_endpoint and s.voicelive_project_name and s.voicelive_agent_name)
    return {
        "enabled": enabled,
        "voice": s.voicelive_voice,
        "agent": s.voicelive_agent_name if enabled else "",
    }


async def _voicelive_pump_upstream_to_client(connection, ws: WebSocket) -> None:
    """Forward raw Voice Live server frames straight to the browser."""
    while True:
        raw = await connection.recv_bytes()  # raw JSON text frame (bytes)
        if not raw:
            break
        await ws.send_text(raw.decode("utf-8"))


async def _voicelive_pump_client_to_upstream(connection, ws: WebSocket) -> None:
    """Forward browser client events (JSON) up to the Voice Live service."""
    while True:
        text = await ws.receive_text()
        try:
            event = json.loads(text)
        except ValueError:
            continue
        await connection.send(event)


@app.websocket("/api/voicelive/ws")
async def api_voicelive_ws(ws: WebSocket) -> None:
    """Relay a browser voice session to the Voice Live API in agent mode (keyless)."""
    s: Settings = getattr(app.state, "settings", None) or get_settings()
    await ws.accept()

    if not (s.voicelive_endpoint and s.voicelive_project_name and s.voicelive_agent_name):
        await ws.send_text(json.dumps({"type": "error", "error": {"message": "Voice Live is not configured."}}))
        await ws.close()
        return

    # Lazy imports so the rest of the app doesn't hard-depend on the Voice Live SDK.
    from azure.ai.voicelive.aio import connect as voicelive_connect
    from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential

    global _voicelive_credential
    if _voicelive_credential is None:
        _voicelive_credential = AsyncDefaultAzureCredential()

    try:
        async with voicelive_connect(
            credential=_voicelive_credential,
            endpoint=s.voicelive_endpoint,
            api_version=s.voicelive_api_version,
            agent_name=s.voicelive_agent_name,
            project_name=s.voicelive_project_name,
        ) as connection:
            up = asyncio.create_task(_voicelive_pump_upstream_to_client(connection, ws))
            down = asyncio.create_task(_voicelive_pump_client_to_upstream(connection, ws))
            done, pending = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    except WebSocketDisconnect:
        logger.info("voicelive: client disconnected")
    except Exception as exc:  # noqa: BLE001 - surface upstream errors to the client
        logger.exception("voicelive relay failed")
        try:
            await ws.send_text(json.dumps({"type": "error", "error": {"message": f"Voice Live relay failed: {exc}"}}))
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Browser chat UI.
# ---------------------------------------------------------------------------
_UI_PATH = Path(__file__).parent / "static" / "index.html"


@app.get("/", response_class=HTMLResponse)
def chat_ui() -> HTMLResponse:
    """Serve the single-page chat UI that talks to POST /api/chat."""
    try:
        return HTMLResponse(_UI_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Chat UI not found.")

