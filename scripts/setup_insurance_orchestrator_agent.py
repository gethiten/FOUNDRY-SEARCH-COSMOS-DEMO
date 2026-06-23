"""Register the delegating Insurance Orchestrator agent.

Unlike ``setup_orchestrator_agent.py`` (which combines the Search + policy
OpenAPI tools into one router agent), THIS orchestrator performs true
agent-to-agent delegation: its single OpenAPI tool calls back to this app's
``/api/agents/kb`` and ``/api/agents/policy`` endpoints, and those endpoints
*invoke the two hosted leaf agents* (kb-search-agent and policy-cosmos-agent)
via the Foundry Responses API.

Call graph at runtime:

    Chat UI -> POST /api/chat
            -> invoke  insurance-orchestrator  (this agent)
                 -> OpenAPI tool: askKnowledgeBase -> POST /api/agents/kb
                        -> invoke  kb-search-agent  (Azure AI Search)
                 -> OpenAPI tool: askPolicyData    -> POST /api/agents/policy
                        -> invoke  policy-cosmos-agent  (Cosmos via OpenAPI)
            -> combined answer back to the UI

Prerequisites:
  * POLICY_API_BASE_URL — public HTTPS URL of the FastAPI app (App Service).
  * The two leaf agents (kb-search-agent, policy-cosmos-agent) already registered.
  * The App Service managed identity must hold the "Azure AI User" role on the
    Foundry project so /api/agents/* can invoke the leaf agents.

Run:
    $env:PYTHONPATH = "src"
    python scripts/setup_insurance_orchestrator_agent.py
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
    "You are the Auto-Insurance Orchestrator. You answer every question by "
    "delegating to two specialist agents through your tools, then composing a "
    "single clear answer. You have no direct data access yourself.\n\n"
    "YOUR TOOLS:\n"
    "- askPolicyData(question): the Policy specialist. Use it for SPECIFIC policy, "
    "claim, or customer data — policy IDs like POL-001, policy numbers, customer "
    "names, deductibles for a named policy, claim status, premiums, coverage totals.\n"
    "- askKnowledgeBase(question): the Knowledge-Base specialist. Use it for GENERAL "
    "or EDUCATIONAL questions — what a deductible is, how comprehensive coverage "
    "works, the claims process, insurance terminology.\n\n"
    "ROUTING RULES:\n"
    "- Decide which specialist(s) the user's question needs. Call only the relevant "
    "one when the question is purely data OR purely conceptual.\n"
    "- When a question needs BOTH (e.g. 'explain the deductible on POL-001 and what a "
    "deductible means'), call askPolicyData AND askKnowledgeBase, then merge the two "
    "answers into one response.\n"
    "- Pass a focused, self-contained question string to each tool. You may rephrase "
    "the user's question for the specialist.\n\n"
    "GENERAL RULES:\n"
    "- ALWAYS delegate to a tool before answering; never invent policy IDs, names, "
    "amounts, or definitions.\n"
    "- If a required identifier (policy ID, customer name) is missing, ask the user.\n"
    "- Be concise and clearly attribute which facts came from policy data vs. the "
    "knowledge base."
)


def _delegation_spec(base_url: str) -> dict:
    """OpenAPI 3.0 spec for the two delegation endpoints (POST {question} -> {answer})."""
    ask_body = {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "A focused, self-contained question for the specialist agent.",
                        }
                    },
                    "required": ["question"],
                }
            }
        },
    }
    answer_response = {
        "200": {
            "description": "The specialist agent's answer.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "answer": {"type": "string"},
                            "agent": {"type": "string"},
                        },
                    }
                }
            },
        }
    }
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Insurance Agent Delegation API",
            "version": "1.0.0",
            "description": "Delegate questions to the knowledge-base and policy specialist agents.",
        },
        "servers": [{"url": base_url.rstrip("/")}],
        "paths": {
            "/api/agents/policy": {
                "post": {
                    "operationId": "askPolicyData",
                    "summary": "Ask the Policy specialist about a specific policy, claim, or customer.",
                    "requestBody": ask_body,
                    "responses": answer_response,
                }
            },
            "/api/agents/kb": {
                "post": {
                    "operationId": "askKnowledgeBase",
                    "summary": "Ask the Knowledge-Base specialist a general/educational insurance question.",
                    "requestBody": ask_body,
                    "responses": answer_response,
                }
            },
        },
    }


def main() -> None:
    settings = get_settings()

    base_url = settings.policy_api_base_url
    if not base_url:
        raise SystemExit(
            "POLICY_API_BASE_URL is not set. Expose the FastAPI app on a public "
            "HTTPS URL (App Service) and put it in .env."
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
            "model": settings.insurance_orchestrator_agent_model,
            "instructions": INSTRUCTIONS,
            "tools": [
                {
                    "type": "openapi",
                    "openapi": {
                        "name": "agent_delegation",
                        "description": (
                            "Delegate questions to the knowledge-base agent "
                            "(askKnowledgeBase) and the policy agent (askPolicyData)."
                        ),
                        "spec": _delegation_spec(base_url),
                        "auth": {"type": "anonymous"},
                    },
                }
            ],
        }
    }

    version = client.agents.create_version(
        agent_name=settings.insurance_orchestrator_agent_name,
        body=body,
        description="Delegating orchestrator: routes to kb-search-agent and policy-cosmos-agent.",
    )
    print(f"Created {settings.insurance_orchestrator_agent_name} version: {version.id}")
    print(f"Delegation server: {base_url}")
    print(json.dumps(version.as_dict(), indent=2, default=str))


if __name__ == "__main__":
    main()
