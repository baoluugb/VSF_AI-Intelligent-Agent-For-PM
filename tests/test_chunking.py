"""Tests for ChromaStore chunking logic (all in-memory, no API key needed)."""
from __future__ import annotations

import chromadb
import pytest

from src.storage.chroma_store import ChromaStore


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path) -> ChromaStore:
    """Fresh, fully isolated ChromaStore for each test.

    ``chromadb.EphemeralClient()`` shares a single process-wide in-memory
    server, so consecutive tests contaminate each other's collections.
    Using a per-test ``tmp_path`` with ``PersistentClient`` gives genuine
    isolation while still requiring no API key or network access.
    """
    return ChromaStore(path=str(tmp_path / "chroma"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_all(collection: chromadb.Collection) -> list[dict]:
    """Return every stored document as a list of {document, metadata} dicts."""
    result = collection.get(include=["documents", "metadatas"])
    return [
        {"document": doc, "metadata": meta}
        for doc, meta in zip(result["documents"], result["metadatas"])
    ]


# ---------------------------------------------------------------------------
# add_confluence_chunks
# ---------------------------------------------------------------------------

class TestAddConfluenceChunks:
    """Chunking behaviour for Confluence pages."""

    # Craft a page with exactly 3 short `##` sections so each stays well
    # under CHUNK_SIZE_CONFLUENCE (600 chars) and is never split further.
    _PAGE = {
        "page_id": "CONF-42",
        "space": "Engineering",
        "author": "alice",
        "last_updated": "2025-05-01",
        "status": "current",
        "linked_jira_epics": ["AIP-10", "AIP-20"],
        "content": (
            "## Context\n"
            "This change replaces the legacy pipeline with the new ingestion engine.\n\n"
            "## Decision\n"
            "We adopted the event-driven approach after evaluating three alternatives.\n\n"
            "## Status\n"
            "Implementation is complete and ready for review.\n"
        ),
    }

    def test_chunk_count_equals_number_of_sections(self, store: ChromaStore) -> None:
        """One `##` section → one chunk when content is short."""
        n = store.add_confluence_chunks(self._PAGE)
        assert n == 3, f"Expected 3 chunks (one per ##-section), got {n}."

    def test_each_chunk_has_correct_section_metadata(self, store: ChromaStore) -> None:
        """Every chunk must carry the section name from its `##` header."""
        store.add_confluence_chunks(self._PAGE)
        chunks = _get_all(store._confluence)

        sections = {c["metadata"]["section"] for c in chunks}
        assert sections == {"Context", "Decision", "Status"}, (
            f"Unexpected section values: {sections}"
        )

    def test_page_level_metadata_is_present_on_all_chunks(self, store: ChromaStore) -> None:
        """page_id, space, author, status and linked_jira_epics must be on every chunk."""
        store.add_confluence_chunks(self._PAGE)
        chunks = _get_all(store._confluence)

        for chunk in chunks:
            meta = chunk["metadata"]
            assert meta["page_id"] == "CONF-42"
            assert meta["space"] == "Engineering"
            assert meta["author"] == "alice"
            assert meta["status"] == "current"
            assert meta["linked_jira_epics"] == "AIP-10,AIP-20"

    def test_empty_content_adds_zero_chunks(self, store: ChromaStore) -> None:
        """Pages with no content must be silently skipped."""
        empty_page = {**self._PAGE, "content": "   "}
        n = store.add_confluence_chunks(empty_page)
        assert n == 0


# ---------------------------------------------------------------------------
# add_meeting_chunks
# ---------------------------------------------------------------------------

class TestAddMeetingChunks:
    """Chunking behaviour for meeting notes."""

    _NOTE = {
        "meeting_id": "MTG-STORM-20250528",
        "date": "2025-05-28",
        "project": "STORM",
        "content": (
            "[Attendees]\n"
            "Sarah Lee (PM), Dan Davis (Dev), Alex Kim (QA)\n\n"
            "[Action Items]\n"
            "- Dan: fix thread-pool deadlock by Friday\n"
            "- Alex: add regression test for AIP-99\n"
            "- Sarah: update stakeholder slide deck\n"
        ),
    }

    def test_chunks_are_produced(self, store: ChromaStore) -> None:
        """At least one chunk must be added for a non-empty note."""
        n = store.add_meeting_chunks(self._NOTE)
        assert n >= 1, f"Expected ≥1 chunk, got {n}."

    def test_note_id_metadata_is_stored(self, store: ChromaStore) -> None:
        """Every chunk must carry the meeting's note_id."""
        store.add_meeting_chunks(self._NOTE)
        chunks = _get_all(store._meeting)

        for chunk in chunks:
            assert chunk["metadata"]["note_id"] == "MTG-STORM-20250528", (
                f"Wrong note_id: {chunk['metadata']['note_id']!r}"
            )

    def test_date_and_project_metadata_is_stored(self, store: ChromaStore) -> None:
        """date and project must be present on every chunk."""
        store.add_meeting_chunks(self._NOTE)
        chunks = _get_all(store._meeting)

        for chunk in chunks:
            assert chunk["metadata"]["date"] == "2025-05-28"
            assert chunk["metadata"]["project"] == "STORM"

    def test_note_id_falls_back_to_meeting_id_key(self, store: ChromaStore) -> None:
        """The connector emits 'meeting_id'; confirm it is stored as 'note_id'."""
        # Build a note that only has 'meeting_id' (no explicit 'note_id').
        note = {
            "meeting_id": "MTG-ALIAS-001",
            "date": "2025-06-01",
            "project": "ALIAS",
            "content": "Short standalone meeting note for alias test.",
        }
        store.add_meeting_chunks(note)
        chunks = _get_all(store._meeting)
        assert chunks, "Expected at least one chunk to be stored."
        assert all(c["metadata"]["note_id"] == "MTG-ALIAS-001" for c in chunks), (
            f"note_id mismatch: {[c['metadata']['note_id'] for c in chunks]}"
        )

    def test_empty_content_adds_zero_chunks(self, store: ChromaStore) -> None:
        """Notes with no content must be silently skipped."""
        n = store.add_meeting_chunks({**self._NOTE, "content": ""})
        assert n == 0


# ---------------------------------------------------------------------------
# add_jira_description
# ---------------------------------------------------------------------------

class TestAddJiraDescription:
    """Single-document upsert behaviour for Jira descriptions."""

    _ENTITY = {
        "source_id": "AIP-1",
        "description": "Replace deprecated concern-engine API calls across the board.",
        "status": "Blocked",
        "assignee": "Jane Doe",
    }

    def test_description_is_stored_as_single_document(self, store: ChromaStore) -> None:
        """Exactly one document must be present after a single upsert."""
        store.add_jira_description(self._ENTITY)
        result = store._jira.get(include=["documents", "metadatas"])
        assert len(result["ids"]) == 1

    def test_source_id_metadata_is_correct(self, store: ChromaStore) -> None:
        """source_id in metadata must match the entity's source_id."""
        store.add_jira_description(self._ENTITY)
        result = store._jira.get(include=["metadatas"])
        meta = result["metadatas"][0]
        assert meta["source_id"] == "AIP-1", (
            f"Expected source_id='AIP-1', got {meta['source_id']!r}"
        )

    def test_status_and_assignee_metadata(self, store: ChromaStore) -> None:
        """status and assignee must be stored on the document."""
        store.add_jira_description(self._ENTITY)
        result = store._jira.get(include=["metadatas"])
        meta = result["metadatas"][0]
        assert meta["status"] == "Blocked"
        assert meta["assignee"] == "Jane Doe"

    def test_source_tag_is_jira(self, store: ChromaStore) -> None:
        """The 'source' metadata field must always be set to 'jira'."""
        store.add_jira_description(self._ENTITY)
        result = store._jira.get(include=["metadatas"])
        assert result["metadatas"][0]["source"] == "jira"

    def test_upsert_is_idempotent(self, store: ChromaStore) -> None:
        """Calling add_jira_description twice with the same source_id must not
        create duplicate documents."""
        store.add_jira_description(self._ENTITY)
        store.add_jira_description(self._ENTITY)
        result = store._jira.get(include=["documents"])
        assert len(result["ids"]) == 1, (
            f"Expected 1 doc after two upserts, got {len(result['ids'])}."
        )

    def test_empty_description_is_skipped(self, store: ChromaStore) -> None:
        """Entities with no description must produce zero documents."""
        store.add_jira_description({**self._ENTITY, "description": ""})
        result = store._jira.get()
        assert len(result["ids"]) == 0
