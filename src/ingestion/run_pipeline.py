"""Ingestion orchestrator / entry point (Week 2).

Loads the three data sources, extracts entities + backlinks, and routes
everything into the dual store:

    connectors ──► normalized docs ──┬─► EntityExtractor ─► SQLite (entities, snapshots, backlinks)
                                     └─► ChromaDB (jira descriptions, confluence/meeting chunks)

Run it as a script to (re)build the stores end-to-end::

    python src/ingestion/run_pipeline.py \
        --jira  data/jira/jira_synthetic_AIP.json \
        --conf  data/confluence \
        --notes data/meeting_notes

Field bridging
--------------
The connectors emit a normalized doc using ``text_content`` / ``source_id``,
but :class:`storage.chroma_store.ChromaStore` reads ``content`` / ``page_id`` /
``note_id``. :func:`_confluence_to_chroma` and :func:`_meeting_to_chroma` bridge
those field names so chunks are actually indexed (the previous orchestrator
passed docs straight through and silently indexed nothing).
"""
from __future__ import annotations

import os
import sys

# --- Make the module runnable from any entry point (CLI, import, tests) ------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))   # .../src/ingestion
_SRC_DIR = os.path.dirname(_THIS_DIR)                     # .../src
_ROOT_DIR = os.path.dirname(_SRC_DIR)                     # repo root
for _p in (_ROOT_DIR, _SRC_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import argparse
import logging
from datetime import date
from typing import Any, Dict, List, Optional

from config import (
    CHROMA_PATH,
    CONFLUENCE_API_TOKEN,
    CONFLUENCE_BASE_URL,
    DB_PATH,
    JIRA_API_TOKEN,
    JIRA_BASE_URL,
    JIRA_EMAIL,
    SOURCE_MODE,
)
from guardrail.sanitizer import InputSanitizer
from ingestion.confluence_connector import ConfluenceConnector
from ingestion.entity_extractor import EntityExtractor
from ingestion.jira_connector import JiraConnector
from ingestion.meeting_notes_connector import MeetingNotesConnector
from storage.chroma_store import ChromaStore
from storage.init_db import init_db
from storage.sqlite_store import SQLiteStore, meaningful_fields

logger = logging.getLogger(__name__)

# Default source locations (match the synthetic data layout in data/).
DEFAULT_JIRA_PATH = "data/jira/jira_synthetic_AIP.json"
DEFAULT_CONFLUENCE_PATH = "data/confluence"
DEFAULT_MEETING_NOTES_PATH = "data/meeting_notes"


# ---------------------------------------------------------------------------
# Field bridges: connector normalized doc -> ChromaStore method input
# ---------------------------------------------------------------------------

def _confluence_to_chroma(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Map a normalized Confluence doc onto ``add_confluence_chunks`` input."""
    return {
        "content": doc.get("text_content", ""),
        "page_id": doc.get("source_id", ""),
        "space": doc.get("space", ""),
        "author": doc.get("author", ""),
        "last_updated": doc.get("last_updated", ""),
        "status": doc.get("status", ""),
        "linked_jira_epics": doc.get("linked_jira_epics", []),
    }


def _meeting_to_chroma(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Map a normalized Meeting Notes doc onto ``add_meeting_chunks`` input."""
    return {
        "content": doc.get("text_content", ""),
        "note_id": doc.get("source_id", ""),
        "date": doc.get("date", ""),
        "project": doc.get("project", ""),
    }


# ---------------------------------------------------------------------------
# Source selection: synthetic (local JSON) vs api (live Jira/Confluence Cloud)
# ---------------------------------------------------------------------------

def _make_jira_connector(jira_path: Optional[str], source_mode: str):
    """Pick the Jira connector for ``source_mode``; ``None`` to skip Jira."""
    if source_mode == "api":
        from ingestion.jira_api_connector import JiraApiConnector

        return JiraApiConnector(JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN)
    return JiraConnector(jira_path) if jira_path else None


def _make_confluence_connector(conf_path: Optional[str], source_mode: str):
    """Pick the Confluence connector for ``source_mode``; ``None`` to skip."""
    if source_mode == "api":
        from ingestion.confluence_api_connector import ConfluenceApiConnector

        return ConfluenceApiConnector(
            CONFLUENCE_BASE_URL, JIRA_EMAIL, CONFLUENCE_API_TOKEN
        )
    return ConfluenceConnector(conf_path) if conf_path else None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_pipeline(
    jira_path: Optional[str] = None,
    conf_path: Optional[str] = None,
    notes_path: Optional[str] = None,
    *,
    db_path: str = DB_PATH,
    chroma_path: str = CHROMA_PATH,
    sanitizer: Optional[InputSanitizer] = None,
    as_of: Optional[str] = None,
    source_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Run ingestion across the provided sources and route into both stores.

    Parameters
    ----------
    jira_path:
        Path to the Jira JSON **file** (or ``None`` to skip Jira).
    conf_path:
        Path to the Confluence **folder** of JSON files (or ``None`` to skip).
    notes_path:
        Path to the Meeting Notes **folder** (or ``None`` to skip).
    db_path:
        SQLite database path (defaults to ``config.DB_PATH``).
    chroma_path:
        ChromaDB persistence path (defaults to ``config.CHROMA_PATH``).
    sanitizer:
        Optional :class:`guardrail.sanitizer.InputSanitizer` (Week 5 §5.2).
        When given, every document's ``text_content`` is screened for
        prompt-injection patterns *before* it reaches ChromaDB/SQLite —
        matches are replaced with a filtered placeholder and logged to
        ``audit_log``. Clean text is passed through untouched (the
        sanitizer's truncate/HTML-strip side effects are skipped here so
        long-form Confluence/meeting content keeps chunking normally);
        ``None`` (the default) preserves the original ingest-everything
        behaviour used by ``run_agent.sh`` and the test suite.
    as_of:
        Reference ISO date (YYYY-MM-DD) tagging this run's snapshots. Defaults
        to today. Snapshots are written **only for entities that changed** since
        the prior baseline (incremental), so ``get_daily_diff(as_of)`` surfaces
        real day-over-day changes without re-snapshotting unchanged tasks.

    Returns
    -------
    dict
        Summary stats: ``documents``, ``entities``, ``entities_changed``,
        ``backlinks``, ``jira_docs``, ``confluence_chunks``, ``meeting_chunks``,
        ``sources``, ``flagged_injections``.
    """
    logger.info("Initializing database at %s", db_path)
    init_db(db_path)

    # 1. Load normalized docs from each source ------------------------------
    # source_mode "synthetic" (default) reads local JSON; "api" reads live
    # Jira/Confluence Cloud. Meeting notes have no standard API → always file-based.
    source_mode = source_mode or SOURCE_MODE
    all_docs: List[Dict[str, Any]] = []

    jira_connector = _make_jira_connector(jira_path, source_mode)
    if jira_connector is not None:
        logger.info("Loading Jira (source_mode=%s)", source_mode)
        all_docs.extend(jira_connector.load())

    conf_connector = _make_confluence_connector(conf_path, source_mode)
    if conf_connector is not None:
        logger.info("Loading Confluence (source_mode=%s)", source_mode)
        all_docs.extend(conf_connector.load())

    if notes_path:
        logger.info("Loading Meeting Notes from %s", notes_path)
        all_docs.extend(MeetingNotesConnector(notes_path).load())

    logger.info("Loaded %d normalized document(s).", len(all_docs))

    # 1b. Input guardrail — screen for prompt-injection before indexing -----
    flagged_injections = 0
    if sanitizer is not None:
        for doc in all_docs:
            text = doc.get("text_content") or ""
            if text and sanitizer.sanitize(text, "text_content", doc.get("source_id", "unknown")) == "[FILTERED]":
                doc["text_content"] = "[FILTERED: potential injection in text_content]"
                flagged_injections += 1
        if flagged_injections:
            logger.warning(
                "Input guardrail flagged %d document(s) for prompt-injection patterns "
                "(see audit_log).", flagged_injections,
            )

    # 2. Extract entities + backlinks ---------------------------------------
    entities, backlinks = EntityExtractor().extract(all_docs)
    logger.info("Extracted %d entities and %d backlinks.", len(entities), len(backlinks))

    # 3. Route structured data into SQLite ----------------------------------
    sources = sorted({doc["source"] for doc in all_docs if doc.get("source")})
    snapshot_date = as_of or date.today().isoformat()
    entities_changed = 0
    with SQLiteStore(db_path=db_path) as sqlite_store:
        # Capture the prior baseline BEFORE upserting so we can snapshot only the
        # entities that actually changed (incremental / delta-only snapshots).
        prior_states = sqlite_store.load_current_states()
        sqlite_store.bulk_upsert(entities)

        for entity in entities:
            task_id = entity.get("task_id") or entity.get("source_id")
            if not task_id:
                continue
            new_state = meaningful_fields(entity)
            old_state = prior_states.get(task_id)
            if old_state is None:
                # New / first-seen task → baseline snapshot (no prior to diff).
                sqlite_store.save_snapshot(task_id, entity, None, snapshot_date=snapshot_date)
                entities_changed += 1
            elif old_state != new_state:
                diff = {
                    k: [old_state.get(k), new_state.get(k)]
                    for k in new_state
                    if old_state.get(k) != new_state.get(k)
                }
                sqlite_store.save_snapshot(task_id, entity, diff, snapshot_date=snapshot_date)
                entities_changed += 1
            # else: unchanged → skip (no snapshot, no reprocessing).

        # Backlinks are fully derived from the current docs → replace, don't append.
        sqlite_store.replace_backlinks(backlinks)
        for source in sources:
            sqlite_store.update_sync_log(source)
    logger.info(
        "SQLite: upserted %d entities (%d changed @ %s), %d backlinks, sync_log for %s.",
        len(entities), entities_changed, snapshot_date, len(backlinks), sources,
    )

    # 4. Route semantic data into ChromaDB ----------------------------------
    chroma_store = ChromaStore(path=chroma_path)
    jira_docs = 0
    confluence_chunks = 0
    meeting_chunks = 0
    for doc in all_docs:
        source = doc.get("source")
        if source == "jira":
            chroma_store.add_jira_description(doc)  # reads description/source_id directly
            jira_docs += 1
        elif source == "confluence":
            confluence_chunks += chroma_store.add_confluence_chunks(_confluence_to_chroma(doc))
        elif source == "meeting_notes":
            meeting_chunks += chroma_store.add_meeting_chunks(_meeting_to_chroma(doc))
    logger.info(
        "ChromaDB: %d jira docs, %d confluence chunks, %d meeting chunks.",
        jira_docs, confluence_chunks, meeting_chunks,
    )

    logger.info("Ingestion pipeline completed successfully.")
    return {
        "documents": len(all_docs),
        "entities": len(entities),
        "entities_changed": entities_changed,
        "backlinks": len(backlinks),
        "jira_docs": jira_docs,
        "confluence_chunks": confluence_chunks,
        "meeting_chunks": meeting_chunks,
        "sources": sources,
        "flagged_injections": flagged_injections,
    }


# ---------------------------------------------------------------------------
# CLI — python src/ingestion/run_pipeline.py --jira ... --conf ... --notes ...
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Ingestion orchestrator (Week 2).")
    parser.add_argument("--jira", default=DEFAULT_JIRA_PATH, help="Path to the Jira JSON file")
    parser.add_argument("--conf", default=DEFAULT_CONFLUENCE_PATH, help="Path to the Confluence JSON folder")
    parser.add_argument("--notes", default=DEFAULT_MEETING_NOTES_PATH, help="Path to the Meeting Notes folder")
    parser.add_argument("--db-path", default=DB_PATH, help="SQLite database path")
    parser.add_argument("--chroma-path", default=CHROMA_PATH, help="ChromaDB persistence path")
    args = parser.parse_args()

    if not (args.jira or args.conf or args.notes):
        parser.error("Provide at least one of --jira / --conf / --notes.")

    try:
        stats = run_pipeline(
            args.jira or None,
            args.conf or None,
            args.notes or None,
            db_path=args.db_path,
            chroma_path=args.chroma_path,
        )
    except Exception:
        logger.exception("Pipeline failed:")
        sys.exit(1)

    print("=== Ingestion complete ===")
    print(f"  documents        : {stats['documents']}")
    print(f"  entities         : {stats['entities']}")
    print(f"  backlinks        : {stats['backlinks']}")
    print(f"  jira docs        : {stats['jira_docs']}")
    print(f"  confluence chunks: {stats['confluence_chunks']}")
    print(f"  meeting chunks   : {stats['meeting_chunks']}")
    print(f"  sources          : {', '.join(stats['sources']) or '(none)'}")


if __name__ == "__main__":
    main()
