"""MCP server (Week 5 §5.1) — FastAPI front-end over the ingestion / report /
concern CLIs, with API-key auth and the existing guardrails wired in.

Endpoints
---------
``POST /ingest``              Trigger the ingestion pipeline (3 sources -> dual
                              store). Screens incoming text with
                              ``InputSanitizer`` (flags -> ``audit_log``).
``GET  /report?date=...``     Run the Concern-Engine-grounded Report Agent and
                              return the cited Markdown (``OutputSanitizer``-ed).
``GET  /concerns?min_sev=..`` Run the Concern Engine and return the
                              severity-filtered, deterministic risk list.

Every endpoint requires an ``X-API-Key`` header matching ``config.MCP_API_KEY``.

Run it with::

    python src/mcp/server.py
    # or: uvicorn mcp.server:app --reload --port 8000   (run from src/)

Then::

    curl -X POST http://localhost:8000/ingest   -H "X-API-Key: $MCP_API_KEY"
    curl "http://localhost:8000/report?date=2025-05-30"   -H "X-API-Key: $MCP_API_KEY"
    curl "http://localhost:8000/concerns?min_sev=3"       -H "X-API-Key: $MCP_API_KEY"
"""
from __future__ import annotations

import os
import sys

# --- Make the module runnable from any entry point (CLI, import, tests) ------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))   # .../src/mcp
_SRC_DIR = os.path.dirname(_THIS_DIR)                     # .../src
_ROOT_DIR = os.path.dirname(_SRC_DIR)                     # repo root
for _p in (_ROOT_DIR, _SRC_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from agents.concern_engine import ConcernEngine
from agents.report_pipeline import generate_grounded_report
from config import CHROMA_PATH, DB_PATH, MCP_API_KEY
from guardrail.sanitizer import InputSanitizer
from ingestion.run_pipeline import (
    DEFAULT_CONFLUENCE_PATH,
    DEFAULT_JIRA_PATH,
    DEFAULT_MEETING_NOTES_PATH,
    run_pipeline,
)
from storage.chroma_store import ChromaStore
from storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI Project Intelligence Agent — MCP Server",
    description="FastAPI front-end over the ingestion / report / concern CLIs.",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Auth — Week 5 §5.1: API key in the `X-API-Key` header
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: Optional[str] = Security(_api_key_header)) -> None:
    """Reject the request unless ``X-API-Key`` matches ``config.MCP_API_KEY``.

    Fails closed: a server with no configured key refuses every request
    (rather than treating "unset" as "open").
    """
    if not MCP_API_KEY:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "MCP_API_KEY is not configured on the server (set it in .env).",
        )
    if api_key != MCP_API_KEY:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid X-API-Key header.")


# ---------------------------------------------------------------------------
# Lazily-constructed, shared ChromaDB client (PersistentClient is expensive to
# open and safe to reuse across requests; SQLiteStore is opened per-request
# instead, since it's a lightweight context manager around a sqlite3 connection).
# ---------------------------------------------------------------------------

_chroma_store: Optional[ChromaStore] = None


def _get_chroma_store() -> ChromaStore:
    global _chroma_store
    if _chroma_store is None:
        _chroma_store = ChromaStore(path=CHROMA_PATH)
    return _chroma_store


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    """Optional source overrides; omitted fields fall back to the synthetic
    data paths used by ``run_agent.sh``."""

    jira_path: Optional[str] = None
    conf_path: Optional[str] = None
    notes_path: Optional[str] = None


class IngestResponse(BaseModel):
    documents: int
    entities: int
    backlinks: int
    jira_docs: int
    confluence_chunks: int
    meeting_chunks: int
    sources: List[str]
    flagged_injections: int = Field(
        description="Documents whose text_content matched an InputSanitizer "
        "injection pattern and were filtered before indexing."
    )


