"""OpenAI Function-Calling tool definitions and dispatcher for the Report Agent.

Three tools are exposed to the LLM:
  - query_chroma    : semantic search over Confluence & Meeting Notes in ChromaDB
  - query_sqlite    : exact lookup of a single entity by task_id in SQLite
  - get_daily_diff  : day-over-day snapshot diff from SQLite
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from storage.chroma_store import ChromaStore
from storage.sqlite_store import SQLiteStore
from config import CHROMA_PATH, DB_PATH

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenAI Function-Calling schemas
# ---------------------------------------------------------------------------

TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_chroma",
            "description": (
                "Semantic search across Confluence pages and Meeting Notes stored in ChromaDB. "
                "Use this to find relevant design decisions, action items, or context about a topic. "
                "Returns up to 5 ranked text chunks with their source metadata."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language search query, e.g. 'ingestion pipeline architecture'",
                    },
                    "source_filter": {
                        "type": "string",
                        "enum": ["confluence", "meeting_notes", "all"],
                        "description": (
                            "Which source to search. Use 'confluence' for design docs/decisions, "
                            "'meeting_notes' for action items and meeting records, 'all' for both."
                        ),
                    },
                    "epic_filter": {
                        "type": "string",
                        "description": (
                            "Optional: restrict Confluence search to pages linked to a specific Jira epic. "
                            "Example: 'AIP-1'. Leave empty to search all pages."
                        ),
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "Maximum number of chunks to return (default: 5, max: 10).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_sqlite",
            "description": (
                "Exact lookup of a task/entity by its ID from the structured SQLite store. "
                "Returns authoritative status, assignee, due_date, priority, and labels for the entity. "
                "Always use this (not query_chroma) when you need the definitive current state of a task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "The task or entity ID to look up, e.g. 'AIP-123'.",
                    },
                },
                "required": ["entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_daily_diff",
            "description": (
                "Retrieve all tasks whose status or assignee changed between yesterday and the given date. "
                "Use this to understand what happened today — which tasks moved forward, got reassigned, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": (
                            "ISO-8601 date (YYYY-MM-DD) for which to compute the diff vs. the previous day. "
                            "Example: '2025-05-21'."
                        ),
                    },
                },
                "required": ["date"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatcher — routes LLM tool calls to actual storage backends
# ---------------------------------------------------------------------------

def dispatch_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    sqlite_store: SQLiteStore | None = None,
    chroma_store: ChromaStore | None = None,
) -> Any:
    """Execute the named tool and return a JSON-serialisable result.

    Parameters
    ----------
    tool_name:
        One of ``"query_chroma"``, ``"query_sqlite"``, ``"get_daily_diff"``.
    arguments:
        The parsed arguments dict coming from ``json.loads(tc.function.arguments)``.
    sqlite_store:
        An open :class:`~storage.sqlite_store.SQLiteStore` instance.  When
        ``None`` a fresh instance is created for the call (slower but safe for
        one-off calls).
    chroma_store:
        An open :class:`~storage.chroma_store.ChromaStore` instance.  When
        ``None`` a fresh instance is created using :data:`config.CHROMA_PATH`.

    Returns
    -------
    Any
        A JSON-serialisable value (list, dict, or str) that will be returned to
        the LLM as the tool result content.

    Raises
    ------
    ValueError
        If *tool_name* is not recognised.
    """
    logger.debug("Dispatching tool=%r args=%s", tool_name, json.dumps(arguments))

    if tool_name == "query_chroma":
        return _query_chroma(arguments, chroma_store=chroma_store)
    if tool_name == "query_sqlite":
        return _query_sqlite(arguments, sqlite_store=sqlite_store)
    if tool_name == "get_daily_diff":
        return _get_daily_diff(arguments, sqlite_store=sqlite_store)

    raise ValueError(f"Unknown tool: {tool_name!r}")


# ---------------------------------------------------------------------------
# Internal handlers
# ---------------------------------------------------------------------------

def _query_chroma(
    args: Dict[str, Any],
    *,
    chroma_store: ChromaStore | None,
) -> List[Dict[str, Any]]:
    """Handle query_chroma tool call."""
    query: str = args["query"]
    source_filter: str = args.get("source_filter", "all")
    epic_filter: str | None = args.get("epic_filter") or None
    n_results: int = min(int(args.get("n_results") or 5), 10)

    store = chroma_store or ChromaStore(path=CHROMA_PATH)

    results: List[Dict[str, Any]] = []

    # Determine which collections to search
    collections_to_search: List[str] = []
    if source_filter in ("confluence", "all"):
        collections_to_search.append("confluence_chunks")
    if source_filter in ("meeting_notes", "all"):
        collections_to_search.append("meeting_chunks")

    for collection in collections_to_search:
        where: Dict[str, Any] | None = None
        if epic_filter and collection == "confluence_chunks":
            # ChromaDB metadata filter: linked_jira_epics is stored as comma-separated string.
            # Use $contains for substring match.
            where = {"linked_jira_epics": {"$contains": epic_filter}}

        try:
            hits = store.query(
                collection=collection,
                query_text=query,
                n_results=n_results,
                where=where,
            )
            for hit in hits:
                meta = hit.get("metadata") or {}
                # Normalise source_id for citation
                source_id = (
                    meta.get("page_id")
                    or meta.get("note_id")
                    or meta.get("source_id")
                    or "UNKNOWN"
                )
                results.append(
                    {
                        "source_id": source_id,
                        "source": "confluence" if collection == "confluence_chunks" else "meeting_notes",
                        "document": hit.get("document", ""),
                        "metadata": meta,
                        "distance": hit.get("distance"),
                    }
                )
        except Exception as exc:  # pragma: no cover
            logger.warning("query_chroma error on collection=%r: %s", collection, exc)

    # Sort by distance (lower is more similar) and deduplicate
    results.sort(key=lambda r: r.get("distance") or 1.0)
    return results[:n_results]


def _query_sqlite(
    args: Dict[str, Any],
    *,
    sqlite_store: SQLiteStore | None,
) -> Dict[str, Any]:
    """Handle query_sqlite tool call."""
    entity_id: str = args["entity_id"]
    store = sqlite_store or SQLiteStore(db_path=DB_PATH)

    entity = store.query_entity(entity_id)
    if entity is None:
        return {"found": False, "entity_id": entity_id, "message": f"Entity '{entity_id}' not found in SQLite."}

    # Deserialise JSON-serialised labels field if present
    if isinstance(entity.get("labels"), str):
        try:
            entity["labels"] = json.loads(entity["labels"])
        except (json.JSONDecodeError, TypeError):
            pass

    entity["found"] = True
    entity["source_id"] = entity_id  # always expose for citation
    return dict(entity)


def _get_daily_diff(
    args: Dict[str, Any],
    *,
    sqlite_store: SQLiteStore | None,
) -> List[Dict[str, Any]]:
    """Handle get_daily_diff tool call."""
    date_value: str = args["date"]
    store = sqlite_store or SQLiteStore(db_path=DB_PATH)

    diffs = store.get_daily_diff(date_value)

    # Enrich each diff row with parsed JSON fields for the LLM
    enriched: List[Dict[str, Any]] = []
    for row in diffs:
        entry = dict(row)
        for key in ("data_today", "data_yesterday"):
            raw = entry.get(key)
            if isinstance(raw, str):
                try:
                    entry[key] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    pass
        enriched.append(entry)

    return enriched