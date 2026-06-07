"""Grounded report generation — shared by ``run_agent.py`` and the MCP server.

The Concern Engine's deterministic findings are turned into a prompt that
*grounds* the Report Agent (so its "Concerns" section is anchored to real
detections rather than free exploration); a deterministic fallback report is
produced if the LLM call fails, and the result always passes through
``OutputSanitizer`` before being handed back to a caller.

The report leads with a prioritised **"Priority Actions Today"** block (highest
severity, excluding chronic backlog) plus a one-line risk-count summary, so a PM
sees what to act on first. Language (vi/en) follows ``config.REPORT_LANG``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from config import JIRA_BASE_URL, REPORT_LANG
from agents.report_agent import run_report_agent
from guardrail.sanitizer import OutputSanitizer
from storage.chroma_store import ChromaStore
from storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

_MAX_CONCERNS_IN_PROMPT = 16
_TOP_ACTIONABLE = 5

# Human labels per concern type, per language, for the summary count line.
_TYPE_LABELS = {
    "vi": {
        "unresolved_blocker": "blocker",
        "deadline_risk": "quá hạn/sắp hết hạn",
        "stalled_task": "trì trệ",
        "cross_source_conflict": "xung đột nguồn",
    },
    "en": {
        "unresolved_blocker": "blocker",
        "deadline_risk": "deadline",
        "stalled_task": "stalled",
        "cross_source_conflict": "cross-source",
    },
}
_TYPE_ORDER = ["unresolved_blocker", "deadline_risk", "stalled_task", "cross_source_conflict"]

# Citation ids that look like Jira keys (PROJECT-123) but aren't Jira issues.
_NON_JIRA_PREFIXES = ("CONF", "MTG")
_JIRA_KEY_RE = re.compile(r"\[([A-Z][A-Z0-9]+-\d+)\]")


def _is_vi(lang: Optional[str]) -> bool:
    return (lang or REPORT_LANG or "vi").lower().startswith("vi")


def _is_chronic(concern: Dict[str, Any]) -> bool:
    return bool((concern.get("details") or {}).get("chronic"))


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


def select_actionable(concerns: List[Dict[str, Any]], top_n: int) -> List[Dict[str, Any]]:
    """Top ``top_n`` highest-severity concerns, excluding chronic backlog — the
    "what's on fire today" set that leads the report."""
    items = [c for c in concerns if not _is_chronic(c)]
    items.sort(key=lambda c: c["severity"], reverse=True)
    return items[:top_n]


def summarize_counts(concerns: List[Dict[str, Any]]) -> Tuple[Dict[str, int], int]:
    """Return ``(counts_by_type, chronic_count)``."""
    counts: Dict[str, int] = {}
    chronic = 0
    for c in concerns:
        counts[c["type"]] = counts.get(c["type"], 0) + 1
        if _is_chronic(c):
            chronic += 1
    return counts, chronic


def format_summary(counts: Dict[str, int], chronic: int, lang: Optional[str] = None) -> str:
    """One-line risk-count summary, e.g. 'Risk summary: 36 blocker · 66 deadline …'."""
    vi = _is_vi(lang)
    labels = _TYPE_LABELS["vi" if vi else "en"]
    parts = [f"{counts[t]} {labels[t]}" for t in _TYPE_ORDER if counts.get(t)]
    body = " · ".join(parts) if parts else ("Không có rủi ro" if vi else "no risks")
    if chronic:
        body += (f" (trong đó {chronic} trì trệ kinh niên)" if vi
                 else f" (incl. {chronic} chronic stalled)")
    return (f"Tổng quan rủi ro: {body}" if vi else f"Risk summary: {body}")


def format_concerns_for_prompt(selected: List[Dict[str, Any]], total: int) -> str:
    lines = [
        f"- [{c['type']}] {c['task_id']} — severity {c['severity']} — {c['explanation']}"
        for c in selected
    ]
    more = total - len(selected)
    if more > 0:
        lines.append(f"- … and {more} more concern(s) (see concerns.json).")
    return "\n".join(lines) if lines else "- (No concerns detected.)"


def build_user_query(date_str: str, concerns: List[Dict[str, Any]], lang: Optional[str] = None) -> str:
    lang = lang or REPORT_LANG
    vi = _is_vi(lang)
    actionable = select_actionable(concerns, _TOP_ACTIONABLE)
    selected = select_diverse(concerns, per_type=4, cap=_MAX_CONCERNS_IN_PROMPT)
    counts, chronic = summarize_counts(concerns)
    summary_line = format_summary(counts, chronic, lang)
    top_block = format_concerns_for_prompt(actionable, len(actionable))
    concern_block = format_concerns_for_prompt(selected, len(concerns))
    lang_name = "Vietnamese (Tiếng Việt)" if vi else "English"
    prio_section = "Cần xử lý hôm nay" if vi else "Priority Actions Today"
    return (
        f"Generate the daily project intelligence report for {date_str}.\n\n"
        f"RISK SUMMARY (counts, put as the first line of the report): {summary_line}\n\n"
        f"TOP ACTIONABLE risks (highest severity, chronic backlog excluded) — these go "
        f"in the '{prio_section}' section:\n{top_block}\n\n"
        f"ALL notable risks (severity-sorted; {len(concerns)} total) for the Concerns section:\n"
        f"{concern_block}\n\n"
        "Instructions:\n"
        f"- Write the ENTIRE report in {lang_name}.\n"
        "- For the most severe items, use `query_sqlite` to confirm the current state and "
        "`query_chroma` (source_filter='meeting_notes' or 'confluence') to add context, "
        "citing [source_id].\n"
        f"- Use `get_daily_diff` with date='{date_str}' for the changes section.\n"
        "- Treat 'chronic' stalled items as low priority: summarise them as a count, "
        "do not list each one.\n"
        "- Keep it concise and cite [source_id] for every claim."
    )


def fallback_report(
    date_str: str,
    concerns: List[Dict[str, Any]],
    error: Exception,
    lang: Optional[str] = None,
) -> str:
    """Deterministic report from the Concern Engine when the LLM is unavailable."""
    vi = _is_vi(lang)
    actionable = select_actionable(concerns, _TOP_ACTIONABLE)
    selected = select_diverse(concerns, per_type=5, cap=20)
    counts, chronic = summarize_counts(concerns)
    summary_line = format_summary(counts, chronic, lang)

    if vi:
        h = {
            "prio": "## Cần xử lý hôm nay",
            "ov": "## Tổng quan",
            "ch": "## Thay đổi hôm nay",
            "co": "## Rủi ro",
            "na": "## Hành động tiếp theo",
        }
        overview = (
            f"_Không tạo được phần tường thuật bằng LLM ({type(error).__name__}); đây là báo "
            f"cáo dự phòng sinh trực tiếp từ Concern Engine cho ngày {date_str}._"
        )
        changes = "_(Cần LLM agent; xem output/concerns.json để biết danh sách đầy đủ.)_"
        next_actions = (
            "- Ưu tiên xử lý các mục mức độ cao ở trên; chi tiết đầy đủ trong `output/concerns.json`."
        )
    else:
        h = {
            "prio": "## Priority Actions Today",
            "ov": "## Overview",
            "ch": "## Changes Today",
            "co": "## Concerns",
            "na": "## Next Actions",
        }
        overview = (
            f"_LLM narrative unavailable ({type(error).__name__}); this is a deterministic "
            f"fallback generated directly from the Concern Engine for {date_str}._"
        )
        changes = "_(Requires the LLM agent; see output/concerns.json for the full risk list.)_"
        next_actions = "- Triage the highest-severity items above; full details in `output/concerns.json`."

    lines = [h["prio"], summary_line, ""]
    for i, c in enumerate(actionable, 1):
        lines.append(
            f"{i}. [{c['type']}] {c['task_id']} (severity {c['severity']}): "
            f"{c['explanation']} [{c['task_id']}]"
        )
    lines += ["", h["ov"], overview, "", h["ch"], changes, "", h["co"]]
    for c in selected:
        lines.append(
            f"- [{c['type']}] {c['task_id']} (severity {c['severity']}): "
            f"{c['explanation']} [{c['task_id']}]"
        )
    lines += ["", h["na"], next_actions]
    return "\n".join(lines)


def linkify_jira(text: str, base_url: str) -> str:
    """Turn `[FLINK-40]` citations into clickable Jira links when ``base_url`` is
    set. Non-Jira ids (CONF-*, MTG-*) are left untouched. No-op if ``base_url`` is
    empty, so the default behaviour is unchanged."""
    if not base_url:
        return text
    base = base_url.rstrip("/")

    def _repl(m: "re.Match[str]") -> str:
        key = m.group(1)
        if key.split("-", 1)[0] in _NON_JIRA_PREFIXES:
            return m.group(0)
        return f"[{key}]({base}/browse/{key})"

    return _JIRA_KEY_RE.sub(_repl, text)


def generate_grounded_report(
    date_str: str,
    concerns: List[Dict[str, Any]],
    sqlite_store: SQLiteStore,
    chroma_store: ChromaStore,
    lang: Optional[str] = None,
) -> str:
    """Build a Concern-Engine-grounded prompt, run the Report Agent, and sanitize.

    Falls back to a deterministic Concern-Engine-only report if the LLM call
    raises (proxy throttling, network errors, etc.), so a caller never crashes.
    Always returns text that has passed through ``OutputSanitizer`` (secrets
    redacted) — safe to hand straight back to an API client or write to disk.
    Jira citations are linkified when ``config.JIRA_BASE_URL`` is set.
    """
    lang = lang or REPORT_LANG
    user_query = build_user_query(date_str, concerns, lang)
    try:
        report = run_report_agent(user_query, date_str, sqlite_store, chroma_store, lang=lang)
    except Exception as exc:
        logger.error(
            "Report Agent failed (%s); using deterministic fallback report.", exc)
        report = fallback_report(date_str, concerns, exc, lang)

    report = OutputSanitizer().sanitize(report)
    return linkify_jira(report, JIRA_BASE_URL)
