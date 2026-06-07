"""Report Agent — OpenAI SDK ReAct loop with citation enforcement.

Public surface
--------------
``SYSTEM_PROMPT``       : citation-enforcing system prompt.
``run_report_agent()``  : the ReAct loop that drives the OpenAI model + tools.

The agent uses hand-written OpenAI Function Calling (no LangChain).  Each
iteration sends the running message history to the model; if the model asks for
a tool it is executed via :func:`agents.tools.dispatch_tool` and the result is
fed back; once the model answers in plain text the report is returned.
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

import json
import logging
import time
from datetime import date as _date
from typing import Any, Dict, List

import openai

# HTTP statuses worth retrying (rate limits, transient upstream/proxy errors).
_RETRYABLE_STATUS = {403, 408, 409, 425, 429, 500, 502, 503, 504}

from config import (
    CHROMA_PATH,
    MAX_AGENT_ITERATIONS,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    REPORT_LANG,
)
from agents.tools import TOOLS, dispatch_tool
from storage.chroma_store import ChromaStore
from storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

# Model is read from config (.env: OPENAI_MODEL) so it can target the
# ckey.vn-hosted model without code changes. Defaults to gpt-4o-mini.
MODEL = OPENAI_MODEL


# ---------------------------------------------------------------------------
# System prompt — citation enforcement + output contract
# ---------------------------------------------------------------------------

_PROMPT_EN = """\
You are the AI Project Intelligence Agent — a senior project analyst that writes \
concise, accurate daily status reports for a software engineering team.

You have three tools to gather facts:
  - `get_daily_diff(date)`  → tasks whose status/assignee changed today.
  - `query_sqlite(entity_id)` → the authoritative current state of one task.
  - `query_chroma(query, source_filter, epic_filter)` → semantic search over \
Confluence design docs and Meeting Notes.

## CITATION RULES (MANDATORY)
Every factual claim you write MUST be followed immediately by a citation of the \
form `[source_id]`, where `source_id` is taken from the `source_ids` list (or the \
`result` metadata) returned by a tool. Cite the exact id you were given.

  ✅ "AIP-45 is still 'In Progress' as of today [AIP-45]."
  ✅ "The team chose ChromaDB for semantic search [CONF-012]."
  ❌ "AIP-45 appears to be stuck."          ← no source_id
  ❌ "According to the meeting, ..."          ← vague, no [id]

## NO HALLUCINATION
Only state things that appear in a tool result. If a tool returns empty results \
(no rows, `found: false`, or an empty list), DO NOT invent or guess. Write \
"(No verified data found.)" for that item instead. Never fabricate task ids, \
dates, names, or statuses.

## OUTPUT FORMAT
Return a Markdown report with EXACTLY these five sections, in this order:

## Priority Actions Today
Begin with a one-line summary count of risks by type (provided to you). Then a \
numbered list (max 5) of the highest-severity, decision-ready items the PM must \
act on today — each with an owner and a `[source_id]`. Do NOT put low-priority \
"chronic" backlog items here.

## Overview
A 2-3 sentence executive summary of the day.

## Changes Today
Bullet list of status/assignee changes from `get_daily_diff`. If none, say so.

## Concerns
Tasks that are at risk (stale, near deadline, blocked) or that conflict across \
sources (e.g. Jira says Done but a meeting note says pending). Group by type; \
summarise low-priority chronic backlog as a single count rather than listing each.

## Next Actions
Clear, owner-tagged next steps.

Every bullet and every claim must end with at least one `[source_id]` citation.
"""

_PROMPT_VI = """\
Bạn là AI Project Intelligence Agent — một chuyên viên phân tích dự án cấp cao, \
viết báo cáo trạng thái hằng ngày ngắn gọn, chính xác cho một nhóm kỹ thuật phần mềm.

Bạn có ba công cụ để thu thập dữ kiện:
  - `get_daily_diff(date)`  → các task đổi trạng thái/người phụ trách trong ngày.
  - `query_sqlite(entity_id)` → trạng thái hiện tại chính xác của một task.
  - `query_chroma(query, source_filter, epic_filter)` → tìm kiếm ngữ nghĩa trong \
tài liệu Confluence và Meeting Notes.

