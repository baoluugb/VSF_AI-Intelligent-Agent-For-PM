"""Slack delivery (P0 adoption MVP) — post the daily risk digest to a Slack
Incoming Webhook so a PM gets it where they already work.

The digest is built from the **structured concern list** (not by parsing the
Markdown report), reusing the report pipeline's canonical count/summary/ranking
helpers so the Slack text matches the report exactly. Delivery is opt-in: with
no ``webhook_url`` it is a logged no-op, so the default ``run_agent.sh`` flow is
unchanged.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from agents.report_pipeline import (
    format_summary,
    select_actionable,
    summarize_counts,
)

logger = logging.getLogger(__name__)

_TOP_N = 5  # decision-ready items to list in the digest
_TIMEOUT_S = 10.0


def build_digest_text(
    date_str: str,
    concerns: List[Dict[str, Any]],
    lang: Optional[str] = None,
    report_url: Optional[str] = None,
) -> str:
    """Build the Slack message: header + one-line risk summary + top-N actions."""
    counts, chronic = summarize_counts(concerns)
    summary = format_summary(counts, chronic, lang)

    lines = [f"*Project Intelligence — {date_str}*", summary]

    top = select_actionable(concerns, _TOP_N)
    if top:
        lines.append("")
        for i, c in enumerate(top, 1):
            lines.append(
                f"{i}. [{c['task_id']}] (sev {c['severity']}) {c['explanation']}"
            )
    if report_url:
        lines += ["", f"Full report: {report_url}"]
    return "\n".join(lines)


def post_concerns_digest(
    date_str: str,
    concerns: List[Dict[str, Any]],
    webhook_url: Optional[str],
    *,
    lang: Optional[str] = None,
    report_url: Optional[str] = None,
) -> bool:
    """Post the digest to ``webhook_url``. No-op (returns False) when unset.

    Raises on HTTP error so the caller can decide whether a delivery failure
    should be fatal; ``run_agent`` wraps this so a failed post never crashes the
    pipeline.
    """
    if not webhook_url:
        logger.info("SLACK_WEBHOOK_URL not set — skipping Slack delivery.")
        return False

    text = build_digest_text(date_str, concerns, lang=lang, report_url=report_url)
    resp = httpx.post(webhook_url, json={"text": text}, timeout=_TIMEOUT_S)
    resp.raise_for_status()
    logger.info("Posted daily digest to Slack (%d concern(s)).", len(concerns))
    return True
