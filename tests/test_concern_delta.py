"""Delta-first digest: the concern-set delta vs the previous run.

Covers the deterministic delta (new / resolved / worsened), its rendering, the
SQLite round-trip that gives it memory, and that the Slack digest leads with it.
"""
from __future__ import annotations

from unittest.mock import patch

from agents.insights import compute_concern_delta
from agents.report_pipeline import format_concern_delta, generate_grounded_report
from delivery.slack import build_digest_text
from storage.init_db import init_db
from storage.sqlite_store import SQLiteStore


def _c(type_, task_id, severity):
    return {"type": type_, "task_id": task_id, "severity": severity,
            "explanation": f"{type_} {task_id}"}


def test_compute_concern_delta_classifies_new_resolved_worsened():
    prior = {
        ("unresolved_blocker", "A-1"): 3,   # worsens to 5
        ("stalled_task", "A-2"): 3,          # resolved (absent today)
        ("deadline_risk", "A-3"): 4,         # unchanged
    }
    today = [
        _c("unresolved_blocker", "A-1", 5),       # worsened
        _c("deadline_risk", "A-3", 4),            # unchanged
        _c("cross_source_conflict", "A-9", 5),    # new
    ]
    delta = compute_concern_delta(prior, today)

    assert delta["has_prior"] is True
    assert [c["task_id"] for c in delta["new"]] == ["A-9"]
    assert [c["task_id"] for c in delta["resolved"]] == ["A-2"]
    assert len(delta["worsened"]) == 1
    assert delta["worsened"][0]["task_id"] == "A-1"
    assert delta["worsened"][0]["prev_severity"] == 3
    assert delta["worsened"][0]["severity"] == 5


def test_compute_concern_delta_first_run_has_no_prior():
    delta = compute_concern_delta({}, [_c("stalled_task", "A-1", 3)])
    assert delta["has_prior"] is False
    assert [c["task_id"] for c in delta["new"]] == ["A-1"]


def test_format_concern_delta_first_run_and_markers():
    empty = {"has_prior": False, "new": [], "resolved": [], "worsened": []}
    assert "First run" in format_concern_delta(empty, lang="en")

    delta = {
        "has_prior": True,
        "new": [_c("unresolved_blocker", "A-9", 5)],
        "resolved": [{"type": "stalled_task", "task_id": "A-2", "severity": 3}],
        "worsened": [{**_c("deadline_risk", "A-1", 5), "prev_severity": 4}],
    }
    md = format_concern_delta(delta, lang="en")
    assert md.startswith("## ")
    assert "A-9" in md and "A-2" in md
    assert "sev 4→5" in md and "A-1" in md


def test_concern_snapshot_round_trip(tmp_path):
    db = str(tmp_path / "d.db")
    init_db(db)
    with SQLiteStore(db_path=db) as s:
        s.save_concern_snapshot("2025-05-30", [
            _c("unresolved_blocker", "A-1", 3),
            _c("stalled_task", "A-2", 2),
        ])
        prior = s.load_prior_concerns("2025-05-31")  # most recent prior to 05-31

    assert prior == {
        ("unresolved_blocker", "A-1"): 3,
        ("stalled_task", "A-2"): 2,
    }


def test_load_prior_concerns_strictly_before_date(tmp_path):
    db = str(tmp_path / "d.db")
    init_db(db)
    with SQLiteStore(db_path=db) as s:
        s.save_concern_snapshot("2025-05-30", [_c("stalled_task", "A-1", 3)])
        assert s.load_prior_concerns("2025-05-30") == {}  # same date is not prior


def test_slack_digest_leads_with_delta():
    delta = {
        "has_prior": True,
        "new": [_c("unresolved_blocker", "A-1", 5)],
        "resolved": [],
        "worsened": [],
    }
    text = build_digest_text("2025-05-30", [_c("unresolved_blocker", "A-1", 5)],
                             lang="en", delta=delta)
    assert "1 new" in text


def test_generate_grounded_report_leads_with_delta():
    delta = {
        "has_prior": True,
        "new": [_c("unresolved_blocker", "A-1", 5)],
        "resolved": [],
        "worsened": [],
    }
    concerns = [_c("unresolved_blocker", "A-1", 5)]
    with patch(
        "agents.report_pipeline.run_report_agent", return_value="BODY-NARRATIVE"
    ):
        out = generate_grounded_report(
            "2025-05-30", concerns, None, None, lang="en", delta=delta
        )

    assert out.startswith("## ")                       # delta block leads
    assert "Changes Since Last Run" in out
    assert out.index("Changes Since Last Run") < out.index("BODY-NARRATIVE")
