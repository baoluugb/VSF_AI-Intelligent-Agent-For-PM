"""One-command demo runner (Week 6): ingest → concerns → grounded report → output/.

Pipeline
--------
1. Ingest the 3 synthetic sources into the dual store (``run_pipeline``),
   tagging this run's snapshots with ``--date``. Snapshots are written only for
   entities that changed since the prior run (incremental), so the day-over-day
   diff is real — empty on the first/baseline run or when nothing changed.
2. Run the **Concern Engine** → ``output/concerns.json`` (deterministic, severity-sorted).
3. Run the **Report Agent**, *grounded* with the top concerns, → ``output/report.md``
   (passed through ``OutputSanitizer`` to redact any leaked secrets).

By default the stores **persist** between runs so snapshot history accumulates;
pass ``--reset`` for a clean baseline (what ``run_agent.sh`` does).

Usage::

    python src/run_agent.py --date 2025-05-30 --reset       # clean baseline
    python src/run_agent.py --date 2025-05-31                # next day, accumulates
    python src/run_agent.py --date 2025-05-30 --skip-ingest  # reuse existing stores
"""
from __future__ import annotations

import os
import sys

# --- Make the module runnable from any entry point --------------------------
# This MUST run before the local imports below: they (transitively) do
# ``from config import ...`` and ``config.py`` lives at the repo root, so the
# repo root has to be on sys.path *before* those imports are evaluated.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))   # .../src
_ROOT_DIR = os.path.dirname(_THIS_DIR)                    # repo root
for _p in (_ROOT_DIR, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import argparse
import json
import logging
import shutil
from typing import Any, Dict

from exporters import export_report_to_docx, export_concerns_to_excel
from storage.sqlite_store import SQLiteStore
from storage.chroma_store import ChromaStore
from ingestion.run_pipeline import (
    DEFAULT_CONFLUENCE_PATH,
    DEFAULT_JIRA_PATH,
    DEFAULT_MEETING_NOTES_PATH,
    run_pipeline,
)
from agents.report_pipeline import generate_grounded_report
from agents.concern_engine import ConcernEngine
from agents.insights import aggregate_by_assignee, compute_concern_delta, weekly_changes
from delivery.slack import post_concerns_digest
from config import CHROMA_PATH, DB_PATH, REPORT_LANG, SLACK_WEBHOOK_URL


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_stores(db_path: str, chroma_path: str) -> None:
    """Delete the dual store for a clean baseline run (``--reset``).

    Replaces the manual ``rm`` that ``run_agent.sh`` used to do, so the reset is
    opt-in and lives next to the code that depends on it. Without ``--reset`` the
    stores persist across runs, accumulating snapshot history for the diff.
    """
    if os.path.exists(db_path):
        os.remove(db_path)
    if os.path.isdir(chroma_path):
        shutil.rmtree(chroma_path)
    logger.info("Reset stores: removed %s and %s", db_path, chroma_path)


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
    reset: bool = False,
) -> Dict[str, Any]:
    os.makedirs(output_dir, exist_ok=True)

    # 0. Optional clean baseline -------------------------------------------
    if reset and not skip_ingest:
        _reset_stores(db_path, chroma_path)

    # 1. Ingest (incremental: snapshots tagged with date_str) ---------------
    if skip_ingest:
        logger.info("Skipping ingestion (reusing existing stores).")
    else:
        logger.info("Ingesting sources into %s / %s (as_of=%s) ...",
                    db_path, chroma_path, date_str)
        run_pipeline(jira_path, conf_path, notes_path, db_path=db_path,
                     chroma_path=chroma_path, as_of=date_str)

    # 2. Concern Engine -> concerns.json ------------------------------------
    chroma = ChromaStore(path=chroma_path)
    with SQLiteStore(db_path=db_path) as store:
        concerns = ConcernEngine(as_of=date_str).run_all_rules(store, chroma)

        concerns_path = os.path.join(output_dir, "concerns.json")
        with open(concerns_path, "w", encoding="utf-8") as fh:
            json.dump(concerns, fh, ensure_ascii=False, indent=2)
        logger.info("Wrote %d concern(s) -> %s", len(concerns), concerns_path)

        # 2b. Concern delta vs the previous run — read the prior set BEFORE
        # saving today's, so the report/Slack digest LEADS with what changed.
        prior_concerns = store.load_prior_concerns(date_str)
        delta = compute_concern_delta(prior_concerns, concerns)
        store.save_concern_snapshot(date_str, concerns)

        # 2c. PM rollups & trends -> insights.json --------------------------
        # Owner rollup ("who carries the most risk") + "what changed since last
        # week" (meaningful once daily runs accumulate snapshot history).
        insights = {
            "date": date_str,
            "delta": delta,
            "by_assignee": aggregate_by_assignee(concerns),
            "changes_since_last_week": weekly_changes(store, date_str, 7),
        }
        insights_path = os.path.join(output_dir, "insights.json")
        with open(insights_path, "w", encoding="utf-8") as fh:
            json.dump(insights, fh, ensure_ascii=False, indent=2)
        logger.info("Wrote insights -> %s", insights_path)

        # 3. Grounded Report Agent -> report.md -----------------------------
        # The "Recent Changes" section is a real day-over-day diff (get_daily_diff
        # over the accumulated snapshots) — empty when nothing changed since the
        # prior run.
        logger.info("Running Report Agent (grounded with %d concerns)...", len(concerns))
        report = generate_grounded_report(date_str, concerns, store, chroma, delta=delta)

    report_path = os.path.join(output_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    logger.info("Wrote report -> %s", report_path)

    # 4. Optional delivery: post a digest to Slack when configured -----------
    # Opt-in via SLACK_WEBHOOK_URL; a failed post is logged but never crashes
    # the daily run (mirrors the export error handling below).
    slack_delivered = False
    if SLACK_WEBHOOK_URL:
        try:
            slack_delivered = post_concerns_digest(
                date_str, concerns, SLACK_WEBHOOK_URL, lang=REPORT_LANG, delta=delta)
        except Exception as exc:
            logger.error("Slack delivery failed: %s", exc)

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
        "report_path": report_path,
        "concerns_path": concerns_path,
        "insights_path": insights_path,
        "report_docx_path": report_docx_path,
        "concerns_xlsx_path": concerns_xlsx_path,
        "slack_delivered": slack_delivered,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Run the full agent demo end-to-end.")
    parser.add_argument("--date", default="2025-05-30",
                        help="Reference (as-of) ISO date")
    parser.add_argument("--db-path", default=DB_PATH)
    parser.add_argument("--chroma-path", default=CHROMA_PATH)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="Reuse existing stores (fast iteration)")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Wipe the stores for a clean baseline before ingesting (omit to accumulate snapshot history)",
    )
    args = parser.parse_args()

    from config import validate_config
    validate_config()  # fail fast if OPENAI_API_KEY missing

    stats = run(
        args.date,
        db_path=args.db_path,
        chroma_path=args.chroma_path,
        output_dir=args.output_dir,
        skip_ingest=args.skip_ingest,
        reset=args.reset,
    )
    print("\n=== Demo complete ===")
    print(f"  date            : {stats['date']}")
    print(f"  concerns        : {stats['concerns']}  -> {stats['concerns_path']}")
    print(f"  report          : {stats['report_path']}")
    if stats.get("report_docx_path"):
        print(f"  report (word)   : {stats['report_docx_path']}")
    if stats.get("concerns_xlsx_path"):
        print(f"  concerns (excel): {stats['concerns_xlsx_path']}")


if __name__ == "__main__":
    main()
