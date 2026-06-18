"""Tests for the Report Agent tool dispatcher — focused on get_tasks_changed_since.

The cutoff date is computed deterministically in code (``_cutoff_date``) from the
report's reference date, so the LLM only has to pick a ``period`` word — it never
does date arithmetic itself.
"""
from __future__ import annotations

import pytest

from agents.tools import _cutoff_date, dispatch_tool
from storage.init_db import init_db
from storage.sqlite_store import SQLiteStore


@pytest.mark.parametrize(
    "period,expected",
    [
        ("week", "2025-05-23"),
        ("month", "2025-04-30"),
        ("quarter", "2025-02-28"),   # day clamped to Feb length
        ("year", "2024-05-30"),
    ],
)
def test_cutoff_date(period, expected):
    assert _cutoff_date("2025-05-30", period) == expected


def test_get_tasks_changed_since_resolves_period_from_ref_date(tmp_path):
    db = str(tmp_path / "d.db")
    init_db(db)
    with SQLiteStore(db_path=db) as s:
        s.bulk_upsert([{
            "source_id": "T-1", "source": "jira", "status": "Done", "assignee": "Bob",
            "due_date": None, "priority": None, "title": "t",
            "created_at": "2025-01-01T00:00:00.000+0000",
            "updated_at": "2025-05-30T00:00:00.000+0000",
        }])
        s.save_snapshot(
            "T-1",
            {"status": "In Progress", "assignee": "Alice", "due_date": None, "priority": None},
            None,
            snapshot_date="2025-01-15",
        )
        out = dispatch_tool(
            "get_tasks_changed_since", {"period": "month"}, s, None, ref_date="2025-05-30"
        )

    assert out["since"] == "2025-04-30"           # month before the report date
    assert out["source_ids"] == ["T-1"]
    res = {r["task_id"]: r for r in out["result"]}
    assert res["T-1"]["changes"]["status"] == ["In Progress", "Done"]


def test_get_tasks_changed_since_honours_explicit_since_date(tmp_path):
    db = str(tmp_path / "d.db")
    init_db(db)
    with SQLiteStore(db_path=db) as s:
        out = dispatch_tool(
            "get_tasks_changed_since",
            {"since_date": "2024-12-31"},
            s, None, ref_date="2025-05-30",
        )
    assert out["since"] == "2024-12-31"            # explicit date overrides period


def test_unknown_tool_returns_error():
    assert dispatch_tool("nope", {}, None, None) == {"error": "Unknown tool"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
