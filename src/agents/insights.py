"""PM rollups & trends (roadmap P2) — deterministic aggregations over the
Concern Engine output + snapshot history.

Two views a PM asks for beyond the daily report:
  * ``aggregate_by_assignee`` — "who is carrying the most risk" (owner rollup);
  * ``weekly_changes`` — "what changed since last week" (trend), via the existing
    ``SQLiteStore.diff_since`` point-in-time diff.

Both are pure/deterministic (no LLM) and feed ``output/insights.json``.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Tuple

if TYPE_CHECKING:  # annotation only — the store is passed in at call time
    from storage.sqlite_store import SQLiteStore

_UNASSIGNED = "Unassigned"


def aggregate_by_assignee(concerns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Roll concerns up per assignee, ranked by load then peak severity.

    Returns a list of ``{assignee, count, by_type, max_severity, task_ids}`` so a
    PM can see who is overloaded and on what kind of risk.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    for c in concerns:
        who = c.get("assignee") or _UNASSIGNED
        g = groups.setdefault(
            who,
            {"assignee": who, "count": 0, "by_type": Counter(),
             "max_severity": 0, "task_ids": []},
        )
        g["count"] += 1
        g["by_type"][c["type"]] += 1
        g["max_severity"] = max(g["max_severity"], c.get("severity", 0))
        g["task_ids"].append(c["task_id"])

    out: List[Dict[str, Any]] = []
    for g in groups.values():
        g["by_type"] = dict(g["by_type"])
        out.append(g)
    out.sort(key=lambda g: (g["count"], g["max_severity"]), reverse=True)
    return out


def weekly_changes(
    store: "SQLiteStore", date_str: str, days: int = 7
) -> List[Dict[str, Any]]:
    """What changed in the ``days`` before ``date_str`` — the "since last week"
    trend. Delegates to ``SQLiteStore.diff_since`` (point-in-time reconstruction
    over the snapshot changelog), so it's only meaningful once daily runs have
    accumulated history."""
    from_date = (date.fromisoformat(date_str) - timedelta(days=days)).isoformat()
    return store.diff_since(from_date)


def compute_concern_delta(
    prior: Dict[Tuple[str, str], int], today: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Diff today's concern set against the prior run's — the "delta-first" core.

    ``prior`` is ``{(type, task_id): severity}`` (from
    ``SQLiteStore.load_prior_concerns``); ``today`` is the concern list. Returns
    ``new`` / ``resolved`` / ``worsened`` (severity-sorted) plus ``has_prior``
    (False on the first run — nothing to compare against).
    """
    today_by_key = {(c["type"], c["task_id"]): c for c in today}
    new = [c for k, c in today_by_key.items() if k not in prior]
    resolved = [
        {"type": t, "task_id": tid, "severity": sev}
        for (t, tid), sev in prior.items()
        if (t, tid) not in today_by_key
    ]
    worsened = [
        {**c, "prev_severity": prior[k]}
        for k, c in today_by_key.items()
        if k in prior and (c.get("severity") or 0) > (prior[k] or 0)
    ]
    new.sort(key=lambda c: c.get("severity") or 0, reverse=True)
    worsened.sort(key=lambda c: c.get("severity") or 0, reverse=True)
    return {
        "has_prior": bool(prior),
        "new": new,
        "resolved": resolved,
        "worsened": worsened,
    }
