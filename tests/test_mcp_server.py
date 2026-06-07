"""Tests for the MCP server (Week 5 §5.1) — FastAPI front-end over the CLIs.

No real DB/Chroma/LLM calls: ``run_pipeline``, ``ConcernEngine`` and
``generate_grounded_report`` are mocked at the ``mcp.server`` import sites
(mirroring the mocking style of ``test_report_agent.py``), and ``SQLiteStore``
is replaced with a fake context manager so no ``data/vault.db`` is required.

Scenarios
---------
1. Auth: missing / wrong / unconfigured ``X-API-Key`` -> 401 / 500.
2. ``POST /ingest``: runs the pipeline with an ``InputSanitizer`` wired in and
   returns its stats (V5.3 — guardrail wiring on ingest).
3. ``GET /report``: grounds the Report Agent in Concern Engine findings and
   returns the cited Markdown.
4. ``GET /concerns``: returns the deterministic risk list filtered by
   ``min_sev``.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# fastapi is an optional dependency; skip this whole module (rather than abort
# the entire test-suite collection) if it isn't installed in the active env.
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import mcp.server as mcp_server
from guardrail.sanitizer import InputSanitizer
from mcp.server import app

TEST_KEY = "test-mcp-key-12345"


def _auth_headers(key: str = TEST_KEY) -> dict:
    return {"X-API-Key": key}


def _store_context_manager(instance: MagicMock | None = None) -> MagicMock:
    """Build a ``MagicMock`` that behaves like ``with SQLiteStore(...) as x``."""
    instance = instance if instance is not None else MagicMock()
    cls = MagicMock()
    cls.return_value.__enter__.return_value = instance
    cls.return_value.__exit__.return_value = False
    return cls


@pytest.fixture
def client():
    """A TestClient with a known MCP_API_KEY patched into the server module."""
    with patch.object(mcp_server, "MCP_API_KEY", TEST_KEY):
        yield TestClient(app)


# ---------------------------------------------------------------------------
# Auth (every endpoint is gated by `require_api_key`)
# ---------------------------------------------------------------------------

class TestAuth:
    def test_missing_api_key_is_rejected(self, client):
        resp = client.get("/concerns")
        assert resp.status_code == 401

    def test_wrong_api_key_is_rejected(self, client):
        resp = client.get("/concerns", headers=_auth_headers("totally-wrong-key"))
        assert resp.status_code == 401

    def test_unconfigured_server_key_fails_closed(self):
        """An empty MCP_API_KEY must refuse every request, not allow them through."""
        with patch.object(mcp_server, "MCP_API_KEY", ""):
            resp = TestClient(app).get("/concerns", headers=_auth_headers("any-key-at-all"))
        assert resp.status_code == 500

    def test_correct_api_key_is_accepted(self, client):
        with patch.object(mcp_server, "SQLiteStore", _store_context_manager()), \
             patch.object(mcp_server, "_get_chroma_store", return_value=MagicMock()), \
             patch.object(mcp_server, "ConcernEngine") as mock_engine_cls:
            mock_engine_cls.return_value.run_all_rules.return_value = []
            resp = client.get("/concerns", headers=_auth_headers())
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /ingest
# ---------------------------------------------------------------------------

class TestIngest:
    def test_ingest_runs_pipeline_with_sanitizer_and_returns_stats(self, client):
        fake_stats = {
            "documents": 10,
            "entities": 5,
            "backlinks": 2,
            "jira_docs": 3,
            "confluence_chunks": 4,
            "meeting_chunks": 1,
            "sources": ["confluence", "jira", "meeting_notes"],
            "flagged_injections": 1,
        }
        with patch.object(mcp_server, "SQLiteStore", _store_context_manager()), \
             patch.object(mcp_server, "run_pipeline", return_value=fake_stats) as mock_run:
            resp = client.post("/ingest", headers=_auth_headers())

        assert resp.status_code == 200
        assert resp.json() == fake_stats

        # The guardrail (Week 5 §5.2) must be wired into ingestion (V5.3).
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert isinstance(kwargs.get("sanitizer"), InputSanitizer)

    def test_ingest_accepts_custom_source_paths(self, client):
        fake_stats = {
            "documents": 0, "entities": 0, "backlinks": 0, "jira_docs": 0,
            "confluence_chunks": 0, "meeting_chunks": 0, "sources": [],
            "flagged_injections": 0,
        }
        with patch.object(mcp_server, "SQLiteStore", _store_context_manager()), \
             patch.object(mcp_server, "run_pipeline", return_value=fake_stats) as mock_run:
            resp = client.post(
                "/ingest",
                headers=_auth_headers(),
                json={"jira_path": "data/custom/jira.json"},
            )

        assert resp.status_code == 200
        args, _ = mock_run.call_args
        assert args[0] == "data/custom/jira.json"

    def test_ingest_requires_api_key(self):
        with patch.object(mcp_server, "MCP_API_KEY", TEST_KEY):
            resp = TestClient(app).post("/ingest")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /report
# ---------------------------------------------------------------------------

class TestReport:
    def test_report_is_grounded_in_concerns_and_returned(self, client):
        concerns = [
            {
                "type": "stalled_task",
                "task_id": "AIP-1",
                "severity": 4,
                "explanation": "Task chưa có update trong 9 ngày.",
                "source_ids": ["AIP-1"],
            }
        ]
        with patch.object(mcp_server, "SQLiteStore", _store_context_manager()), \
             patch.object(mcp_server, "_get_chroma_store", return_value=MagicMock()), \
             patch.object(mcp_server, "ConcernEngine") as mock_engine_cls, \
             patch.object(mcp_server, "generate_grounded_report", return_value="## Overview\nAll quiet [AIP-1].") as mock_generate:
            mock_engine_cls.return_value.run_all_rules.return_value = concerns

            resp = client.get("/report", params={"date": "2025-05-30"}, headers=_auth_headers())

        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "date": "2025-05-30",
            "report": "## Overview\nAll quiet [AIP-1].",
            "concern_count": 1,
        }
        # Grounding: the computed concerns must be handed to the report generator.
        mock_generate.assert_called_once()
        passed_concerns = mock_generate.call_args.args[1]
        assert passed_concerns == concerns

    def test_report_requires_date_query_param(self, client):
        resp = client.get("/report", headers=_auth_headers())
        assert resp.status_code == 422  # FastAPI validation error — missing required query param


# ---------------------------------------------------------------------------
# GET /concerns
# ---------------------------------------------------------------------------

class TestConcerns:
    _CONCERNS = [
        {"type": "cross_source_conflict", "task_id": "AIP-5", "severity": 5,
         "explanation": "Jira đánh dấu Done nhưng tài liệu khác vẫn ghi nhận đang pending.",
         "source_ids": ["AIP-5", "MTG-1"]},
        {"type": "stalled_task", "task_id": "AIP-9", "severity": 2,
         "explanation": "Task chưa có update trong 4 ngày.", "source_ids": ["AIP-9"]},
    ]

    def test_concerns_filters_by_min_severity(self, client):
        with patch.object(mcp_server, "SQLiteStore", _store_context_manager()), \
             patch.object(mcp_server, "_get_chroma_store", return_value=MagicMock()), \
             patch.object(mcp_server, "ConcernEngine") as mock_engine_cls:
            mock_engine_cls.return_value.run_all_rules.return_value = self._CONCERNS

            resp = client.get("/concerns", params={"min_sev": 3}, headers=_auth_headers())

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["task_id"] == "AIP-5"

    def test_concerns_default_min_sev_returns_everything(self, client):
        with patch.object(mcp_server, "SQLiteStore", _store_context_manager()), \
             patch.object(mcp_server, "_get_chroma_store", return_value=MagicMock()), \
             patch.object(mcp_server, "ConcernEngine") as mock_engine_cls:
            mock_engine_cls.return_value.run_all_rules.return_value = self._CONCERNS

            resp = client.get("/concerns", headers=_auth_headers())

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_concerns_passes_as_of_date_to_engine(self, client):
        with patch.object(mcp_server, "SQLiteStore", _store_context_manager()), \
             patch.object(mcp_server, "_get_chroma_store", return_value=MagicMock()), \
             patch.object(mcp_server, "ConcernEngine") as mock_engine_cls:
            mock_engine_cls.return_value.run_all_rules.return_value = []

            client.get("/concerns", params={"date": "2025-05-30"}, headers=_auth_headers())

        mock_engine_cls.assert_called_once_with(as_of="2025-05-30")