## QUY TẮC TRÍCH DẪN (BẮT BUỘC)
Mọi phát biểu mang tính dữ kiện PHẢI kèm ngay sau một trích dẫn dạng `[source_id]`, \
trong đó `source_id` lấy từ danh sách `source_ids` (hoặc metadata `result`) mà công \
cụ trả về. Trích đúng id được cấp.

  ✅ "AIP-45 vẫn 'In Progress' tính đến hôm nay [AIP-45]."
  ❌ "AIP-45 có vẻ đang bị kẹt."   ← thiếu source_id

## KHÔNG BỊA (NO HALLUCINATION)
Chỉ nêu những gì xuất hiện trong kết quả công cụ. Nếu công cụ trả về rỗng (không có \
dòng nào, `found: false`, hoặc danh sách rỗng), KHÔNG được bịa hay suy đoán. Hãy ghi \
"(Không tìm thấy dữ liệu xác thực.)" cho mục đó. Tuyệt đối không bịa id, ngày, tên, trạng thái.

## ĐỊNH DẠNG ĐẦU RA
Trả về một báo cáo Markdown với ĐÚNG năm mục sau, theo đúng thứ tự này, VIẾT BẰNG TIẾNG VIỆT:

## Cần xử lý hôm nay
Mở đầu bằng một dòng tóm tắt số lượng rủi ro theo loại (đã cung cấp cho bạn). Sau đó \
là danh sách đánh số (tối đa 5) những mục mức độ cao nhất, sẵn sàng ra quyết định mà PM \
cần xử lý ngay hôm nay — mỗi mục kèm người phụ trách và `[source_id]`. KHÔNG đưa các mục \
tồn đọng "kinh niên" mức độ thấp vào đây.

## Tổng quan
Tóm tắt điều hành 2-3 câu về tình hình trong ngày.

## Thay đổi hôm nay
Liệt kê các thay đổi trạng thái/người phụ trách từ `get_daily_diff`. Nếu không có, hãy nói rõ.

## Rủi ro
Các task có rủi ro (trì trệ, gần hạn, bị chặn) hoặc xung đột giữa các nguồn (vd Jira ghi \
Done nhưng meeting note ghi đang pending). Nhóm theo loại; gộp phần tồn đọng kinh niên mức \
thấp thành một con số tổng thay vì liệt kê từng cái.

## Hành động tiếp theo
Các bước tiếp theo rõ ràng, gắn người phụ trách.

