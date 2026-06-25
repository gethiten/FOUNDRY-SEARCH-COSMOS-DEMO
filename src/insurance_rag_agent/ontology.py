"""Insurance domain ontology for deterministic agent routing.

Maps domain *concepts* to the specialist agent that "owns" them, so the chat
entry point can decide which agent(s) to call explainably and fast — instead of
paying for an extra LLM "orchestrator" turn that guesses (and can stall).

Two ownership regions map 1:1 to the two specialist agents:

* Instance / structured-data concepts (a specific Policy, Claim, Customer,
  coverage totals) -> the **policy** agent (Azure Cosmos DB).
* Conceptual / educational concepts (what a term means, how a coverage works,
  the claims process) -> the **kb** agent (Azure AI Search knowledge base).

Routing is a pure function of the question text: match the question against each
concept's id-patterns (regex) and labels (phrases), collect the owning agents,
and return them together with the concepts that fired (for an auditable trail).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Agent ownership keys (mapped to concrete agent names by the caller).
POLICY = "policy"
KB = "kb"


@dataclass(frozen=True)
class Concept:
    """A domain concept and the agent that owns it."""

    name: str
    agent: str
    id_patterns: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()


# The ontology. Order matters only for the returned concept trail, not routing.
ONTOLOGY: tuple[Concept, ...] = (
    # --- Structured-data concepts -> policy (Cosmos) agent -----------------
    Concept(
        name="Policy",
        agent=POLICY,
        id_patterns=(r"\bPOL-\d+\b", r"\bAU-\d+\b"),
        labels=("policy", "policies", "my coverage", "renewal", "premium", "deductible on", "vehicles on"),
    ),
    Concept(
        name="Claim",
        agent=POLICY,
        id_patterns=(r"\bCLM-\d+\b", r"\bCL-\d{4}-\d+\b"),
        labels=("claim status", "claims for", "claims on", "my claim", "claim details", "payout"),
    ),
    Concept(
        name="Customer",
        agent=POLICY,
        id_patterns=(r"\bCUST-\d+\b",),
        labels=("customer", "policyholder", "insured", "account holder"),
    ),
    Concept(
        name="CoverageSummary",
        agent=POLICY,
        labels=("coverage summary", "portfolio", "total premium", "across all policies", "how many policies"),
    ),
    # --- Conceptual concepts -> knowledge-base (Search) agent --------------
    Concept(
        name="Terminology",
        agent=KB,
        labels=(
            "what is a", "what is an", "what are", "what's a", "what does",
            "define", "definition", "explain", "how does", "meaning of", " mean",
        ),
    ),
    Concept(
        name="CoverageType",
        agent=KB,
        labels=("comprehensive coverage", "collision coverage", "liability coverage",
                "uninsured motorist", "what is covered", "types of coverage"),
    ),
    Concept(
        name="ClaimsProcess",
        agent=KB,
        labels=("how to file", "how do i file", "claims process", "filing a claim",
                "file a claim", "steps to file", "how do i claim"),
    ),
)

# Agent used when no concept matches (general/uncertain questions). The KB agent
# can answer conceptually or ask the user to clarify.
DEFAULT_AGENT = KB


@dataclass(frozen=True)
class RouteDecision:
    """Result of routing a question through the ontology."""

    agents: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    matched: bool = False


def route(question: str) -> RouteDecision:
    """Decide which specialist agent(s) own the concepts in ``question``.

    Returns the owning agent keys (``"policy"`` and/or ``"kb"``) and the names of
    the concepts that fired, in ontology order. Falls back to ``DEFAULT_AGENT``
    when nothing matches.
    """
    text = (question or "").lower()
    fired: list[Concept] = []
    for concept in ONTOLOGY:
        if any(re.search(p, question, re.IGNORECASE) for p in concept.id_patterns):
            fired.append(concept)
            continue
        if any(label in text for label in concept.labels):
            fired.append(concept)

    if not fired:
        return RouteDecision(agents=[DEFAULT_AGENT], concepts=[], matched=False)

    # Preserve agent order as policy-then-kb for stable, readable merged output.
    agents: list[str] = []
    for key in (POLICY, KB):
        if any(c.agent == key for c in fired):
            agents.append(key)
    return RouteDecision(agents=agents, concepts=[c.name for c in fired], matched=True)
