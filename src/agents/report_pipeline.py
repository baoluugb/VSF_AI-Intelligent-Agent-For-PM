"""Grounded report generation — shared by ``run_agent.py`` and the MCP server.

The Concern Engine's deterministic findings are turned into a prompt that
*grounds* the Report Agent (so its "Concerns" section is anchored to real
detections rather than free exploration); a deterministic fallback report is
produced if the LLM call fails, and the result always passes through
``OutputSanitizer`` before being handed back to a caller.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from agents.report_agent import run_report_agent
from guardrail.sanitizer import OutputSanitizer
from storage.chroma_store import ChromaStore
from storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

_MAX_CONCERNS_IN_PROMPT = 16


def select_diverse(concerns: List[Dict[str, Any]], per_type: int, cap: int) -> List[Dict[str, Any]]:
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


def format_concerns_for_prompt(selected: List[Dict[str, Any]], total: int) -> str:
    lines = [
        f"- [{c['type']}] {c['task_id']} — severity {c['severity']} — {c['explanation']}"
        for c in selected
    ]
    more = total - len(selected)
    if more > 0:
        lines.append(f"- … and {more} more concern(s) (see concerns.json).")
    return "\n".join(lines) if lines else "- (No concerns detected.)"


def build_user_query(date_str: str, concerns: List[Dict[str, Any]]) -> str:
    selected = select_diverse(concerns, per_type=4, cap=_MAX_CONCERNS_IN_PROMPT)
    concern_block = format_concerns_for_prompt(selected, len(concerns))
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


def fallback_report(date_str: str, concerns: List[Dict[str, Any]], error: Exception) -> str:
    """Deterministic report from the Concern Engine when the LLM is unavailable."""
    selected = select_diverse(concerns, per_type=5, cap=20)
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


def generate_grounded_report(
    date_str: str,
    concerns: List[Dict[str, Any]],
    sqlite_store: SQLiteStore,
    chroma_store: ChromaStore,
) -> str:
    """Build a Concern-Engine-grounded prompt, run the Report Agent, and sanitize.

    Falls back to a deterministic Concern-Engine-only report if the LLM call
    raises (proxy throttling, network errors, etc.), so a caller never crashes.
    Always returns text that has passed through ``OutputSanitizer`` (secrets
    redacted) — safe to hand straight back to an API client or write to disk.
    """
    user_query = build_user_query(date_str, concerns)
    try:
        report = run_report_agent(user_query, date_str, sqlite_store, chroma_store)
    except Exception as exc:
        logger.error(
            "Report Agent failed (%s); using deterministic fallback report.", exc)
        report = fallback_report(date_str, concerns, exc)

    return OutputSanitizer().sanitize(report)
