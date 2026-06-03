"""Concern Engine (Week 4) — deterministic risk detection over the dual store.

Three rules run as pure SQL over SQLite (stalled / deadline / blocker); the
cross-source conflict rule combines a SQL recency filter with a ChromaDB
keyword match over meeting notes — no LLM required (the plan keeps the LLM as an
optional confirmation step only).

Reference date
--------------
Every rule compares against a *reference date* (`as_of`, default today). This
matters because the synthetic data is dated mid-2025: pass ``as_of="2025-05-22"``
to get meaningful results against it, or leave it unset to evaluate "as of now".

Timestamps are compared via ``substr(updated_at, 1, 10)`` (the date portion),
because the Jira ``updated``/``created`` values carry a ``+0000`` suffix that
SQLite's ``julianday`` cannot parse directly.
"""
from __future__ import annotations

import os
import sys

# --- Make the module runnable from any entry point (CLI, import, tests) ------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))   # .../src/agents
_SRC_DIR = os.path.dirname(_THIS_DIR)                     # .../src
_ROOT_DIR = os.path.dirname(_SRC_DIR)                     # repo root
for _p in (_ROOT_DIR, _SRC_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from config import (
    BLOCKER_OPEN_DAYS,
    CHROMA_PATH,
    CONFLICT_WINDOW_H,
    DB_PATH,
    DEADLINE_RISK_DAYS,
    STALLED_DAYS,
)

if TYPE_CHECKING:  # annotations only — instances are passed in at call time
    from storage.chroma_store import ChromaStore
    from storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

# Keywords that, when found in a meeting chunk mentioning a "Done" task, signal a
# cross-source conflict (the plan's rule-based fallback: pending|chờ|review|chưa).
_CONFLICT_KEYWORDS = ("pending", "chờ", "review", "chưa")

_MEETING_COLLECTION = "meeting_chunks"


class ConcernEngine:
    """Detect project risks from the SQLite + ChromaDB stores."""

    def __init__(
        self,
        *,
        as_of: Optional[str] = None,
        stalled_days: int = STALLED_DAYS,
        deadline_risk_days: int = DEADLINE_RISK_DAYS,
        blocker_open_days: int = BLOCKER_OPEN_DAYS,
        conflict_window_h: int = CONFLICT_WINDOW_H,
    ) -> None:
        """Parameters
        ----------
        as_of:
            ISO date (YYYY-MM-DD) used as the reference "today" for all
            time-based rules. ``None`` → SQLite's ``'now'``.
        stalled_days / deadline_risk_days / blocker_open_days / conflict_window_h:
            Thresholds (default from ``config``).
        """
        self.as_of = as_of
        self._ref = as_of if as_of else "now"
        self.stalled_days = stalled_days
        self.deadline_risk_days = deadline_risk_days
        self.blocker_open_days = blocker_open_days
        self.conflict_window_h = conflict_window_h

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def run_all_rules(
        self,
        sqlite_store: "SQLiteStore",
        chroma_store: "ChromaStore",
    ) -> List[Dict[str, Any]]:
        """Run every rule, merge the results, and sort by severity (desc)."""
        concerns: List[Dict[str, Any]] = []
        concerns.extend(self._rule_stalled(sqlite_store))
        concerns.extend(self._rule_deadline_risk(sqlite_store))
        concerns.extend(self._rule_blocker(sqlite_store))
        concerns.extend(self._rule_cross_source_conflict(sqlite_store, chroma_store))

        concerns.sort(key=lambda c: c["severity"], reverse=True)
        logger.info("ConcernEngine produced %d concern(s).", len(concerns))
        return concerns

    # ------------------------------------------------------------------
    # Rule 1 — Stalled task
    # ------------------------------------------------------------------

    def _rule_stalled(self, db: "SQLiteStore") -> List[Dict[str, Any]]:
        """In-progress tasks not updated for more than ``stalled_days`` days."""
        sql = """
            SELECT task_id, assignee, status, updated_at,
                   julianday(?) - julianday(substr(updated_at, 1, 10)) AS days_stalled
            FROM entities
            WHERE status = 'In Progress'
              AND updated_at IS NOT NULL
              AND julianday(?) - julianday(substr(updated_at, 1, 10)) > ?
            ORDER BY days_stalled DESC
        """
        rows = db.run_query(sql, (self._ref, self._ref, self.stalled_days))
        concerns: List[Dict[str, Any]] = []
        for r in rows:
            if r["days_stalled"] is None:
                continue
            days = int(round(r["days_stalled"]))
            severity, explanation = self.score_severity("stalled_task", days_stalled=days)
            concerns.append({
                "type": "stalled_task",
                "task_id": r["task_id"],
                "severity": severity,
                "explanation": explanation,
                "assignee": r["assignee"],
                "source_ids": [r["task_id"]],
                "details": {"days_stalled": days, "status": r["status"]},
            })
        return concerns

    # ------------------------------------------------------------------
    # Rule 2 — Deadline risk
    # ------------------------------------------------------------------

    def _rule_deadline_risk(self, db: "SQLiteStore") -> List[Dict[str, Any]]:
        """Not-done tasks within ``deadline_risk_days`` of (or past) their due date."""
        sql = """
            SELECT task_id, assignee, status, due_date,
                   julianday(due_date) - julianday(?) AS days_remaining
            FROM entities
            WHERE status != 'Done'
              AND due_date IS NOT NULL AND due_date != ''
              AND julianday(due_date) - julianday(?) <= ?
            ORDER BY days_remaining ASC
        """
        rows = db.run_query(sql, (self._ref, self._ref, self.deadline_risk_days))
        concerns: List[Dict[str, Any]] = []
        for r in rows:
            if r["days_remaining"] is None:
                continue
            days = int(round(r["days_remaining"]))
            severity, explanation = self.score_severity(
                "deadline_risk", days_remaining=days, status=r["status"]
            )
            concerns.append({
                "type": "deadline_risk",
                "task_id": r["task_id"],
                "severity": severity,
                "explanation": explanation,
                "assignee": r["assignee"],
                "source_ids": [r["task_id"]],
                "details": {"days_remaining": days, "due_date": r["due_date"], "status": r["status"]},
            })
        return concerns

    # ------------------------------------------------------------------
    # Rule 3 — Unresolved blocker
    # ------------------------------------------------------------------

    def _rule_blocker(self, db: "SQLiteStore") -> List[Dict[str, Any]]:
        """Open tasks labelled 'blocker' that have been open > ``blocker_open_days``."""
        sql = """
            SELECT e.task_id, e.assignee, e.status, e.updated_at,
                   julianday(?) - julianday(substr(e.updated_at, 1, 10)) AS days_open,
                   (SELECT COUNT(*) FROM backlinks b
                      WHERE b.target_entity_id = e.task_id) AS dependent_count
            FROM entities e
            WHERE e.status != 'Done'
              AND e.labels IS NOT NULL AND json_valid(e.labels)
              AND EXISTS (
                  SELECT 1 FROM json_each(e.labels) je
                  WHERE lower(je.value) = 'blocker'
              )
              AND e.updated_at IS NOT NULL
              AND julianday(?) - julianday(substr(e.updated_at, 1, 10)) > ?
            ORDER BY dependent_count DESC, days_open DESC
        """
        rows = db.run_query(sql, (self._ref, self._ref, self.blocker_open_days))
        concerns: List[Dict[str, Any]] = []
        for r in rows:
            if r["days_open"] is None:
                continue
            days = int(round(r["days_open"]))
            dependent_count = int(r["dependent_count"] or 0)
            severity, explanation = self.score_severity(
                "unresolved_blocker", days_open=days, dependent_count=dependent_count
            )
            concerns.append({
                "type": "unresolved_blocker",
                "task_id": r["task_id"],
                "severity": severity,
                "explanation": explanation,
                "assignee": r["assignee"],
                "source_ids": [r["task_id"]],
                "details": {"days_open": days, "dependent_count": dependent_count, "status": r["status"]},
            })
        return concerns

    # ------------------------------------------------------------------
    # Rule 4 — Cross-source conflict (rule-based filter; LLM optional)
    # ------------------------------------------------------------------

    def _rule_cross_source_conflict(
        self,
        db: "SQLiteStore",
        chroma: "ChromaStore",
    ) -> List[Dict[str, Any]]:
        """Jira says 'Done' recently, but a meeting note still says pending/review.

        Step 1: SQL — recently-Done tasks (within ``conflict_window_h`` hours).
        Step 2: For each, search meeting-note chunks for the task id.
        Step 3: If a chunk mentions the task AND a conflict keyword → flag it
                (deterministic, no LLM). Step 4 (LLM phrasing) is left as an
                optional future enhancement.
        """
        # Step 1 — recently-Done candidates.
        sql = """
            SELECT task_id, assignee, status, updated_at
            FROM entities
            WHERE status = 'Done'
              AND updated_at IS NOT NULL
              AND (julianday(?) - julianday(substr(updated_at, 1, 10))) >= 0
              AND (julianday(?) - julianday(substr(updated_at, 1, 10))) * 24 <= ?
        """
        candidates = db.run_query(
            sql, (self._ref, self._ref, self.conflict_window_h)
        )

        concerns: List[Dict[str, Any]] = []
        for cand in candidates:
            task_id = cand["task_id"]

            # Step 2 — pull meeting chunks relevant to this task.
            try:
                hits = chroma.query(
                    collection=_MEETING_COLLECTION, query_text=task_id, n_results=10
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("cross-source query failed for %s: %s", task_id, exc)
                continue

            # Step 3 — keyword + id match.
            for hit in hits:
                document = (hit.get("document") or "")
                lowered = document.lower()
                if task_id.lower() not in lowered:
                    continue
                if not any(kw in lowered for kw in _CONFLICT_KEYWORDS):
                    continue

                meta = hit.get("metadata") or {}
                note_id = meta.get("note_id") or "meeting_notes"
                severity, explanation = self.score_severity("cross_source_conflict")
                concerns.append({
                    "type": "cross_source_conflict",
                    "task_id": task_id,
                    "severity": severity,
                    "explanation": explanation,
                    "assignee": cand.get("assignee"),
                    "source_ids": [task_id, note_id],
                    "details": {"note_id": note_id, "evidence": document[:200]},
                })
                break  # one flag per task is enough

        return concerns

    # ------------------------------------------------------------------
    # Severity scoring
    # ------------------------------------------------------------------

    @staticmethod
    def score_severity(concern_type: str, **kwargs: Any) -> Tuple[int, str]:
        """Return ``(severity 1-5, explanation)`` for a concern type."""
        if concern_type == "stalled_task":
            days = kwargs.get("days_stalled", 0)
            severity = 4 if days > 7 else 3
            return severity, f"Task chưa có update trong {days} ngày."

        if concern_type == "deadline_risk":
            days = kwargs.get("days_remaining", 0)
            status = kwargs.get("status", "")
            severity = 5 if days <= 1 else 4
            if days < 0:
                return severity, f"Deadline đã quá hạn {abs(days)} ngày, status vẫn '{status}'."
            return severity, f"Deadline còn {days} ngày, status vẫn '{status}'."

        if concern_type == "unresolved_blocker":
            dependent_count = kwargs.get("dependent_count", 0)
            days_open = kwargs.get("days_open", 0)
            severity = min(3 + dependent_count, 5)
            return severity, f"Blocker mở {days_open} ngày, ảnh hưởng {dependent_count} task."

        if concern_type == "cross_source_conflict":
            return 5, "Jira đánh dấu Done nhưng tài liệu khác vẫn ghi nhận đang pending/review."

        return 1, f"Unknown concern type: {concern_type}"


# ---------------------------------------------------------------------------
# CLI — python src/agents/concern_engine.py --date 2025-05-22 [--min-sev 3]
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import json

    from storage.chroma_store import ChromaStore
    from storage.sqlite_store import SQLiteStore

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Run the Concern Engine and emit concerns JSON.")
    parser.add_argument("--date", default=None, help="Reference (as-of) ISO date; default = today")
    parser.add_argument("--min-sev", type=int, default=1, help="Only emit concerns with severity >= this")
    parser.add_argument("--db-path", default=DB_PATH, help="SQLite database path")
    parser.add_argument("--chroma-path", default=CHROMA_PATH, help="ChromaDB persistence path")
    args = parser.parse_args()

    engine = ConcernEngine(as_of=args.date)
    with SQLiteStore(db_path=args.db_path) as sqlite_store:
        chroma_store = ChromaStore(path=args.chroma_path)
        concerns = engine.run_all_rules(sqlite_store, chroma_store)

    concerns = [c for c in concerns if c["severity"] >= args.min_sev]
    print(json.dumps(concerns, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
