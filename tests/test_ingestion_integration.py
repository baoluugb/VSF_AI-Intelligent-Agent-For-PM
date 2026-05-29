"""Integration tests: JiraConnector → SQLiteStore (in-memory SQLite)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.ingestion.jira_connector import JiraConnector
from src.storage.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_PATH: Path = (
    Path(__file__).resolve().parents[1] / "data" / "jira" / "jira_synthetic_AIP.json"
)

_DDL = """
CREATE TABLE IF NOT EXISTS entities (
    task_id     TEXT PRIMARY KEY,
    source      TEXT,
    title       TEXT,
    status      TEXT,
    assignee    TEXT,
    priority    TEXT,
    due_date    TEXT,
    labels      TEXT,
    description TEXT,
    url         TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
"""


def _make_in_memory_store() -> SQLiteStore:
    """Return a SQLiteStore backed by ':memory:' with the entities table created."""
    store = SQLiteStore(db_path=":memory:")
    conn = store._ensure_connection()
    conn.executescript(_DDL)
    conn.commit()
    return store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def loaded_entities():
    """Load and return all normalised entities from the synthetic Jira file."""
    connector = JiraConnector(str(FIXTURE_PATH))
    return connector.load()


@pytest.fixture(scope="module")
def populated_store(loaded_entities):
    """Bulk-upsert all entities into an in-memory store and return it."""
    store = _make_in_memory_store()
    store.bulk_upsert(loaded_entities)
    return store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestIngestionIntegration:
    def test_total_entity_count_matches_file(self, populated_store, loaded_entities):
        """Row count in DB must equal the number of issues in the JSON file."""
        conn = populated_store._ensure_connection()
        (count,) = conn.execute("SELECT COUNT(*) FROM entities").fetchone()
        assert count == len(loaded_entities), (
            f"Expected {len(loaded_entities)} rows in DB, got {count}."
        )

    def test_query_aip1_returns_correct_title(self, populated_store):
        """query_entity('AIP-1') must return the entity with its exact title."""
        entity = populated_store.query_entity("AIP-1")

        assert entity is not None, "AIP-1 was not found in the database."
        assert entity["task_id"] == "AIP-1"
        assert entity["title"] == "Replace deprecated concern-engine API calls", (
            f"Unexpected title for AIP-1: {entity['title']!r}"
        )

    def test_done_status_entity_persisted_correctly(self, populated_store):
        """At least one entity with status 'Done' must be saved and retrievable."""
        conn = populated_store._ensure_connection()
        cursor = conn.execute(
            "SELECT task_id, title, status FROM entities WHERE status = 'Done' LIMIT 1"
        )
        row = cursor.fetchone()

        assert row is not None, "No entity with status 'Done' was found in the DB."
        assert dict(row)["status"] == "Done", (
            f"Expected status 'Done', got {dict(row)['status']!r}."
        )
        # Cross-check: query_entity round-trip for the same task_id
        task_id = dict(row)["task_id"]
        entity = populated_store.query_entity(task_id)
        assert entity is not None
        assert entity["status"] == "Done"
