"""OpenAI Function-Calling tool definitions and dispatcher for the Report Agent.

Public surface
--------------
``TOOLS``          : ``list[dict]`` — three OpenAI function-calling schemas.
``dispatch_tool``  : router that executes a tool by name against the stores.

Each tool returns a uniform envelope::

    {"result": <payload>, "source_ids": [<citation ids>, ...]}

so the agent always knows which ``source_id`` values it may cite.  An unknown
tool name returns ``{"error": "Unknown tool"}`` instead of raising.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:  # avoid hard runtime import coupling / sys.path surprises
    from storage.chroma_store import ChromaStore
    from storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

# Default number of semantic-search hits to return per collection.
_DEFAULT_N_RESULTS = 5

# ChromaDB collection names (mirrors storage.chroma_store).
_COLLECTION_CONFLUENCE = "confluence_chunks"
_COLLECTION_MEETING = "meeting_chunks"


# ---------------------------------------------------------------------------
# OpenAI Function-Calling schemas
# ---------------------------------------------------------------------------

TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_chroma",
            "description": (
                "Semantic search across Confluence pages and Meeting Notes. "
                "Use it to find design decisions, action items, or narrative "
                "context about a topic. Returns ranked text chunks, each with a "
                "citable source_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language search query, e.g. 'ingestion pipeline architecture'.",
                    },
                    "source_filter": {
                        "type": "string",
                        "enum": ["confluence", "meeting_notes", "all"],
                        "description": (
                            "Which source to search: 'confluence' for design docs/decisions, "
                            "'meeting_notes' for action items, 'all' for both. Defaults to 'all'."
                        ),
                    },
                    "epic_filter": {
                        "type": "string",
                        "description": (
                            "Optional. Restrict Confluence hits to pages linked to a specific "
                            "Jira epic, e.g. 'AIP-1'. Leave unset to search all pages."
                        ),
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
                "Exact lookup of a task/entity by ID from the structured SQLite store. "
                "Returns the authoritative status, assignee, due_date, priority and labels. "
                "Prefer this over query_chroma whenever you need the definitive state of a task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "The task/entity ID to look up, e.g. 'AIP-123'.",
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
                "Return all tasks whose status or assignee changed between the previous day "
                "and the given date. Use it to understand what happened today."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "ISO-8601 date (YYYY-MM-DD) to diff against the previous day, e.g. '2025-05-21'.",
                    },
                },
                "required": ["date"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def dispatch_tool(
    name: str,
    args: Dict[str, Any],
    sqlite_store: "SQLiteStore",
    chroma_store: "ChromaStore",
) -> Dict[str, Any]:
    """Execute the tool *name* and return its ``{"result", "source_ids"}`` envelope.

    Parameters
    ----------
    name:
        One of ``"query_chroma"``, ``"query_sqlite"``, ``"get_daily_diff"``.
    args:
        Parsed arguments dict (typically ``json.loads(tool_call.function.arguments)``).
    sqlite_store:
        An open :class:`storage.sqlite_store.SQLiteStore`.
    chroma_store:
        An open :class:`storage.chroma_store.ChromaStore`.

    Returns
    -------
    dict
        ``{"result": ..., "source_ids": [...]}`` on success, or
        ``{"error": "Unknown tool"}`` if *name* is not recognised.
    """
    logger.debug("dispatch_tool name=%r args=%s", name, args)

    if name == "query_chroma":
        return _query_chroma(args, chroma_store)
    if name == "query_sqlite":
        return _query_sqlite(args, sqlite_store)
    if name == "get_daily_diff":
        return _get_daily_diff(args, sqlite_store)

    logger.warning("Unknown tool requested: %r", name)
    return {"error": "Unknown tool"}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _query_chroma(args: Dict[str, Any], chroma_store: "ChromaStore") -> Dict[str, Any]:
    """Semantic search over Confluence and/or Meeting Notes collections."""
    query: str = args["query"]
    source_filter: str = args.get("source_filter") or "all"
    epic_filter = args.get("epic_filter") or None
    n_results = _DEFAULT_N_RESULTS

    # Map the source_filter onto concrete collections.
    targets: List[str] = []
    if source_filter in ("confluence", "all"):
        targets.append(_COLLECTION_CONFLUENCE)
    if source_filter in ("meeting_notes", "all"):
        targets.append(_COLLECTION_MEETING)

    hits: List[Dict[str, Any]] = []
    for collection in targets:
        # Over-fetch a little when an epic_filter is set so post-filtering still
        # has enough candidates to return.
        fetch_n = n_results * 3 if (epic_filter and collection ==
                                    _COLLECTION_CONFLUENCE) else n_results
        try:
            raw = chroma_store.query(collection=collection,
                                     query_text=query, n_results=fetch_n)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("query_chroma failed on %r: %s", collection, exc)
            continue

        for hit in raw:
            meta = hit.get("metadata") or {}

            # epic_filter: linked_jira_epics is a comma-joined string in metadata,
            # so we substring-match in Python (Chroma metadata `where` has no
            # $contains operator).
            if epic_filter and collection == _COLLECTION_CONFLUENCE:
                linked = str(meta.get("linked_jira_epics") or "")
                linked_set = {e.strip() for e in linked.split(",") if e.strip()}
                if epic_filter not in linked_set:
                    continue

            source_id = (
                meta.get("page_id")
                or meta.get("note_id")
                or meta.get("source_id")
                or "UNKNOWN"
            )
            hits.append(
                {
                    "source_id": source_id,
                    "source": "confluence" if collection == _COLLECTION_CONFLUENCE else "meeting_notes",
                    "document": hit.get("document", ""),
                    "metadata": meta,
                    "distance": hit.get("distance"),
                }
            )

    # Rank by similarity (lower distance = more similar) and cap to n_results.
    hits.sort(key=lambda h: h.get("distance") if h.get("distance") is not None else 1.0)
    hits = hits[:n_results]

    source_ids = _unique([h["source_id"] for h in hits if h["source_id"] != "UNKNOWN"])
    return {"result": hits, "source_ids": source_ids}


def _query_sqlite(args: Dict[str, Any], sqlite_store: "SQLiteStore") -> Dict[str, Any]:
    """Exact entity lookup by ID."""
    entity_id: str = args["entity_id"]
    entity = sqlite_store.query_entity(entity_id)

    if entity is None:
        return {
            "result": {"found": False, "entity_id": entity_id},
            "source_ids": [],
        }

    entity = dict(entity)

    # Deserialise the JSON-serialised labels field if present.
    labels = entity.get("labels")
    if isinstance(labels, str):
        import json

        try:
            entity["labels"] = json.loads(labels)
        except (json.JSONDecodeError, TypeError):
            pass

    entity["found"] = True
    return {"result": entity, "source_ids": [entity_id]}


def _get_daily_diff(args: Dict[str, Any], sqlite_store: "SQLiteStore") -> Dict[str, Any]:
    """Day-over-day snapshot diff for the given date."""
    date_value: str = args["date"]
    rows = sqlite_store.get_daily_diff(date_value)

    enriched: List[Dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        # Surface the embedded JSON snapshots for the LLM.
        for key in ("data_today", "data_yesterday"):
            raw = entry.get(key)
            if isinstance(raw, str):
                import json

                try:
                    entry[key] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    pass
        enriched.append(entry)

    source_ids = _unique([r.get("task_id") for r in enriched if r.get("task_id")])
    return {"result": enriched, "source_ids": source_ids}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unique(items: List[Any]) -> List[Any]:
    """Return *items* with duplicates removed, preserving first-seen order."""
    seen: set = set()
    out: List[Any] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
