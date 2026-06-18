"""End-to-end tests for the ingestion orchestrator (`run_pipeline`).

Runs the full pipeline on tiny synthetic fixtures into an isolated temp SQLite
DB + temp ChromaDB (no network: Chroma uses its bundled local embedder, same as
test_chunking.py), then asserts both stores were populated correctly — including
the connector→Chroma field bridge (`text_content`→`content`, `source_id`→
`page_id`/`note_id`).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import chromadb
import pytest

from ingestion.run_pipeline import (
    run_pipeline,
    _confluence_to_chroma,
    _meeting_to_chroma,
)
from storage.chroma_store import ChromaStore
from storage.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Fixture data — one issue, one page, one meeting, with cross-references
# ---------------------------------------------------------------------------

# NOTE: payload "source" is deliberately "Apache" (like the real synthetic file)
# to guard that JiraConnector normalizes it to the canonical "jira" discriminator
# so routing/extraction still work end-to-end.
_JIRA_PAYLOAD = {
    "source": "Apache",
    "issues": [
        {
            "key": "AIP-1",
            "self": "https://jira/AIP-1",
            "fields": {
                "summary": "Build ingestion pipeline",
                "status": {"name": "In Progress"},
                "assignee": {"displayName": "Minh Tuan"},
                "priority": {"name": "High"},
                "labels": ["backend"],
                "duedate": "2025-05-24",
                "description": "Implement ingestion that feeds AIP-2 downstream.",
                "created": "2025-05-01T09:00:00.000+0000",
                "updated": "2025-05-20T09:00:00.000+0000",
            },
        }
    ],
}

_CONFLUENCE_PAYLOAD = {
    "pages": [
        {
            "page_id": "CONF-1",
            "title": "Ingestion Architecture",
            "space": "AIP",
            "author": "Minh Tuan",
            "last_updated": "2025-05-20",
            "status": "current",
            "linked_jira_epics": ["AIP-1"],
            "tags": ["architecture"],
            "content": (
                "## Context\n"
                "The pipeline ingests AIP-2 data.\n\n"
                "## Decision\n"
                "Use ChromaDB for semantic search.\n"
            ),
        }
    ]
}

_MEETING_PAYLOAD = {
    "meetings": [
        {
            "meeting_id": "MTG-2025-05-21",
            "date": "2025-05-21",
            "project": "AIP",
            "attendees": [{"name": "Minh Tuan", "role": "Tech Lead"}],
            "action_items": [
                {"jira_key": "AIP-1", "owner": "Minh Tuan", "description": "finish pipeline"}
            ],
            "content": (
                "[Attendees]\n"
                "Minh Tuan (Tech Lead)\n\n"
                "[Action Items]\n"
                "- AIP-1: Minh Tuan finish pipeline\n"
                "- AIP-3 still pending review\n"
            ),
        }
    ]
}


def _get_all(collection: "chromadb.Collection") -> list[dict]:
    """Return every stored doc as {document, metadata} dicts."""
    result = collection.get(include=["documents", "metadatas"])
    return [
        {"document": doc, "metadata": meta}
        for doc, meta in zip(result["documents"], result["metadatas"])
    ]


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory) -> dict:
    """Build fixtures and run the pipeline once; share the result across tests."""
    base = tmp_path_factory.mktemp("ingest")

    jira_path = base / "jira.json"
    jira_path.write_text(json.dumps(_JIRA_PAYLOAD), encoding="utf-8")

    conf_dir = base / "confluence"
    conf_dir.mkdir()
    (conf_dir / "pages.json").write_text(json.dumps(_CONFLUENCE_PAYLOAD), encoding="utf-8")

    notes_dir = base / "meeting"
    notes_dir.mkdir()
    (notes_dir / "notes.json").write_text(json.dumps(_MEETING_PAYLOAD), encoding="utf-8")

    db_path = str(base / "vault.db")
    chroma_path = str(base / "chroma")

    stats = run_pipeline(
        str(jira_path), str(conf_dir), str(notes_dir),
        db_path=db_path, chroma_path=chroma_path,
    )
    return {"stats": stats, "db_path": db_path, "chroma_path": chroma_path}


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_document_and_entity_counts(self, pipeline):
        stats = pipeline["stats"]
        assert stats["documents"] == 3
        assert stats["entities"] == 1          # only the Jira issue
        assert stats["backlinks"] == 4         # 2 confluence + 2 meeting

    def test_chunk_counts(self, pipeline):
        stats = pipeline["stats"]
        assert stats["jira_docs"] == 1
        assert stats["confluence_chunks"] == 2  # 2 markdown ## sections
        assert stats["meeting_chunks"] >= 1

    def test_sources(self, pipeline):
        assert pipeline["stats"]["sources"] == ["confluence", "jira", "meeting_notes"]


# ---------------------------------------------------------------------------
# SQLite routing
# ---------------------------------------------------------------------------

class TestSQLiteRouting:
    def _conn(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def test_entity_persisted(self, pipeline):
        conn = self._conn(pipeline["db_path"])
        row = conn.execute("SELECT * FROM entities WHERE task_id = 'AIP-1'").fetchone()
        conn.close()
        assert row is not None
        assert row["status"] == "In Progress"
        assert row["assignee"] == "Minh Tuan"
        assert row["title"] == "Build ingestion pipeline"

    def test_backlinks_persisted(self, pipeline):
        conn = self._conn(pipeline["db_path"])
        rows = conn.execute(
            "SELECT source_entity_id, target_entity_id, link_type FROM backlinks"
        ).fetchall()
        conn.close()
        triples = {(r["source_entity_id"], r["target_entity_id"], r["link_type"]) for r in rows}
        assert len(rows) == 4
        assert ("CONF-1", "AIP-1", "mentions") in triples       # linked epic
        assert ("CONF-1", "AIP-2", "mentions") in triples       # inline mention
        assert ("MTG-2025-05-21", "AIP-1", "action_item") in triples
        assert ("MTG-2025-05-21", "AIP-3", "mentions") in triples

    def test_snapshot_saved_for_today(self, pipeline):
        conn = self._conn(pipeline["db_path"])
        rows = conn.execute(
            "SELECT task_id, snapshot_date FROM snapshots WHERE task_id = 'AIP-1'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["snapshot_date"] == date.today().isoformat()

    def test_sync_log_per_source(self, pipeline):
        conn = self._conn(pipeline["db_path"])
        sources = {r["source"] for r in conn.execute("SELECT source FROM sync_log").fetchall()}
        conn.close()
        assert sources == {"jira", "confluence", "meeting_notes"}


# ---------------------------------------------------------------------------
# ChromaDB routing — proves the field bridge actually indexes chunks
# ---------------------------------------------------------------------------

class TestChromaRouting:
    def test_jira_description_indexed(self, pipeline):
        store = ChromaStore(path=pipeline["chroma_path"])
        docs = _get_all(store._jira)
        assert len(docs) == 1
        assert docs[0]["metadata"]["source_id"] == "AIP-1"
        assert docs[0]["metadata"]["source"] == "jira"

    def test_confluence_chunks_indexed_with_bridged_page_id(self, pipeline):
        store = ChromaStore(path=pipeline["chroma_path"])
        chunks = _get_all(store._confluence)
        assert len(chunks) == 2, "field bridge must map text_content→content so chunks index"
        # source_id → page_id bridge
        assert all(c["metadata"]["page_id"] == "CONF-1" for c in chunks)
        assert {c["metadata"]["section"] for c in chunks} == {"Context", "Decision"}

    def test_meeting_chunks_indexed_with_bridged_note_id(self, pipeline):
        store = ChromaStore(path=pipeline["chroma_path"])
        chunks = _get_all(store._meeting)
        assert len(chunks) >= 1, "field bridge must map text_content→content so chunks index"
        # source_id → note_id bridge
        assert all(c["metadata"]["note_id"] == "MTG-2025-05-21" for c in chunks)
        assert all(c["metadata"]["project"] == "AIP" for c in chunks)


# ---------------------------------------------------------------------------
# Field-bridge unit tests (fast, no stores)
# ---------------------------------------------------------------------------

class TestFieldBridges:
    def test_confluence_bridge_renames_fields(self):
        doc = {
            "source": "confluence", "source_id": "CONF-9", "text_content": "body",
            "space": "S", "author": "A", "last_updated": "2025-01-01",
            "status": "current", "linked_jira_epics": ["AIP-1"],
        }
        out = _confluence_to_chroma(doc)
        assert out["content"] == "body"           # text_content → content
        assert out["page_id"] == "CONF-9"          # source_id → page_id
        assert out["linked_jira_epics"] == ["AIP-1"]

    def test_meeting_bridge_renames_fields(self):
        doc = {
            "source": "meeting_notes", "source_id": "MTG-1", "text_content": "body",
            "date": "2025-05-21", "project": "AIP",
        }
        out = _meeting_to_chroma(doc)
        assert out["content"] == "body"            # text_content → content
        assert out["note_id"] == "MTG-1"           # source_id → note_id
        assert out["date"] == "2025-05-21"


# ---------------------------------------------------------------------------
# Edge case
# ---------------------------------------------------------------------------

def test_run_pipeline_with_no_sources_is_a_noop(tmp_path):
    """No source paths → empty stats, DB still initialised (no crash)."""
    stats = run_pipeline(
        None, None, None,
        db_path=str(tmp_path / "empty.db"),
        chroma_path=str(tmp_path / "empty_chroma"),
    )
    assert stats["documents"] == 0
    assert stats["entities"] == 0
    assert stats["sources"] == []


# ---------------------------------------------------------------------------
# Idempotent re-ingest — the MCP /ingest path reuses the stores without a reset
# ---------------------------------------------------------------------------

def test_reingest_is_idempotent(tmp_path):
    """Running ingest twice on the SAME stores must not accumulate duplicates:
    deterministic Chroma chunk ids (upsert) and the UNIQUE(task_id, snapshot_date)
    snapshot index keep store contents stable. Guards the MCP `/ingest` path,
    which (unlike `run_agent.sh`) does not delete the store between runs."""
    jira_path = tmp_path / "jira.json"
    jira_path.write_text(json.dumps(_JIRA_PAYLOAD), encoding="utf-8")
    conf_dir = tmp_path / "confluence"; conf_dir.mkdir()
    (conf_dir / "pages.json").write_text(json.dumps(_CONFLUENCE_PAYLOAD), encoding="utf-8")
    notes_dir = tmp_path / "meeting"; notes_dir.mkdir()
    (notes_dir / "notes.json").write_text(json.dumps(_MEETING_PAYLOAD), encoding="utf-8")

    db_path = str(tmp_path / "vault.db")
    chroma_path = str(tmp_path / "chroma")

    def _run():
        return run_pipeline(
            str(jira_path), str(conf_dir), str(notes_dir),
            db_path=db_path, chroma_path=chroma_path,
        )

    first = _run()
    _run()  # second ingest onto the same, un-reset stores

    # Chroma: total stored chunks equal a single run's output (no duplication).
    store = ChromaStore(path=chroma_path)
    assert len(_get_all(store._confluence)) == first["confluence_chunks"]
    assert len(_get_all(store._meeting)) == first["meeting_chunks"]
    assert len(_get_all(store._jira)) == 1

    # SQLite: still exactly one snapshot per task for the day.
    conn = sqlite3.connect(db_path)
    snaps = conn.execute(
        "SELECT COUNT(*) FROM snapshots WHERE task_id = 'AIP-1'"
    ).fetchone()[0]
    conn.close()
    assert snaps == 1, "re-ingest must not duplicate the per-day snapshot"


# ---------------------------------------------------------------------------
# Incremental day-over-day diff — baseline then a real change
# ---------------------------------------------------------------------------

def test_incremental_diff_surfaces_real_changes(tmp_path):
    """Ingest a baseline at D-1, then a genuine status change at D into the SAME
    store → get_daily_diff(D) surfaces exactly the changed task (delta-only
    snapshots tagged by as_of + meaningful-field diff). No fake seeding."""
    jira = tmp_path / "jira.json"
    db = str(tmp_path / "vault.db")
    chroma = str(tmp_path / "chroma")

    # Baseline @ 2025-05-29
    jira.write_text(json.dumps(_JIRA_PAYLOAD), encoding="utf-8")
    run_pipeline(str(jira), None, None, db_path=db, chroma_path=chroma, as_of="2025-05-29")

    # AIP-1 transitions In Progress → Done; re-ingest @ 2025-05-30.
    mutated = json.loads(json.dumps(_JIRA_PAYLOAD))
    mutated["issues"][0]["fields"]["status"]["name"] = "Done"
    mutated["issues"][0]["fields"]["updated"] = "2025-05-30T09:00:00.000+0000"
    jira.write_text(json.dumps(mutated), encoding="utf-8")
    stats = run_pipeline(str(jira), None, None, db_path=db, chroma_path=chroma, as_of="2025-05-30")

    assert stats["entities_changed"] == 1

    with SQLiteStore(db_path=db) as store:
        diff = store.get_daily_diff("2025-05-30")

    assert len(diff) == 1, "only the one changed task should surface"
    assert diff[0]["task_id"] == "AIP-1"
    assert diff[0]["previous_date"] == "2025-05-29"
    assert diff[0]["changes"]["status"] == ["In Progress", "Done"]


def test_unchanged_reingest_produces_no_diff(tmp_path):
    """Re-ingesting identical data at a later date writes no new snapshots, so the
    diff is honestly empty (the demo's single static dataset behaves this way)."""
    jira = tmp_path / "jira.json"
    db = str(tmp_path / "vault.db")
    chroma = str(tmp_path / "chroma")
    jira.write_text(json.dumps(_JIRA_PAYLOAD), encoding="utf-8")

    run_pipeline(str(jira), None, None, db_path=db, chroma_path=chroma, as_of="2025-05-29")
    stats = run_pipeline(str(jira), None, None, db_path=db, chroma_path=chroma, as_of="2025-05-30")

    assert stats["entities_changed"] == 0
    with SQLiteStore(db_path=db) as store:
        assert store.get_daily_diff("2025-05-30") == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
