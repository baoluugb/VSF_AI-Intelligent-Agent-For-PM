"""Integration test for the daily (incremental) run path — the value behind
``scripts/daily_run.sh``.

``test_sqlite_store.py`` already covers ``get_daily_diff`` with hand-written
snapshots. What it does *not* cover is that running the **ingestion pipeline**
across two ``as_of`` dates — without resetting the store — accumulates snapshots
correctly so the day-over-day diff is real. Demos use ``run_agent.sh --reset``
(single snapshot → always-empty "Recent Changes"); this proves the non-reset
daily path works.

Uses a 1-issue Jira fixture into an isolated temp SQLite + temp ChromaDB (no
network: Chroma uses its bundled local embedder, same as test_run_pipeline.py).
"""
from __future__ import annotations

import json

from ingestion.run_pipeline import run_pipeline
from storage.sqlite_store import SQLiteStore


def _jira_file(tmp_path, name, status):
    """Write a 1-issue Jira payload with the given status; return its path."""
    payload = {
        "source": "Apache",
        "issues": [
            {
                "key": "AIP-1",
                "self": "https://jira/AIP-1",
                "fields": {
                    "summary": "Build ingestion pipeline",
                    "status": {"name": status},
                    "assignee": {"displayName": "Minh Tuan"},
                    "priority": {"name": "High"},
                    "labels": ["backend"],
                    "duedate": "2025-05-24",
                    "description": "Implement ingestion.",
                    "created": "2025-05-01T09:00:00.000+0000",
                    "updated": "2025-05-20T09:00:00.000+0000",
                },
            }
        ],
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_daily_run_accumulates_history_and_diffs(tmp_path):
    db = str(tmp_path / "vault.db")
    chroma = str(tmp_path / "chroma")

    day1 = _jira_file(tmp_path, "jira_day1.json", "In Progress")
    day2 = _jira_file(tmp_path, "jira_day2.json", "Done")

    # Day 1 — baseline (no prior snapshot → no day-over-day change).
    run_pipeline(day1, None, None, db_path=db, chroma_path=chroma, as_of="2025-05-30")
    with SQLiteStore(db_path=db) as s:
        assert s.get_daily_diff("2025-05-30") == []

    # Day 2 — same store (NO reset): status moves In Progress → Done.
    run_pipeline(day2, None, None, db_path=db, chroma_path=chroma, as_of="2025-05-31")
    with SQLiteStore(db_path=db) as s:
        diff = s.get_daily_diff("2025-05-31")

    assert len(diff) == 1
    assert diff[0]["task_id"] == "AIP-1"
    assert diff[0]["changes"]["status"] == ["In Progress", "Done"]
