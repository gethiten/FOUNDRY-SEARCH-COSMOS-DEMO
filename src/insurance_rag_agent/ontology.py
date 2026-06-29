"""Insurance domain ontology for deterministic agent routing.

The ontology itself lives in **metadata** (``ontology.json``), not in code, so it
can be edited and governed without a code change. This module loads that metadata
and exposes a pure routing function.

Each concept maps to the specialist agent that "owns" it:

* Instance / structured-data concepts (a specific Policy, Claim, Customer,
  coverage totals) -> the **policy** agent (Azure Cosmos DB).
* Conceptual / educational concepts (what a term means, how a coverage works,
  the claims process) -> the **kb** agent (Azure AI Search knowledge base).

Routing is a pure function of the question text: match the question against each
concept's id-patterns (regex) and labels (phrases), collect the owning agents,
and return them together with the concepts that fired (for an auditable trail).

The metadata source is resolved in this order:

1. ``ONTOLOGY_BLOB_URL`` -- a governed blob (read via managed identity) for
   centrally controlled, config-driven governance.
2. ``ONTOLOGY_PATH`` -- an external/local file path.
3. The packaged ``ontology.json`` next to this module.

If the governed source is unreachable or invalid, the packaged copy is used so
the service never goes down.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("insurance_rag.ontology")

# Agent ownership keys (mapped to concrete agent names by the caller).
POLICY = "policy"
KB = "kb"

# Packaged ontology shipped with the app — the always-available fallback.
_PACKAGED_PATH = Path(__file__).parent / "ontology.json"

# Path to the ontology metadata file. Point ONTOLOGY_PATH at an external/governed
# location for config-driven governance; if unset or unreadable, the packaged
# copy above is used.
ONTOLOGY_PATH = Path(os.environ.get("ONTOLOGY_PATH") or _PACKAGED_PATH)

# Governed blob URL (e.g. https://<acct>.blob.core.windows.net/ontology/ontology.json).
# When set, it takes precedence and is read with the app's managed identity.
ONTOLOGY_BLOB_URL = os.environ.get("ONTOLOGY_BLOB_URL")


@dataclass(frozen=True)
class Concept:
    """A domain concept and the agent that owns it."""

    name: str
    agent: str
    id_patterns: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()


def _parse_ontology(data: dict) -> tuple[tuple[Concept, ...], str]:
    """Build concepts and the default agent from parsed metadata."""
    concepts = tuple(
        Concept(
            name=c["name"],
            agent=c["agent"],
            id_patterns=tuple(c.get("id_patterns", ())),
            labels=tuple(label.lower() for label in c.get("labels", ())),
        )
        for c in data.get("concepts", [])
    )
    default_agent = data.get("default_agent", KB)
    return concepts, default_agent


def _read_blob(url: str) -> str:
    """Download ontology JSON text from a blob URL using the app's managed identity."""
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobClient

    client = BlobClient.from_blob_url(url, credential=DefaultAzureCredential())
    return client.download_blob(encoding="utf-8").readall()


def _read_primary() -> tuple[str, str]:
    """Return ``(json_text, source_label)`` from the governed source.

    Prefers ``ONTOLOGY_BLOB_URL`` (a governed blob read via managed identity);
    otherwise reads the ``ONTOLOGY_PATH`` file.
    """
    if ONTOLOGY_BLOB_URL:
        return _read_blob(ONTOLOGY_BLOB_URL), ONTOLOGY_BLOB_URL
    return ONTOLOGY_PATH.read_text(encoding="utf-8"), str(ONTOLOGY_PATH)


def _load_ontology() -> tuple[tuple[Concept, ...], str]:
    """Load ontology metadata from the governed source, falling back to the packaged file.

    A missing or malformed governed source must never take the service down, so
    any failure logs a warning and uses the packaged ``ontology.json``.
    """
    try:
        text, source = _read_primary()
        concepts, default_agent = _parse_ontology(json.loads(text))
        if not concepts:
            raise ValueError("ontology metadata contains no concepts")
        logger.info("Loaded %d ontology concepts from %s", len(concepts), source)
        return concepts, default_agent
    except Exception:
        governed = bool(ONTOLOGY_BLOB_URL) or ONTOLOGY_PATH != _PACKAGED_PATH
        if governed:
            logger.warning("Failed to load governed ontology; using packaged copy", exc_info=True)
            return _parse_ontology(json.loads(_PACKAGED_PATH.read_text(encoding="utf-8")))
        raise


# Loaded once at import. Concept order is preserved for the returned trail.
ONTOLOGY, DEFAULT_AGENT = _load_ontology()


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
