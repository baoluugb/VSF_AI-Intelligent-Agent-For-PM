"""Report Agent — OpenAI SDK ReAct loop with citation enforcement.

This module provides:
  - ``SYSTEM_PROMPT``      : citation-enforcing system prompt (Task 3.3)
  - ``run_report_agent()`` : the ~50-line ReAct loop (Task 3.2)
  - ``ReportAgent``        : thin class wrapper (for import compatibility with main.py)

Architecture
------------
The agent uses *OpenAI Function Calling* with a hand-written ReAct loop instead
of LangChain.  Every iteration:
  1. Send the full conversation history (system + user + previous tool results)
     to the OpenAI Chat Completions API.
  2. If the model returns tool_calls → execute them via ``dispatch_tool``,
     append results as ``role="tool"`` messages, continue.
  3. If the model returns a plain text response → done, return it.
  4. Safety net: stop after ``max_iterations`` rounds to prevent infinite loops.

Citation Enforcement (Task 3.3)
--------------------------------
The system prompt (``SYSTEM_PROMPT``) is the primary citation guardrail:
  - Every factual claim MUST be followed by a ``[source_id]`` taken from the
    ``source_id`` field in the tool result.
  - Claims without a verifiable ``source_id`` are FORBIDDEN.
  - The agent is also instructed to prefer ``query_sqlite`` for definitive task
    state and ``query_chroma`` for narrative / contextual information.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional

import openai

from config import MAX_AGENT_ITERATIONS, OPENAI_API_KEY, CHROMA_PATH, DB_PATH, OPENAI_BASE_URL, OPENAI_MODEL
from storage.chroma_store import ChromaStore
from storage.sqlite_store import SQLiteStore
from tools.registry import TOOLS, dispatch_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Task 3.3 — Citation enforcement: System Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the AI Project Intelligence Agent — a senior project analyst that \
generates concise, accurate daily status reports for a software engineering team.

## ROLE
Synthesise information from three sources:
  1. **Jira** (authoritative task status) — accessed via `query_sqlite`
  2. **Confluence** (design docs, decisions) — accessed via `query_chroma`
  3. **Meeting Notes** (action items, verbal commitments) — accessed via `query_chroma`

## CITATION RULES (MANDATORY — non-negotiable)
Every factual claim you make MUST be followed immediately by a citation in the \
format **[source_id]** where `source_id` is the exact value of the `source_id` \
field returned by the tool that provided the fact.

  ✅ CORRECT : "Task AIP-45 is still 'In Progress' as of today [AIP-45]."
  ✅ CORRECT : "The architecture decision to use ChromaDB was documented in \
[CONF-012]."
  ✅ CORRECT : "Minh Tuan committed to finishing the pipeline by 2025-05-24 \
[MTG-2025-05-21]."

  ❌ FORBIDDEN : "Task AIP-45 appears to be stuck."  ← no source_id cited
  ❌ FORBIDDEN : "According to the meeting, ..."       ← vague reference, no [id]
  ❌ FORBIDDEN : Asserting anything you did not retrieve via a tool call.

If you cannot find a verifiable source for a claim, DO NOT write that claim. \
Instead write: "(No verified data found for this item.)"

## TOOL USAGE STRATEGY
- Use `get_daily_diff` FIRST to understand what changed since yesterday.
- Use `query_sqlite` to confirm the current state of any specific task mentioned.
- Use `query_chroma` with `source_filter="meeting_notes"` to check for action \
items and commitments that may conflict with Jira status.
- Use `query_chroma` with `source_filter="confluence"` to surface relevant \
design context when explaining a risk or decision.

## REPORT FORMAT
Produce a Markdown report with these sections:
1. **Summary** — 2-3 sentence executive overview of the day
2. **Changes Since Yesterday** — bullet list of status/assignee changes (from \
`get_daily_diff`)
3. **At-Risk Tasks** — tasks with deadline risk, staleness, or blockers
4. **Key Decisions & Context** — relevant Confluence pages or meeting notes
5. **Action Items** — clear next steps with owners

Every bullet must end with at least one [source_id] citation.
"""

# ---------------------------------------------------------------------------
# Task 3.2 — ReAct Loop
# ---------------------------------------------------------------------------


