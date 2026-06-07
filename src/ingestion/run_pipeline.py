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
from typing import Any, Dict, List, Optional

from config import CHROMA_PATH, DB_PATH
from guardrail.sanitizer import InputSanitizer
from ingestion.confluence_connector import ConfluenceConnector
from ingestion.entity_extractor import EntityExtractor
from ingestion.jira_connector import JiraConnector
from ingestion.meeting_notes_connector import MeetingNotesConnector
from storage.chroma_store import ChromaStore
from storage.init_db import init_db
from storage.sqlite_store import SQLiteStore

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

    Returns
    -------
    dict
        Summary stats: ``documents``, ``entities``, ``backlinks``,
        ``jira_docs``, ``confluence_chunks``, ``meeting_chunks``, ``sources``,
        ``flagged_injections``.
    """
    logger.info("Initializing database at %s", db_path)
    init_db(db_path)

    # 1. Load normalized docs from each source ------------------------------
    all_docs: List[Dict[str, Any]] = []
    if jira_path:
        logger.info("Loading Jira from %s", jira_path)
        all_docs.extend(JiraConnector(jira_path).load())
    if conf_path:
        logger.info("Loading Confluence from %s", conf_path)
        all_docs.extend(ConfluenceConnector(conf_path).load())
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
    with SQLiteStore(db_path=db_path) as sqlite_store:
        sqlite_store.bulk_upsert(entities)

        # One snapshot per entity for today's date (powers get_daily_diff).
        for entity in entities:
            task_id = entity.get("task_id") or entity.get("source_id")
            if task_id:
                sqlite_store.save_snapshot(task_id, entity, None)

        sqlite_store.insert_backlinks(backlinks)
        for source in sources:
            sqlite_store.update_sync_log(source)
    logger.info("SQLite: upserted %d entities, %d backlinks, sync_log for %s.",
                len(entities), len(backlinks), sources)

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
