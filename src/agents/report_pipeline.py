"""Grounded report generation — shared by ``run_agent.py`` and the MCP server.

The Concern Engine's deterministic findings are turned into a prompt that
*grounds* the Report Agent (so its "Concerns" section is anchored to real
detections rather than free exploration); a deterministic fallback report is
produced if the LLM call fails, and the result always passes through
``OutputSanitizer`` before being handed back to a caller.

The report is organised around a PM's four daily questions — **Decisions Needed
Today** (led by cross-source conflicts + blockers), **Blocked**, **Deadlines at
Risk**, **Recent Changes** — under a one-line risk-count summary, with chronic
backlog folded into a single count. Concerns are pre-bucketed by type per section
(``bucket_by_type``) so the LLM fills anchored sections rather than re-classifying.
Language (vi/en) follows ``config.REPORT_LANG``.
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
_PER_SECTION = 8  # max concerns fed per question-section (the rest become "… and N more")

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


def bucket_by_type(concerns: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group severity-sorted concerns by type, one bucket per question-section.

    The report restructure maps each concern type to a PM question: blockers →
    "Blocked", deadlines → "Deadlines at Risk", conflicts lead "Decisions", and
    stalled → the "Backlog" count. Pre-bucketing here keeps the LLM from having to
    re-classify (deterministic-first), so each section is anchored to real rows.
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {t: [] for t in _TYPE_ORDER}
    for c in concerns:  # already severity-sorted
        buckets.setdefault(c["type"], []).append(c)
    return buckets


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
    # The task key is the ONLY bracketed token, so when the agent echoes a `[...]`
    # citation it lands on the task id (e.g. ``[KAFKA-64]``) — not the concern type.
    lines = [
        f"- [{c['task_id']}] ({c['type']}, severity {c['severity']}) — {c['explanation']}"
        for c in selected
    ]
    more = total - len(selected)
    if more > 0:
        lines.append(f"- … and {more} more concern(s) (see concerns.json).")
    return "\n".join(lines) if lines else "- (No concerns detected.)"


def build_user_query(date_str: str, concerns: List[Dict[str, Any]], lang: Optional[str] = None) -> str:
    """Build the grounding prompt, with concerns pre-bucketed per PM-question section.

    Each section of the report maps to one of a PM's four daily questions, so we
    feed the LLM a ready-made bucket per section (conflicts + top severity →
    Decisions, blockers → Blocked, deadlines → Deadlines) rather than one flat
    list it must re-classify.
    """
    lang = lang or REPORT_LANG
    vi = _is_vi(lang)
    buckets = bucket_by_type(concerns)
    counts, chronic = summarize_counts(concerns)
    summary_line = format_summary(counts, chronic, lang)

    actionable = select_actionable(concerns, _TOP_ACTIONABLE)
    conflicts = buckets["cross_source_conflict"]
    blockers = buckets["unresolved_blocker"]
    deadlines = buckets["deadline_risk"]
    stalled_total = len(buckets["stalled_task"])

    decisions_block = format_concerns_for_prompt(actionable, len(actionable))
    conflict_block = format_concerns_for_prompt(conflicts[:_PER_SECTION], len(conflicts))
    blocked_block = format_concerns_for_prompt(blockers[:_PER_SECTION], len(blockers))
    deadline_block = format_concerns_for_prompt(deadlines[:_PER_SECTION], len(deadlines))

    lang_name = "Vietnamese (Tiếng Việt)" if vi else "English"
    s_dec = "Cần bạn quyết hôm nay" if vi else "Decisions Needed Today"
    s_block = "Đang bị chặn" if vi else "Blocked"
    s_dead = "Deadline nguy hiểm" if vi else "Deadlines at Risk"
    s_chg = "Thay đổi gần đây" if vi else "Recent Changes"

    return (
        f"Generate the daily project intelligence report for {date_str}.\n\n"
        f"RISK SUMMARY (put as the first line of the report, before the first heading): "
        f"{summary_line}\n\n"
        f"CROSS-SOURCE CONFLICTS (Jira disagrees with a meeting/doc — LEAD the "
        f"'{s_dec}' section with these):\n{conflict_block}\n\n"
        f"TOP DECISION-READY items for '{s_dec}' (highest severity, chronic excluded):\n"
        f"{decisions_block}\n\n"
        f"BLOCKED tasks for the '{s_block}' section:\n{blocked_block}\n\n"
        f"DEADLINES at risk for the '{s_dead}' section (most urgent first):\n{deadline_block}\n\n"
        f"BACKLOG: {stalled_total} stalled task(s) (incl. {chronic} chronic) — summarise "
        f"as a single count, do not list each.\n\n"
        "Instructions:\n"
        f"- Write the ENTIRE report in {lang_name}.\n"
        "- For the most severe items (especially conflicts), use `query_sqlite` to confirm "
        "current state and `query_chroma` (source_filter='meeting_notes' or 'confluence') to "
        "add context, citing [source_id].\n"
        f"- Use `get_daily_diff` with date='{date_str}' for the '{s_chg}' section.\n"
        "- Keep it concise and cite [source_id] for every claim."
    )


def fallback_report(
    date_str: str,
    concerns: List[Dict[str, Any]],
    error: Exception,
    lang: Optional[str] = None,
) -> str:
    """Deterministic report from the Concern Engine when the LLM is unavailable.

    Mirrors the LLM report's PM-question structure (Decisions / Blocked / Deadlines
    / Recent Changes / Backlog) so a fallback is structurally indistinguishable —
    only the narrative prose and the live daily-diff are missing.
    """
    vi = _is_vi(lang)
    actionable = select_actionable(concerns, _TOP_ACTIONABLE)
    buckets = bucket_by_type(concerns)
    blockers = buckets["unresolved_blocker"][:_PER_SECTION]
    deadlines = buckets["deadline_risk"][:_PER_SECTION]
    stalled_total = len(buckets["stalled_task"])
    counts, chronic = summarize_counts(concerns)
    summary_line = format_summary(counts, chronic, lang)

    if vi:
        h = {
            "sum": "## Tóm tắt",
            "dec": "## ⚡ Cần bạn quyết hôm nay",
            "blk": "## 🚫 Đang bị chặn",
            "dl": "## ⏰ Deadline nguy hiểm",
            "chg": "## 🔄 Thay đổi gần đây",
            "bk": "## 📋 Tồn đọng",
        }
        overview = (
            f"_Không tạo được phần tường thuật bằng LLM ({type(error).__name__}); đây là báo "
            f"cáo dự phòng sinh trực tiếp từ Concern Engine cho ngày {date_str}._"
        )
        changes = "_(Cần LLM agent; xem output/concerns.json để biết danh sách đầy đủ.)_"
        none_txt = "- (Không có.)"
        backlog = f"- {stalled_total} task trì trệ (trong đó {chronic} kinh niên) — xem `output/concerns.json`."
    else:
        h = {
            "sum": "## Summary",
            "dec": "## ⚡ Decisions Needed Today",
            "blk": "## 🚫 Blocked",
            "dl": "## ⏰ Deadlines at Risk",
            "chg": "## 🔄 Recent Changes",
            "bk": "## 📋 Backlog",
        }
        overview = (
            f"_LLM narrative unavailable ({type(error).__name__}); this is a deterministic "
            f"fallback generated directly from the Concern Engine for {date_str}._"
        )
        changes = "_(Requires the LLM agent; see output/concerns.json for the full risk list.)_"
        none_txt = "- (None.)"
        backlog = f"- {stalled_total} stalled task(s) (incl. {chronic} chronic) — see `output/concerns.json`."

    def _bullets(items: List[Dict[str, Any]]) -> List[str]:
        if not items:
            return [none_txt]
        return [
            f"- [{c['type']}] {c['task_id']} (severity {c['severity']}): "
            f"{c['explanation']} [{c['task_id']}]"
            for c in items
        ]

    lines = [summary_line, "", h["sum"], overview, "", h["dec"]]
    for i, c in enumerate(actionable, 1):
        lines.append(
            f"{i}. [{c['type']}] {c['task_id']} (severity {c['severity']}): "
            f"{c['explanation']} [{c['task_id']}]"
        )
    if not actionable:
        lines.append(none_txt)
    lines += ["", h["blk"], *_bullets(blockers)]
    lines += ["", h["dl"], *_bullets(deadlines)]
    lines += ["", h["chg"], changes]
    lines += ["", h["bk"], backlog]
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