Mọi gạch đầu dòng và mọi phát biểu phải kết thúc bằng ít nhất một trích dẫn `[source_id]`.
"""


def build_system_prompt(lang: str | None = None) -> str:
    """Return the system prompt for ``lang`` ("vi"/"en", default REPORT_LANG)."""
    chosen = (lang or REPORT_LANG or "en").lower()
    return _PROMPT_VI if chosen.startswith("vi") else _PROMPT_EN


# Backward-compatible default (English) — the canonical citation/format contract
# asserted by the tests. Localised prompts are built via ``build_system_prompt``.
SYSTEM_PROMPT = _PROMPT_EN


# ---------------------------------------------------------------------------
# OpenAI client
# ---------------------------------------------------------------------------

def _make_client() -> "openai.OpenAI":
    """Build an OpenAI client from config (supports an OpenAI-compatible proxy)."""
    client_kwargs: Dict[str, Any] = {"api_key": OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")}
    if OPENAI_BASE_URL:
        client_kwargs["base_url"] = OPENAI_BASE_URL
    return openai.OpenAI(**client_kwargs)


def _create_with_retry(client: "openai.OpenAI", *, max_attempts: int = 4, **kwargs: Any):
    """Call chat.completions.create, retrying transient errors with backoff.

    The ckey.vn proxy occasionally returns a transient ``403`` ("upstream rejected"),
    plus the usual ``429``/``5xx`` — these are retried; other errors propagate.
    """
    delay = 2.0
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            last_exc = exc
        except openai.APIStatusError as exc:
            if exc.status_code not in _RETRYABLE_STATUS:
                raise
            last_exc = exc
        if attempt < max_attempts:
            logger.warning(
                "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt, max_attempts, last_exc, delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, 20.0)
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# ReAct loop
# ---------------------------------------------------------------------------

def run_report_agent(
    user_query: str,
    date: str,
    sqlite_store: SQLiteStore,
    chroma_store: ChromaStore,
    *,
    lang: str | None = None,
    system_prompt: str | None = None,
) -> str:
    """Run the Report Agent ReAct loop and return the final Markdown report.

    Parameters
    ----------
    user_query:
        The instruction from the user / orchestrator, e.g.
        ``"Generate today's project status report."``.
    date:
        ISO date (YYYY-MM-DD) that scopes the daily diff.
    sqlite_store:
        An open :class:`storage.sqlite_store.SQLiteStore`.
    chroma_store:
        An open :class:`storage.chroma_store.ChromaStore`.
    lang:
        Report language ("vi"/"en", default :data:`config.REPORT_LANG`); selects
        the localised system prompt. Ignored if ``system_prompt`` is given.
    system_prompt:
        Explicit system prompt override (mainly for tests).

    Returns
    -------
    str
        The agent's Markdown report. If the agent exhausts
        ``MAX_AGENT_ITERATIONS`` it returns the best partial report it can
        produce, followed by an explicit caveat.
    """
    client = _make_client()
    sys_prompt = system_prompt or build_system_prompt(lang)

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": sys_prompt},
        {
            "role": "user",
            "content": (
                f"{user_query}\n\n"
                f"[Report date: {date}. Start by calling get_daily_diff with "
                f"date='{date}' to see what changed today.]"
            ),
        },
    ]

    logger.info("ReportAgent start | date=%s | max_iter=%d", date, MAX_AGENT_ITERATIONS)

    for iteration in range(1, MAX_AGENT_ITERATIONS + 1):
        response = _create_with_retry(
            client,
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

        # No tool call → the agent has produced its final answer.
        if not msg.tool_calls:
            logger.info("ReportAgent finished in %d iteration(s).", iteration)
            return msg.content or "(Agent returned an empty report.)"

        # Record the assistant turn (carries the tool_calls) before answering them.
        messages.append(msg)

        # Execute every requested tool call and feed the result back.
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args: Dict[str, Any] = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                logger.warning("Bad tool arguments for %s: %s", name, exc)
                args = {}

            # Required: log the called tool's name and arguments each iteration.
            logger.info("[ReAct iter %d/%d] tool=%s args=%s", iteration, MAX_AGENT_ITERATIONS, name, args)

            result = dispatch_tool(name, args, sqlite_store, chroma_store)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )

    # Max iterations reached → force a final text report from what we have.
    logger.warning("ReportAgent hit MAX_AGENT_ITERATIONS=%d without finishing.", MAX_AGENT_ITERATIONS)
    return _finalize_partial(client, messages)


def _finalize_partial(client: "openai.OpenAI", messages: List[Dict[str, Any]]) -> str:
    """Force one tool-free completion to salvage a partial report, then caveat it."""
    caveat = (
        "\n\n---\n"
        f"> ⚠️ **Caveat:** the agent reached the {MAX_AGENT_ITERATIONS}-iteration "
        "limit, so this report is **incomplete** and may be missing data. "
        "Increase `MAX_AGENT_ITERATIONS` in config.py or narrow the query."
    )

    partial = ""
    try:
        final = client.chat.completions.create(
            model=MODEL,
            messages=messages
            + [
                {
                    "role": "user",
                    "content": (
                        "Stop calling tools. Using ONLY the information already gathered "
                        "above, write the best report you can now, in the required format. "
                        "Cite [source_id] for every claim."
                    ),
                }
            ],
            tool_choice="none",
        )
        partial = final.choices[0].message.content or ""
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to finalize partial report: %s", exc)

    if not partial.strip():
        partial = "_(No report could be generated within the iteration limit.)_"

    return partial + caveat


# ---------------------------------------------------------------------------
# CLI — python src/agents/report_agent.py --date 2025-05-21
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse

    from config import validate_config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Run the Report Agent for a given date.")
    parser.add_argument("--date", default=_date.today().isoformat(), help="ISO date (YYYY-MM-DD)")
    parser.add_argument(
        "--query",
        default="Generate the full project status report for today.",
        help="User query / instruction for the agent.",
    )
    args = parser.parse_args()

    validate_config()  # fail fast if OPENAI_API_KEY is missing

    with SQLiteStore() as sqlite_store:
        chroma_store = ChromaStore(path=CHROMA_PATH)
        report = run_report_agent(args.query, args.date, sqlite_store, chroma_store)

    # The report goes to stdout so `... > output/report.md` works; logs go to stderr.
    print(report)


if __name__ == "__main__":
    _main()