class ReportResponse(BaseModel):
    date: str
    report: str = Field(description="Cited Markdown report (OutputSanitizer-ed).")
    concern_count: int = Field(description="Concern Engine findings used to ground the report.")


class ConcernItem(BaseModel):
    type: str
    task_id: str
    severity: int
    explanation: str
    source_ids: List[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post(
    "/ingest",
    response_model=IngestResponse,
    summary="Trigger the ingestion pipeline",
    dependencies=[Depends(require_api_key)],
)
def ingest(payload: Optional[IngestRequest] = None) -> Dict[str, Any]:
    """Run the 3-connector ingestion pipeline, screening text with the input
    guardrail (Week 5 §5.2) before it reaches ChromaDB/SQLite. Injection
    attempts are filtered and recorded to ``audit_log``."""
    payload = payload or IngestRequest()
    jira_path = payload.jira_path or DEFAULT_JIRA_PATH
    conf_path = payload.conf_path or DEFAULT_CONFLUENCE_PATH
    notes_path = payload.notes_path or DEFAULT_MEETING_NOTES_PATH

    logger.info("POST /ingest -> jira=%s conf=%s notes=%s", jira_path, conf_path, notes_path)
    with SQLiteStore(db_path=DB_PATH) as audit_store:
        sanitizer = InputSanitizer(audit_store=audit_store)
        stats = run_pipeline(
            jira_path,
            conf_path,
            notes_path,
            db_path=DB_PATH,
            chroma_path=CHROMA_PATH,
            sanitizer=sanitizer,
        )

    # Ingestion rebuilds the dual store, so any cached Chroma client is stale.
    global _chroma_store
    _chroma_store = None

    return stats


@app.get(
    "/report",
    response_model=ReportResponse,
    summary="Generate the grounded daily report",
    dependencies=[Depends(require_api_key)],
)
def get_report(
    date: str = Query(..., description="Reference (as-of) ISO date, e.g. 2025-05-30"),
) -> Dict[str, Any]:
    """Run the Concern Engine for grounding, then the ReAct Report Agent, and
    return the cited Markdown report (secrets redacted by ``OutputSanitizer``)."""
    chroma_store = _get_chroma_store()
    with SQLiteStore(db_path=DB_PATH) as sqlite_store:
        concerns = ConcernEngine(as_of=date).run_all_rules(sqlite_store, chroma_store)
        logger.info("GET /report?date=%s -> grounding with %d concern(s)", date, len(concerns))
        report = generate_grounded_report(date, concerns, sqlite_store, chroma_store)

    return {"date": date, "report": report, "concern_count": len(concerns)}


@app.get(
    "/concerns",
    response_model=List[ConcernItem],
    summary="List deterministic risk findings",
    dependencies=[Depends(require_api_key)],
)
def get_concerns(
    min_sev: int = Query(1, ge=1, le=5, description="Only return concerns with severity >= this"),
    date: Optional[str] = Query(None, description="Reference (as-of) ISO date; default = today"),
) -> List[Dict[str, Any]]:
    """Run the rule-based Concern Engine and return findings at or above
    ``min_sev``, severity-sorted (deterministic — no LLM call)."""
    chroma_store = _get_chroma_store()
    with SQLiteStore(db_path=DB_PATH) as sqlite_store:
        concerns = ConcernEngine(as_of=date).run_all_rules(sqlite_store, chroma_store)

    filtered = [c for c in concerns if c["severity"] >= min_sev]
    logger.info("GET /concerns?min_sev=%d&date=%s -> %d/%d", min_sev, date, len(filtered), len(concerns))
    return filtered


# ---------------------------------------------------------------------------
# Standalone launcher — `python src/mcp/server.py`
# ---------------------------------------------------------------------------

def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    from config import validate_config
    validate_config()  # fail fast if OPENAI_API_KEY missing (needed by /report)
    if not MCP_API_KEY:
        raise SystemExit("MCP_API_KEY is missing — set it in .env before starting the server.")

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
