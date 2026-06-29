"""Unit tests for PM rollups & trends (src/agents/insights.py)."""
from __future__ import annotations

from agents.insights import aggregate_by_assignee, weekly_changes
from storage.init_db import init_db
from storage.sqlite_store import SQLiteStore

_CONCERNS = [
    {"type": "deadline_risk", "task_id": "A-1", "severity": 5, "assignee": "Alice"},
    {"type": "unresolved_blocker", "task_id": "A-2", "severity": 4,
     "assignee": "Alice"},
    {"type": "stalled_task", "task_id": "A-3", "severity": 3, "assignee": "Alice"},
    {"type": "stalled_task", "task_id": "B-1", "severity": 3, "assignee": "Bob"},
    {"type": "stalled_task", "task_id": "C-1", "severity": 2, "assignee": None},
]


def test_aggregate_by_assignee_ranks_by_load_then_severity():
    rollup = aggregate_by_assignee(_CONCERNS)

    # Alice carries the most (3) → ranked first; None bucket → "Unassigned".
    assert rollup[0]["assignee"] == "Alice"
    assert rollup[0]["count"] == 3
    assert rollup[0]["max_severity"] == 5
    assert rollup[0]["by_type"] == {
        "deadline_risk": 1, "unresolved_blocker": 1, "stalled_task": 1,
    }
    assert {g["assignee"] for g in rollup} == {"Alice", "Bob", "Unassigned"}


def test_aggregate_by_assignee_empty():
    assert aggregate_by_assignee([]) == []


def test_weekly_changes_delegates_to_diff_since(tmp_path):
    db = str(tmp_path / "d.db")
    init_db(db)
    with SQLiteStore(db_path=db) as s:
        # Entity now Done; its state "last week" (a snapshot before the cutoff)
        # was In Progress → weekly_changes must surface the status move.
        s.bulk_upsert([{
            "source_id": "T-1", "source": "jira", "status": "Done",
            "assignee": "Bob", "created_at": "2025-01-01T00:00:00.000+0000",
            "updated_at": "2025-05-30T00:00:00.000+0000", "title": "t",
        }])
        s.save_snapshot(
            "T-1", {"status": "In Progress", "assignee": "Bob"}, None,
            snapshot_date="2025-05-20",
        )
        changes = weekly_changes(s, "2025-05-30", days=7)  # cutoff = 2025-05-23

    assert len(changes) == 1
    assert changes[0]["task_id"] == "T-1"
    assert changes[0]["changes"]["status"] == ["In Progress", "Done"]
