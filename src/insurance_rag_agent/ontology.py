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
the service never goes down. When a governed source is configured, it is polled
for changes (ETag for a blob, mtime for a file) at most once every
``ONTOLOGY_RELOAD_SECONDS`` (default 30; set 0 to disable) so edits take effect
without an app restart.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
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

# How often (seconds) to poll the governed source for changes so edits apply
# without a restart. Set to 0 to load once at startup and never re-check.
try:
    ONTOLOGY_RELOAD_SECONDS = float(os.environ.get("ONTOLOGY_RELOAD_SECONDS", "30"))
except ValueError:
    ONTOLOGY_RELOAD_SECONDS = 30.0


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


_blob_client = None  # cached BlobClient for the governed blob (reused across polls)


def _get_blob_client():
    """Return a cached BlobClient for the governed blob, created on first use."""
    global _blob_client
    if _blob_client is None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobClient

        _blob_client = BlobClient.from_blob_url(ONTOLOGY_BLOB_URL, credential=DefaultAzureCredential())
    return _blob_client


def _read_primary() -> tuple[str, str]:
    """Return ``(json_text, source_label)`` from the governed source.

    Prefers ``ONTOLOGY_BLOB_URL`` (a governed blob read via managed identity);
    otherwise reads the ``ONTOLOGY_PATH`` file.
    """
    if ONTOLOGY_BLOB_URL:
        return _get_blob_client().download_blob(encoding="utf-8").readall(), ONTOLOGY_BLOB_URL
    return ONTOLOGY_PATH.read_text(encoding="utf-8"), str(ONTOLOGY_PATH)


def _source_version() -> str | None:
    """Return a cheap change token for the active source (blob ETag or file mtime)."""
    if ONTOLOGY_BLOB_URL:
        return _get_blob_client().get_blob_properties().etag
    return str(ONTOLOGY_PATH.stat().st_mtime_ns)


def _safe_source_version() -> str | None:
    """Best-effort ``_source_version`` that never raises."""
    try:
        return _source_version()
    except Exception:
        return None


@dataclass
class _OntologyState:
    """The loaded ontology plus the source version it was loaded from."""

    concepts: tuple[Concept, ...]
    default_agent: str
    version: str | None
    checked_at: float


def _load_state(known_version: str | None = None) -> _OntologyState:
    """Load ontology from the governed source, falling back to the packaged file.

    A missing or malformed governed source must never take the service down, so
    any failure logs a warning and uses the packaged ``ontology.json``.
    """
    try:
        text, source = _read_primary()
        concepts, default_agent = _parse_ontology(json.loads(text))
        if not concepts:
            raise ValueError("ontology metadata contains no concepts")
        version = known_version if known_version is not None else _safe_source_version()
        logger.info("Loaded %d ontology concepts from %s", len(concepts), source)
        return _OntologyState(concepts, default_agent, version, time.monotonic())
    except Exception:
        governed = bool(ONTOLOGY_BLOB_URL) or ONTOLOGY_PATH != _PACKAGED_PATH
        if governed:
            logger.warning("Failed to load governed ontology; using packaged copy", exc_info=True)
            concepts, default_agent = _parse_ontology(json.loads(_PACKAGED_PATH.read_text(encoding="utf-8")))
            return _OntologyState(concepts, default_agent, None, time.monotonic())
        raise


# Loaded once at import; refreshed in place when the governed source changes.
_state = _load_state()
_lock = threading.Lock()

# Initial snapshot for introspection / back-compat (live values live in _state).
ONTOLOGY = _state.concepts
DEFAULT_AGENT = _state.default_agent


def _maybe_reload() -> None:
    """Reload the ontology if the governed source changed, throttled by TTL."""
    global _state
    if ONTOLOGY_RELOAD_SECONDS <= 0:
        return
    now = time.monotonic()
    if now - _state.checked_at < ONTOLOGY_RELOAD_SECONDS:
        return
    with _lock:
        # Another thread may have refreshed while we waited for the lock.
        if now - _state.checked_at < ONTOLOGY_RELOAD_SECONDS:
            return
        _state.checked_at = now  # throttle further checks regardless of outcome
        version = _safe_source_version()
        if version is None or version == _state.version:
            return  # unreadable or unchanged -> keep the current copy
        _state = _load_state(known_version=version)


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
    _maybe_reload()
    state = _state
    text = (question or "").lower()
    fired: list[Concept] = []
    for concept in state.concepts:
        if any(re.search(p, question, re.IGNORECASE) for p in concept.id_patterns):
            fired.append(concept)
            continue
        if any(label in text for label in concept.labels):
            fired.append(concept)

    if not fired:
        return RouteDecision(agents=[state.default_agent], concepts=[], matched=False)

    # Preserve agent order as policy-then-kb for stable, readable merged output.
    agents: list[str] = []
    for key in (POLICY, KB):
        if any(c.agent == key for c in fired):
            agents.append(key)
    return RouteDecision(agents=agents, concepts=[c.name for c in fired], matched=True)