def run_report_agent(
    user_query: str,
    *,
    report_date: Optional[str] = None,
    max_iterations: int = MAX_AGENT_ITERATIONS,
    sqlite_store: Optional[SQLiteStore] = None,
    chroma_store: Optional[ChromaStore] = None,
    model: str = OPENAI_MODEL,
) -> str:
    """Run the Report Agent ReAct loop and return the final report string.

    Parameters
    ----------
    user_query:
        The high-level question or instruction from the user / orchestrator.
        Example: ``"Generate today's project status report."``.
    report_date:
        ISO date (YYYY-MM-DD) that scopes the daily diff.  Defaults to today.
    max_iterations:
        Safety cap on the number of think-act cycles (default from config).
    sqlite_store:
        Pre-opened SQLite store (for testing or shared sessions).  A new one is
        created if not provided.
    chroma_store:
        Pre-opened Chroma store (for testing or shared sessions).  A new one is
        created if not provided.
    model:
        OpenAI model identifier.

    Returns
    -------
    str
        The agent's final Markdown report, or an error/timeout message.
    """
    api_key = OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
    client_kwargs = {"api_key": api_key}
    if OPENAI_BASE_URL:
        client_kwargs["base_url"] = OPENAI_BASE_URL
    client = openai.OpenAI(**client_kwargs)

    # Resolve report date
    target_date = report_date or date.today().isoformat()

    # Augment user query with the target date so the agent knows which diff to pull
    enriched_query = (
        f"{user_query}\n\n"
        f"[Context: today's date is {target_date}. "
        f"Use get_daily_diff with date='{target_date}' to see what changed.]"
    )

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": enriched_query},
    ]

    # Lazy-init stores so callers that inject their own stores pay no overhead
    _sqlite: Optional[SQLiteStore] = sqlite_store
    _chroma: Optional[ChromaStore] = chroma_store

    logger.info("ReportAgent starting | date=%s | max_iter=%d", target_date, max_iterations)

    for iteration in range(1, max_iterations + 1):
        logger.debug("ReAct iteration %d / %d", iteration, max_iterations)

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        msg = response.choices[0].message

        # ── No more tool calls → agent has enough information, return answer ──
        if not msg.tool_calls:
            logger.info("ReportAgent finished in %d iterations.", iteration)
            return msg.content or "(Agent returned an empty response.)"

        # ── Append the assistant turn (with tool_calls) to history ──
        messages.append(msg)  # type: ignore[arg-type]

        # ── Execute each requested tool call and feed results back ──
        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args: Dict[str, Any] = json.loads(tc.function.arguments)
            except json.JSONDecodeError as exc:
                logger.warning("Could not parse tool args for %r: %s", fn_name, exc)
                fn_args = {}

            logger.debug("Tool call: %s(%s)", fn_name, fn_args)

            try:
                result = dispatch_tool(
                    fn_name,
                    fn_args,
                    sqlite_store=_sqlite,
                    chroma_store=_chroma,
                )
            except Exception as exc:  # pragma: no cover
                logger.error("Tool %r raised: %s", fn_name, exc)
                result = {"error": str(exc), "tool": fn_name}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )

    # ── Safety net: max iterations exceeded ──
    logger.warning("ReportAgent hit max_iterations=%d without finishing.", max_iterations)
    return (
        f"⚠️ Report incomplete — agent reached the {max_iterations}-iteration limit "
        f"without producing a final answer. Please review the tool call history or "
        f"increase MAX_AGENT_ITERATIONS in config.py."
    )


# ---------------------------------------------------------------------------
# Class wrapper (for import compatibility with main.py and test_agent.py)
# ---------------------------------------------------------------------------


class ReportAgent:
    """Thin wrapper around :func:`run_report_agent` that keeps a shared store pair.

    Usage::

        agent = ReportAgent()
        report = agent.run("Generate today's status report.")
    """

    def __init__(
        self,
        *,
        db_path: str = DB_PATH,
        chroma_path: str = CHROMA_PATH,
        model: str = OPENAI_MODEL,
        max_iterations: int = MAX_AGENT_ITERATIONS,
    ) -> None:
        self._db_path = db_path
        self._chroma_path = chroma_path
        self._model = model
        self._max_iterations = max_iterations

    def run(
        self,
        user_query: str,
        *,
        report_date: Optional[str] = None,
    ) -> str:
        """Generate a report for the given query and date.

        Parameters
        ----------
        user_query:
            Natural-language question or instruction.
        report_date:
            ISO date to scope the diff (defaults to today).

        Returns
        -------
        str
            The Markdown report string.
        """
        with SQLiteStore(db_path=self._db_path) as sqlite_store:
            chroma_store = ChromaStore(path=self._chroma_path)
            return run_report_agent(
                user_query,
                report_date=report_date,
                max_iterations=self._max_iterations,
                sqlite_store=sqlite_store,
                chroma_store=chroma_store,
                model=self._model,
            )

    # ------------------------------------------------------------------
    # CLI entry-point
    # ------------------------------------------------------------------

    @classmethod
    def from_cli(cls) -> "ReportAgent":
        """Construct a ReportAgent from environment / config (for run_agent.sh)."""
        from config import validate_config  # noqa: PLC0415
        validate_config()
        return cls()


# ---------------------------------------------------------------------------
# CLI — python src/agents/report_agent.py --date 2025-05-21
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Run the Report Agent for a given date.")
    parser.add_argument("--date", default=date.today().isoformat(), help="ISO date (YYYY-MM-DD)")
    parser.add_argument("--query", default="Generate the full project status report.", help="User query")
    parser.add_argument("--model", default=OPENAI_MODEL, help="OpenAI model")
    parser.add_argument("--max-iter", type=int, default=MAX_AGENT_ITERATIONS, help="Max ReAct iterations")
    args = parser.parse_args()

    agent = ReportAgent(model=args.model, max_iterations=args.max_iter)
    try:
        report = agent.run(args.query, report_date=args.date)
        print(report)
    except Exception as e:
        logger.error("Agent failed: %s", e)
        sys.exit(1)
