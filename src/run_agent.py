"""One-command demo runner (Week 6): ingest → concerns → grounded report → output/.

Pipeline
--------
1. Rebuild the dual store from the 3 synthetic sources (``run_pipeline``).
2. Run the **Concern Engine** → ``output/concerns.json`` (deterministic, severity-sorted).
3. Seed a prior-day snapshot for the top concern tasks so the **day-over-day diff**
   has something to show (the system is designed to run daily; this simulates one
   prior run so ``get_daily_diff`` is non-empty for the demo).
4. Run the **Report Agent**, *grounded* with the top concerns, → ``output/report.md``
   (passed through ``OutputSanitizer`` to redact any leaked secrets).

Usage::

    python src/run_agent.py --date 2025-05-30
    python src/run_agent.py --date 2025-05-30 --skip-ingest   # reuse existing stores
"""
from __future__ import annotations

import os
import sys

# --- Make the module runnable from any entry point --------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))   # .../src
_ROOT_DIR = os.path.dirname(_THIS_DIR)                    # repo root
for _p in (_ROOT_DIR, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import argparse
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from config import CHROMA_PATH, DB_PATH
from agents.concern_engine import ConcernEngine
from agents.report_agent import run_report_agent
from guardrail.sanitizer import OutputSanitizer
from ingestion.run_pipeline import (
    DEFAULT_CONFLUENCE_PATH,
    DEFAULT_JIRA_PATH,
    DEFAULT_MEETING_NOTES_PATH,
    run_pipeline,
)
from storage.chroma_store import ChromaStore
from storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

# Plausible "previous day" status for the seeded day-over-day diff.
_PRIOR_STATUS = {
    "Done": "In Progress",
    "In Progress": "To Do",
    "In Review": "In Progress",
    "Blocked": "In Progress",
    "Resolved": "In Progress",
    "Closed": "In Review",
}
_MAX_SEED_CHANGES = 6
_MAX_CONCERNS_IN_PROMPT = 16


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_prior_snapshots(
    store: SQLiteStore, concerns: List[Dict[str, Any]], ref_date: str
) -> int:
    """Seed yesterday+today snapshots for a few concern tasks so the diff is non-empty.

    Picks up to ``_MAX_SEED_CHANGES`` concern tasks whose current status has a
    plausible earlier value, writes a "yesterday" snapshot with that earlier
    status and a "today" snapshot with the real status. ``get_daily_diff(ref_date)``
    will then surface exactly those transitions.
    """
    yesterday = (datetime.strptime(ref_date, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()

    # Idempotent: clear any prior demo snapshots for these two dates so repeated
    # --skip-ingest runs don't accumulate duplicate diff rows.
    conn = store._ensure_connection()
    conn.execute("DELETE FROM snapshots WHERE snapshot_date IN (?, ?)", (ref_date, yesterday))
    conn.commit()

    seeded = 0
    seen: set = set()
    for concern in concerns:
        if seeded >= _MAX_SEED_CHANGES:
            break
        task_id = concern["task_id"]
        if task_id in seen:
            continue
        seen.add(task_id)

        entity = store.query_entity(task_id)
        if not entity:
            continue
        today_status = entity.get("status") or ""
        prior_status = _PRIOR_STATUS.get(today_status)
        if not prior_status:
            continue  # no meaningful transition to show

        assignee = entity.get("assignee")
        store.save_snapshot(
            task_id, {"status": prior_status, "assignee": assignee}, None, snapshot_date=yesterday
        )
        store.save_snapshot(
            task_id, {"status": today_status, "assignee": assignee}, None, snapshot_date=ref_date
        )
        seeded += 1
    return seeded


def _select_diverse(concerns: List[Dict[str, Any]], per_type: int, cap: int) -> List[Dict[str, Any]]:
    """Pick the top ``per_type`` (severity-sorted) of *each* concern type, capped at
    ``cap`` — so the report covers every anomaly type, not just the most frequent
    (deadline) one."""
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for c in concerns:  # already severity-sorted
        by_type.setdefault(c["type"], []).append(c)
    selected: List[Dict[str, Any]] = []
    for items in by_type.values():
        selected.extend(items[:per_type])
    selected.sort(key=lambda c: c["severity"], reverse=True)
    return selected[:cap]


def _format_concerns_for_prompt(selected: List[Dict[str, Any]], total: int) -> str:
    lines = [
        f"- [{c['type']}] {c['task_id']} — severity {c['severity']} — {c['explanation']}"
        for c in selected
    ]
    more = total - len(selected)
    if more > 0:
        lines.append(f"- … and {more} more concern(s) (see concerns.json).")
    return "\n".join(lines) if lines else "- (No concerns detected.)"


def _build_user_query(date_str: str, concerns: List[Dict[str, Any]]) -> str:
    selected = _select_diverse(concerns, per_type=4, cap=_MAX_CONCERNS_IN_PROMPT)
    concern_block = _format_concerns_for_prompt(selected, len(concerns))
    return (
        f"Generate the daily project intelligence report for {date_str}.\n\n"
        f"The deterministic Concern Engine flagged these risks (severity-sorted; "
        f"{len(concerns)} total):\n{concern_block}\n\n"
        "Instructions:\n"
        "- Ground the **Concerns** section in the items above. For the most severe "
        "ones, use `query_sqlite` to confirm the current state and `query_chroma` "
        "(source_filter='meeting_notes' or 'confluence') to add context, citing [source_id].\n"
        f"- Use `get_daily_diff` with date='{date_str}' for the **Changes Since** section.\n"
        "- Keep it concise and cite [source_id] for every claim."
    )


def _fallback_report(date_str: str, concerns: List[Dict[str, Any]], error: Exception) -> str:
    """Deterministic report from the Concern Engine when the LLM is unavailable."""
    selected = _select_diverse(concerns, per_type=5, cap=20)
    lines = [
        "## Overview",
        f"_LLM narrative unavailable ({type(error).__name__}); this is a deterministic "
        f"fallback generated directly from the Concern Engine for {date_str}._",
        "",
        f"{len(concerns)} concern(s) detected across stalled / deadline / blocker / "
        "cross-source-conflict rules.",
        "",
        "## Changes Since",
        "_(Requires the LLM agent; see output/concerns.json for the full risk list.)_",
        "",
        "## Concerns",
    ]
    for c in selected:
        lines.append(
            f"- [{c['type']}] {c['task_id']} (severity {c['severity']}): "
            f"{c['explanation']} [{c['task_id']}]"
        )
    lines += [
        "",
        "## Next Actions",
        "- Triage the highest-severity items above; full details in `output/concerns.json`.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(
    date_str: str,
    *,
    jira_path: str = DEFAULT_JIRA_PATH,
    conf_path: str = DEFAULT_CONFLUENCE_PATH,
    notes_path: str = DEFAULT_MEETING_NOTES_PATH,
    db_path: str = DB_PATH,
    chroma_path: str = CHROMA_PATH,
    output_dir: str = "output",
    skip_ingest: bool = False,
) -> Dict[str, Any]:
    os.makedirs(output_dir, exist_ok=True)

    # 1. Ingest -------------------------------------------------------------
    if skip_ingest:
        logger.info("Skipping ingestion (reusing existing stores).")
    else:
        logger.info("Ingesting sources into %s / %s ...", db_path, chroma_path)
        run_pipeline(jira_path, conf_path, notes_path, db_path=db_path, chroma_path=chroma_path)

    # 2. Concern Engine -> concerns.json ------------------------------------
    chroma = ChromaStore(path=chroma_path)
    with SQLiteStore(db_path=db_path) as store:
        concerns = ConcernEngine(as_of=date_str).run_all_rules(store, chroma)

        concerns_path = os.path.join(output_dir, "concerns.json")
        with open(concerns_path, "w", encoding="utf-8") as fh:
            json.dump(concerns, fh, ensure_ascii=False, indent=2)
        logger.info("Wrote %d concern(s) -> %s", len(concerns), concerns_path)

        # 3. Seed day-over-day history for the diff -------------------------
        seeded = _seed_prior_snapshots(store, concerns, date_str)
        logger.info("Seeded %d prior-day snapshot transition(s) for the diff.", seeded)

        # 4. Grounded Report Agent -> report.md -----------------------------
        user_query = _build_user_query(date_str, concerns)
        logger.info("Running Report Agent (grounded with %d concerns)...", len(concerns))
        try:
            report = run_report_agent(user_query, date_str, store, chroma)
        except Exception as exc:
            logger.error("Report Agent failed (%s); writing deterministic fallback report.", exc)
            report = _fallback_report(date_str, concerns, exc)

    report = OutputSanitizer().sanitize(report)
    report_path = os.path.join(output_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    logger.info("Wrote report -> %s", report_path)

    return {
        "date": date_str,
        "concerns": len(concerns),
        "seeded_changes": seeded,
        "report_path": report_path,
        "concerns_path": concerns_path,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Run the full agent demo end-to-end.")
    parser.add_argument("--date", default="2025-05-30", help="Reference (as-of) ISO date")
    parser.add_argument("--db-path", default=DB_PATH)
    parser.add_argument("--chroma-path", default=CHROMA_PATH)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--skip-ingest", action="store_true", help="Reuse existing stores (fast iteration)")
    args = parser.parse_args()

    from config import validate_config
    validate_config()  # fail fast if OPENAI_API_KEY missing

    stats = run(
        args.date,
        db_path=args.db_path,
        chroma_path=args.chroma_path,
        output_dir=args.output_dir,
        skip_ingest=args.skip_ingest,
    )
    print("\n=== Demo complete ===")
    print(f"  date            : {stats['date']}")
    print(f"  concerns        : {stats['concerns']}  -> {stats['concerns_path']}")
    print(f"  seeded changes  : {stats['seeded_changes']}")
    print(f"  report          : {stats['report_path']}")


if __name__ == "__main__":
    main()
