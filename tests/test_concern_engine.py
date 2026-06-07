"""The project's most important test: Concern Engine accuracy vs. `_ground_truth`.

Loads the real synthetic Jira data, drives each rule, and measures recall (per
rule) and precision (overall) against the planted ground-truth labels.

Setup
-----
* SQLite: an isolated temp-file DB (disposable per test — functionally in-memory).
* ChromaDB: a true in-memory ``EphemeralClient``.
* Reference date ``AS_OF = 2025-05-30`` — the data's "present" (max ``updated`` is
  2025-05-29). The rules are time-relative, so pinning the reference makes the
  results deterministic.

`_ground_truth` schema: ``{"is_anomaly": bool, "anomaly_type": stalled |
deadline_risk | blocker | cross_source_conflict | None}`` (36 of each type, 144
anomalies total, 856 normal).

Note on cross-source conflict: it needs matching meeting-note chunks in ChromaDB,
which aren't present here, so the precision test scopes its positives to the three
SQL-detectable types (stalled / deadline / blocker).
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import chromadb
import pytest

from agents.concern_engine import ConcernEngine
from storage.chroma_store import ChromaStore
from storage.init_db import init_db
from storage.sqlite_store import SQLiteStore

AS_OF = "2025-05-30"
_DATA = Path(__file__).resolve().parents[1] / "data" / "jira" / "jira_synthetic_AIP.json"


# ---------------------------------------------------------------------------
# Data loading / helpers
# ---------------------------------------------------------------------------

def _load_issues():
    return json.loads(_DATA.read_text(encoding="utf-8"))["issues"]


def _gt(it):
    return it.get("_ground_truth") or {}


def _is_anomaly(it):
    return bool(_gt(it).get("is_anomaly"))


def _atype(it):
    return _gt(it).get("anomaly_type")


def _status(it):
    return (it["fields"].get("status") or {}).get("name")


def _to_entity(it):
    """Map a raw Jira issue to a normalized entity dict (drops `_ground_truth`)."""
    f = it["fields"]
    return {
        "source_id": it["key"],
        "source": "jira",
        "title": f.get("summary"),
        "status": _status(it),
        "assignee": (f.get("assignee") or {}).get("displayName"),
        "priority": (f.get("priority") or {}).get("name"),
        "labels": f.get("labels") or [],
        "due_date": f.get("duedate"),
        "description": "",
        "url": None,
        "created_at": f.get("created"),
        "updated_at": f.get("updated"),
    }


_ISSUES = _load_issues()
ANOMALIES = [it for it in _ISSUES if _is_anomaly(it)]
NORMALS = [it for it in _ISSUES if not _is_anomaly(it)]


def _anomalies_of(atype, *, status=None, status_not=None, label=None):
    out = []
    for it in ANOMALIES:
        if _atype(it) != atype:
            continue
        if status and _status(it) != status:
            continue
        if status_not and _status(it) == status_not:
            continue
        if label and label not in (it["fields"].get("labels") or []):
            continue
        out.append(it)
    return out


def _populate(tmp_path, issues, name="v.db"):
    """Build an isolated SQLite DB + in-memory ChromaDB from raw issues."""
    db = str(tmp_path / name)
    init_db(db)
    with SQLiteStore(db_path=db) as store:
        store.bulk_upsert([_to_entity(it) for it in issues])
    chroma = ChromaStore(client=chromadb.EphemeralClient())
    return db, chroma


# ---------------------------------------------------------------------------
# Per-rule recall tests
# ---------------------------------------------------------------------------

def test_stalled_rule(tmp_path):
    """Insert 3 stalled anomalies → all 3 detected by the stalled rule."""
    stalled = _anomalies_of("stalled", status="In Progress")[:3]
    assert len(stalled) == 3, "fixture needs 3 In-Progress stalled anomalies"

    db, _ = _populate(tmp_path, stalled)
    with SQLiteStore(db_path=db) as store:
        flagged = {c["task_id"] for c in ConcernEngine(as_of=AS_OF)._rule_stalled(store)}

    expected = {it["key"] for it in stalled}
    assert expected <= flagged, f"stalled rule missed {expected - flagged}"


def test_deadline_rule(tmp_path):
    """Insert 2 deadline-risk anomalies → both detected by the deadline rule."""
    deadline = _anomalies_of("deadline_risk", status_not="Done")[:2]
    assert len(deadline) == 2, "fixture needs 2 not-Done deadline anomalies"

    db, _ = _populate(tmp_path, deadline)
    with SQLiteStore(db_path=db) as store:
        flagged = {c["task_id"] for c in ConcernEngine(as_of=AS_OF)._rule_deadline_risk(store)}

    expected = {it["key"] for it in deadline}
    assert expected <= flagged, f"deadline rule missed {expected - flagged}"


def test_blocker_rule(tmp_path):
    """Insert 2 blocker anomalies → both detected by the blocker rule."""
    blockers = _anomalies_of("blocker", status_not="Done", label="blocker")[:2]
    assert len(blockers) == 2, "fixture needs 2 open blocker-labelled anomalies"

    db, _ = _populate(tmp_path, blockers)
    with SQLiteStore(db_path=db) as store:
        flagged = {c["task_id"] for c in ConcernEngine(as_of=AS_OF)._rule_blocker(store)}

    expected = {it["key"] for it in blockers}
    assert expected <= flagged, f"blocker rule missed {expected - flagged}"


# ---------------------------------------------------------------------------
# Stalled tiering (needs-review vs chronic backlog)
# ---------------------------------------------------------------------------

def test_stalled_severity_tiers():
    """needs-review → high (4); chronic backlog → low (2); plain → medium (3)."""
    sev_review, _ = ConcernEngine.score_severity(
        "stalled_task", days_stalled=120, has_review_label=True, chronic=False
    )
    sev_chronic, _ = ConcernEngine.score_severity(
        "stalled_task", days_stalled=120, has_review_label=False, chronic=True
    )
    sev_plain, _ = ConcernEngine.score_severity(
        "stalled_task", days_stalled=5, has_review_label=False, chronic=False
    )
    assert (sev_review, sev_chronic, sev_plain) == (4, 2, 3)


def test_stalled_anomalies_flagged_actionable_not_chronic(tmp_path):
    """The planted stalled anomalies carry `needs-review`, so the rule must mark
    them actionable (severity 4, chronic=False), not chronic backlog."""
    stalled = _anomalies_of("stalled", status="In Progress")[:3]
    assert len(stalled) == 3

    db, _ = _populate(tmp_path, stalled)
    with SQLiteStore(db_path=db) as store:
        concerns = ConcernEngine(as_of=AS_OF)._rule_stalled(store)

    by_id = {c["task_id"]: c for c in concerns}
    for it in stalled:
        c = by_id[it["key"]]
        assert c["details"]["needs_review"] is True
        assert c["details"]["chronic"] is False
        assert c["severity"] == 4


def test_chronic_stalled_is_low_severity(tmp_path):
    """A long-idle In-Progress task without `needs-review` is kept but
    de-prioritised (chronic=True, severity 2) so it doesn't crowd the top block."""
    normals_ip = [it for it in NORMALS if _status(it) == "In Progress"]
    assert normals_ip, "need at least one normal In-Progress task"
    ent = _to_entity(normals_ip[0])
    ent["labels"] = []  # ensure no needs-review label
    ent["updated_at"] = "2024-01-01T00:00:00.000+0000"  # very stale vs AS_OF

    db = str(tmp_path / "chronic.db")
    init_db(db)
    with SQLiteStore(db_path=db) as store:
        store.bulk_upsert([ent])
        concerns = ConcernEngine(as_of=AS_OF)._rule_stalled(store)

    c = {c["task_id"]: c for c in concerns}[normals_ip[0]["key"]]
    assert c["details"]["chronic"] is True
    assert c["severity"] == 2


