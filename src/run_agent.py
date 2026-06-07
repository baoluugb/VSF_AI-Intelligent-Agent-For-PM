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
from agents.report_pipeline import generate_grounded_report
from ingestion.run_pipeline import (
    DEFAULT_CONFLUENCE_PATH,
    DEFAULT_JIRA_PATH,
    DEFAULT_MEETING_NOTES_PATH,
    run_pipeline,
)
from storage.chroma_store import ChromaStore
from storage.sqlite_store import SQLiteStore
from exporters import export_report_to_docx, export_concerns_to_excel

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
        logger.info("Running Report Agent (grounded with %d concerns)...", len(concerns))
        report = generate_grounded_report(date_str, concerns, store, chroma)

    report_path = os.path.join(output_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    logger.info("Wrote report -> %s", report_path)

    # 5. Export report to Word (.docx) & concerns to Excel (.xlsx) -----------
    report_docx_path = os.path.join(output_dir, "report.docx")
    concerns_xlsx_path = os.path.join(output_dir, "concerns.xlsx")

    try:
        logger.info("Exporting report to Word -> %s", report_docx_path)
        export_report_to_docx(report_path, report_docx_path)
    except Exception as exc:
        logger.error("Failed to export Word document: %s", exc)
        report_docx_path = None

    try:
        logger.info("Exporting concerns to Excel -> %s", concerns_xlsx_path)
        export_concerns_to_excel(concerns_path, concerns_xlsx_path)
    except Exception as exc:
        logger.error("Failed to export Excel spreadsheet: %s", exc)
        concerns_xlsx_path = None

    return {
        "date": date_str,
        "concerns": len(concerns),
        "seeded_changes": seeded,
        "report_path": report_path,
        "concerns_path": concerns_path,
        "report_docx_path": report_docx_path,
        "concerns_xlsx_path": concerns_xlsx_path,
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
    if stats.get("report_docx_path"):
        print(f"  report (word)   : {stats['report_docx_path']}")
    if stats.get("concerns_xlsx_path"):
        print(f"  concerns (excel): {stats['concerns_xlsx_path']}")


if __name__ == "__main__":
    main()
