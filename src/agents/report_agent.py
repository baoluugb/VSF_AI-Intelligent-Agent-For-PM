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
from datetime import date as _date
from typing import Any, Dict, List

import openai

from config import (
    CHROMA_PATH,
    MAX_AGENT_ITERATIONS,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
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

SYSTEM_PROMPT = """\
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
Return a Markdown report with EXACTLY these four sections, in this order:

## Overview
A 2-3 sentence executive summary of the day.

## Changes Today
Bullet list of status/assignee changes from `get_daily_diff`. If none, say so.

## Concerns
Tasks that are at risk (stale, near deadline, blocked) or that conflict across \
sources (e.g. Jira says Done but a meeting note says pending).

## Next Actions
Clear, owner-tagged next steps.

Every bullet and every claim must end with at least one `[source_id]` citation.
"""


# ---------------------------------------------------------------------------
# OpenAI client
# ---------------------------------------------------------------------------

def _make_client() -> "openai.OpenAI":
    """Build an OpenAI client from config (supports an OpenAI-compatible proxy)."""
    client_kwargs: Dict[str, Any] = {"api_key": OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")}
    if OPENAI_BASE_URL:
        client_kwargs["base_url"] = OPENAI_BASE_URL
    return openai.OpenAI(**client_kwargs)


# ---------------------------------------------------------------------------
# ReAct loop
# ---------------------------------------------------------------------------

def run_report_agent(
    user_query: str,
    date: str,
    sqlite_store: SQLiteStore,
    chroma_store: ChromaStore,
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

    Returns
    -------
    str
        The agent's Markdown report. If the agent exhausts
        ``MAX_AGENT_ITERATIONS`` it returns the best partial report it can
        produce, followed by an explicit caveat.
    """
    client = _make_client()

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
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
        response = client.chat.completions.create(
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