# ---------------------------------------------------------------------------
# Cross-source conflict — B1 (accept Done/Closed/Resolved) + B2 (exact key match)
# ---------------------------------------------------------------------------

def test_mentions_key_is_exact_not_substring():
    """B2: a short key must not match a longer one (AIP-5 ⊄ AIP-53)."""
    assert ConcernEngine._mentions_key("AIP-53 is pending review", "AIP-53") is True
    assert ConcernEngine._mentions_key("see AIP-5.", "AIP-5") is True
    assert ConcernEngine._mentions_key("AIP-53 is pending review", "AIP-5") is False
    assert ConcernEngine._mentions_key("AIP-530 done", "AIP-53") is False


def test_cross_source_accepts_closed_resolved_and_exact_match(tmp_path):
    """B1: Done/Closed/Resolved are all candidates. B2: a substring-only match
    (PROJ-5 inside PROJ-53) must NOT be flagged."""
    base = {
        "source": "jira", "title": "t", "assignee": "X", "priority": "High",
        "labels": [], "due_date": None, "description": "", "url": None,
        "created_at": "2025-05-01T00:00:00.000+0000",
        "updated_at": AS_OF + "T00:00:00.000+0000",  # completed "today"
    }
    ents = [
        {**base, "source_id": "PROJ-1", "status": "Done"},
        {**base, "source_id": "PROJ-2", "status": "Closed"},
        {**base, "source_id": "PROJ-3", "status": "Resolved"},
        {**base, "source_id": "PROJ-5", "status": "Done"},  # substring trap vs PROJ-53
    ]
    db = str(tmp_path / "cs.db")
    init_db(db)
    with SQLiteStore(db_path=db) as store:
        store.bulk_upsert(ents)
        chroma = ChromaStore(client=chromadb.EphemeralClient())
        chroma.add_meeting_chunks({
            "note_id": "MTG-1", "date": AS_OF, "project": "PROJ",
            "content": "PROJ-1 still pending review. PROJ-2 pending. "
                       "PROJ-3 chưa xong. PROJ-53 pending review.",
        })
        flagged = {
            c["task_id"]
            for c in ConcernEngine(as_of=AS_OF)._rule_cross_source_conflict(store, chroma)
        }

    assert {"PROJ-1", "PROJ-2", "PROJ-3"} <= flagged   # B1: all terminal statuses
    assert "PROJ-5" not in flagged                      # B2: no substring false-positive


