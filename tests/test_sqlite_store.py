"""Unit tests for SQLiteStore.get_daily_diff — the day-over-day state diff.

Covers the three behaviours that make the diff trustworthy:
  * compares against the most recent *prior* snapshot (robust to gaps), not strictly day-1;
  * ignores cosmetic churn (e.g. a new ``updated_at``) — only meaningful fields count;
  * a baseline snapshot with no prior is not reported as a "change".
"""
from __future__ import annotations

import pytest

from storage.init_db import init_db
from storage.sqlite_store import SQLiteStore


def _snap(store, task_id, snapshot_date, status, **extra):
    """Save a snapshot whose data carries the meaningful fields + an updated_at."""
    data = {
        "status": status,
        "assignee": extra.get("assignee"),
        "due_date": extra.get("due_date"),
        "priority": extra.get("priority"),
        "updated_at": extra.get("updated_at", snapshot_date + "T00:00:00.000+0000"),
    }
    store.save_snapshot(task_id, data, None, snapshot_date=snapshot_date)


def test_diff_uses_most_recent_prior_across_a_gap(tmp_path):
    db = str(tmp_path / "d.db")
    init_db(db)
    with SQLiteStore(db_path=db) as s:
        _snap(s, "T-1", "2025-05-20", "In Progress")   # gap: no 05-29 snapshot
        _snap(s, "T-1", "2025-05-30", "Done")
        diff = s.get_daily_diff("2025-05-30")

    assert len(diff) == 1
    assert diff[0]["previous_date"] == "2025-05-20"
    assert diff[0]["changes"]["status"] == ["In Progress", "Done"]


def test_diff_ignores_cosmetic_churn(tmp_path):
    db = str(tmp_path / "d.db")
    init_db(db)
    with SQLiteStore(db_path=db) as s:
        _snap(s, "T-1", "2025-05-29", "In Progress", updated_at="2025-05-29T01:00:00.000+0000")
        # Only updated_at moves; no meaningful field changes → not a diff.
        _snap(s, "T-1", "2025-05-30", "In Progress", updated_at="2025-05-30T09:00:00.000+0000")
        diff = s.get_daily_diff("2025-05-30")

    assert diff == []


def test_diff_skips_baseline_without_prior(tmp_path):
    db = str(tmp_path / "d.db")
    init_db(db)
    with SQLiteStore(db_path=db) as s:
        _snap(s, "T-1", "2025-05-30", "In Progress")   # only snapshot, no prior
        diff = s.get_daily_diff("2025-05-30")

    assert diff == []


def test_diff_reports_only_changed_tasks(tmp_path):
    db = str(tmp_path / "d.db")
    init_db(db)
    with SQLiteStore(db_path=db) as s:
        _snap(s, "T-1", "2025-05-29", "In Progress")
        _snap(s, "T-2", "2025-05-29", "To Do")
        _snap(s, "T-1", "2025-05-30", "Done")          # changed
        _snap(s, "T-2", "2025-05-30", "To Do")         # unchanged (status-wise)
        diff = s.get_daily_diff("2025-05-30")

    assert {d["task_id"] for d in diff} == {"T-1"}


def test_diff_detects_assignee_change(tmp_path):
    db = str(tmp_path / "d.db")
    init_db(db)
    with SQLiteStore(db_path=db) as s:
        _snap(s, "T-1", "2025-05-29", "In Progress", assignee="Alice")
        _snap(s, "T-1", "2025-05-30", "In Progress", assignee="Bob")
        diff = s.get_daily_diff("2025-05-30")

    assert len(diff) == 1
    assert diff[0]["changes"]["assignee"] == ["Alice", "Bob"]
    assert "status" not in diff[0]["changes"]


# ---------------------------------------------------------------------------
# diff_since — net diff vs an arbitrary point in the past (week/month/year ago)
# ---------------------------------------------------------------------------

def _entity(store, task_id, status, **extra):
    store.bulk_upsert([{
        "source_id": task_id, "source": "jira", "status": status,
        "assignee": extra.get("assignee"), "due_date": extra.get("due_date"),
        "priority": extra.get("priority"), "title": extra.get("title", "t"),
        "created_at": extra.get("created_at", "2025-01-01T00:00:00.000+0000"),
        "updated_at": extra.get("updated_at", "2025-05-30T00:00:00.000+0000"),
    }])


def test_diff_since_reconstructs_state_as_of_point_in_time(tmp_path):
    """Compares current entities vs the most recent snapshot on/before the cutoff —
    an exact snapshot on that day isn't required."""
    db = str(tmp_path / "d.db")
    init_db(db)
    with SQLiteStore(db_path=db) as s:
        _entity(s, "T-1", "Done", assignee="Bob", created_at="2025-01-01T00:00:00.000+0000")
        _snap(s, "T-1", "2025-01-15", "In Progress", assignee="Alice")  # Jan state
        _snap(s, "T-1", "2025-05-30", "Done", assignee="Bob")           # later than cutoff
        diff = s.diff_since("2025-04-30")  # "last month" from a 2025-05-30 report

    assert len(diff) == 1
    d = diff[0]
    assert d["task_id"] == "T-1" and d["is_new"] is False
    assert d["changes"]["status"] == ["In Progress", "Done"]
    assert d["changes"]["assignee"] == ["Alice", "Bob"]


def test_diff_since_marks_genuinely_new_task(tmp_path):
    db = str(tmp_path / "d.db")
    init_db(db)
    with SQLiteStore(db_path=db) as s:
        _entity(s, "T-NEW", "In Progress", created_at="2025-05-10T00:00:00.000+0000")
        diff = s.diff_since("2025-04-30")  # created after cutoff, no prior snapshot

    assert len(diff) == 1
    assert diff[0]["task_id"] == "T-NEW" and diff[0]["is_new"] is True


def test_diff_since_skips_tasks_with_no_history_before_cutoff(tmp_path):
    """Created before the cutoff but no snapshot that far back → no change can be
    asserted → skipped (avoids 'everything is new' before the baseline)."""
    db = str(tmp_path / "d.db")
    init_db(db)
    with SQLiteStore(db_path=db) as s:
        _entity(s, "T-OLD", "Done", created_at="2025-01-01T00:00:00.000+0000")
        _snap(s, "T-OLD", "2025-05-30", "Done")  # only snapshot is after the cutoff
        diff = s.diff_since("2025-04-30")

    assert diff == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