# ---------------------------------------------------------------------------
# Precision on mixed data (normal + anomaly)
# ---------------------------------------------------------------------------

def test_precision(tmp_path):
    """Run the full engine on anomalies + a random sample of normal tasks and
    verify precision >= 0.8 (the engine must not flood the output with false
    positives). Recall and precision are printed for monitoring."""
    random.seed(0)
    # SQL-detectable anomaly types only (cross-source needs meeting chunks).
    positives = [it for it in ANOMALIES if _atype(it) in ("stalled", "deadline_risk", "blocker")]
    negatives = random.sample(NORMALS, 100)
    mix = positives + negatives
    anomaly_keys = {it["key"] for it in positives}

    db, chroma = _populate(tmp_path, mix)
    with SQLiteStore(db_path=db) as store:
        concerns = ConcernEngine(as_of=AS_OF).run_all_rules(store, chroma)

    flagged = {c["task_id"] for c in concerns}
    tp = flagged & anomaly_keys
    fp = flagged - anomaly_keys
    precision = len(tp) / len(flagged) if flagged else 0.0
    recall = len(tp) / len(anomaly_keys) if anomaly_keys else 0.0

    # Print for monitoring (ASCII only — avoids Windows console encoding issues).
    # Shown with `pytest -s`, and always on failure.
    print(
        f"\n[ConcernEngine accuracy] positives={len(positives)} "
        f"negatives={len(negatives)} flagged={len(flagged)} "
        f"TP={len(tp)} FP={len(fp)}"
    )
    print(f"[ConcernEngine accuracy] precision={precision:.3f}  recall={recall:.3f}")

    assert precision >= 0.8, f"precision {precision:.3f} < 0.80 (too many false positives: {len(fp)})"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
