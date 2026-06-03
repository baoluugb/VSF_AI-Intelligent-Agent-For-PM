"""Unit tests for the Report Agent ReAct loop (no real OpenAI calls).

The v1 OpenAI SDK exposes ``client.chat.completions.create`` on a client
instance, so we patch ``agents.report_agent.openai.OpenAI`` and configure the
returned mock client's ``chat.completions.create``. This lets us script the
model's behaviour (tool calls vs. final answers) deterministically.

Scenarios
---------
1. The agent calls ``query_sqlite("AIP-1")`` then returns a final answer.
2. The agent keeps requesting tools past ``MAX_AGENT_ITERATIONS`` → caveat returned.
3. A tool returns an empty result → the empty result is surfaced to the model
   and the agent does not fabricate a citation.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.report_agent import run_report_agent, SYSTEM_PROMPT
from config import MAX_AGENT_ITERATIONS


# ---------------------------------------------------------------------------
# Helpers to build fake OpenAI response objects
# ---------------------------------------------------------------------------

def _message(content=None, tool_calls=None):
    """Build a fake ``response.choices[0].message``."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    return msg


def _response(message):
    """Wrap a message in a fake ``ChatCompletion`` response."""
    resp = MagicMock()
    resp.choices = [MagicMock(message=message)]
    return resp


def _tool_call(name, args, call_id="call_1"):
    """Build a fake ``tool_call`` entry (name + JSON-encoded arguments)."""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


class TestReportAgentReActLoop:
    # -------------------------------------------------------------------
    # Scenario 1 — one tool call, then a final answer
    # -------------------------------------------------------------------

    @patch("agents.report_agent.openai.OpenAI")
    def test_scenario1_query_sqlite_then_final_answer(self, mock_openai):
        sqlite_store = MagicMock()
        sqlite_store.query_entity.return_value = {
            "task_id": "AIP-1",
            "status": "In Progress",
            "assignee": "Minh Tuan",
            "labels": '["backend"]',
        }
        chroma_store = MagicMock()

        final_report = "## Overview\nAIP-1 is In Progress, owned by Minh Tuan [AIP-1]."

        # Turn 1 → model asks for query_sqlite("AIP-1"); Turn 2 → final answer.
        mock_create = mock_openai.return_value.chat.completions.create
        mock_create.side_effect = [
            _response(_message(tool_calls=[_tool_call("query_sqlite", {"entity_id": "AIP-1"})])),
            _response(_message(content=final_report)),
        ]

        result = run_report_agent("Daily report", "2025-05-21", sqlite_store, chroma_store)

        # The agent dispatched the tool against the real store contract.
        sqlite_store.query_entity.assert_called_once_with("AIP-1")
        # Exactly two model round-trips: one to request the tool, one to answer.
        assert mock_create.call_count == 2
        # The final answer is returned verbatim, with its citation intact.
        assert result == final_report
        assert "[AIP-1]" in result

    @patch("agents.report_agent.openai.OpenAI")
    def test_scenario1_tool_result_is_fed_back_to_model(self, mock_openai):
        """The dispatched tool's result must be appended as a role='tool' message."""
        sqlite_store = MagicMock()
        sqlite_store.query_entity.return_value = {"task_id": "AIP-1", "status": "Done"}
        chroma_store = MagicMock()

        captured_tool_msgs = []

        responses = iter([
            _response(_message(tool_calls=[_tool_call("query_sqlite", {"entity_id": "AIP-1"})])),
            _response(_message(content="## Overview\nDone [AIP-1].")),
        ])

        def create_side_effect(**kwargs):
            tool_msgs = [
                m["content"]
                for m in kwargs.get("messages", [])
                if isinstance(m, dict) and m.get("role") == "tool"
            ]
            captured_tool_msgs.append(tool_msgs)
            return next(responses)

        mock_openai.return_value.chat.completions.create.side_effect = create_side_effect

        run_report_agent("Daily report", "2025-05-21", sqlite_store, chroma_store)

        # On the 2nd model call, the tool envelope from turn 1 is present and
        # carries the citation id for AIP-1.
        second_call_tools = captured_tool_msgs[1]
        assert second_call_tools, "tool result should be appended before the 2nd call"
        envelope = json.loads(second_call_tools[0])
        assert envelope["source_ids"] == ["AIP-1"]
        assert envelope["result"]["found"] is True

    # -------------------------------------------------------------------
    # Scenario 2 — exceed MAX_AGENT_ITERATIONS → caveat
    # -------------------------------------------------------------------

    @patch("agents.report_agent.openai.OpenAI")
    def test_scenario2_exceeding_max_iterations_returns_caveat(self, mock_openai):
        sqlite_store = MagicMock()
        sqlite_store.query_entity.return_value = {"task_id": "AIP-1", "status": "In Progress"}
        chroma_store = MagicMock()

        partial_text = "## Overview\nPartial findings so far [AIP-1]."

        def create_side_effect(**kwargs):
            # The agent's final salvage call disables tools; answer with a partial report.
            if kwargs.get("tool_choice") == "none":
                return _response(_message(content=partial_text))
            # Otherwise the "model" stubbornly keeps requesting tools and never finishes,
            # which forces the loop past its MAX_AGENT_ITERATIONS ceiling.
            return _response(_message(tool_calls=[_tool_call("query_sqlite", {"entity_id": "AIP-1"})]))

        mock_create = mock_openai.return_value.chat.completions.create
        mock_create.side_effect = create_side_effect

        result = run_report_agent("Daily report", "2025-05-21", sqlite_store, chroma_store)

        # The tool was dispatched on every iteration up to the ceiling — and no further.
        assert sqlite_store.query_entity.call_count == MAX_AGENT_ITERATIONS
        # MAX_AGENT_ITERATIONS loop calls + 1 final tool-free salvage call.
        assert mock_create.call_count == MAX_AGENT_ITERATIONS + 1
        # The result carries an explicit "incomplete" caveat.
        assert "caveat" in result.lower()
        assert "incomplete" in result.lower()
        # The salvaged partial content is preserved above the caveat.
        assert "Partial findings so far" in result

    # -------------------------------------------------------------------
    # Scenario 3 — empty tool result → no hallucination
    # -------------------------------------------------------------------

    @patch("agents.report_agent.openai.OpenAI")
    def test_scenario3_empty_result_does_not_hallucinate(self, mock_openai):
        sqlite_store = MagicMock()
        sqlite_store.query_entity.return_value = None  # not found → empty result
        chroma_store = MagicMock()

        # An honest model, given an empty result, declines to invent anything.
        honest_answer = (
            "## Overview\n(No verified data found.)\n\n"
            "## Changes Today\n(No verified data found.)\n\n"
            "## Concerns\n(No verified data found.)\n\n"
            "## Next Actions\n(No verified data found.)"
        )

        captured_tool_msgs = []
        responses = iter([
            _response(_message(tool_calls=[_tool_call("query_sqlite", {"entity_id": "AIP-99"})])),
            _response(_message(content=honest_answer)),
        ])

        def create_side_effect(**kwargs):
            tool_msgs = [
                m["content"]
                for m in kwargs.get("messages", [])
                if isinstance(m, dict) and m.get("role") == "tool"
            ]
            captured_tool_msgs.append(tool_msgs)
            return next(responses)

        mock_openai.return_value.chat.completions.create.side_effect = create_side_effect

        result = run_report_agent("Status of AIP-99", "2025-05-21", sqlite_store, chroma_store)

        # The empty result was actually surfaced to the model on the 2nd call,
        # so the model had the chance to see there was nothing to report.
        second_call_tools = captured_tool_msgs[1]
        assert second_call_tools, "empty tool result must still be fed back to the model"
        envelope = json.loads(second_call_tools[0])
        assert envelope["source_ids"] == []
        assert envelope["result"]["found"] is False

        # The agent returns the honest answer verbatim and invents no citation.
        assert result == honest_answer
        assert "(No verified data found.)" in result
        assert "[AIP-99]" not in result  # nothing found → nothing may be cited

    # -------------------------------------------------------------------
    # Guardrail: the system prompt itself encodes the anti-hallucination contract
    # -------------------------------------------------------------------

    def test_system_prompt_enforces_citations_and_format(self):
        assert "[source_id]" in SYSTEM_PROMPT
        # Anti-hallucination instruction is present.
        assert "No verified data found" in SYSTEM_PROMPT
        assert "HALLUCINAT" in SYSTEM_PROMPT.upper()
        # All four required report sections are specified.
        for section in ("## Overview", "## Changes Today", "## Concerns", "## Next Actions"):
            assert section in SYSTEM_PROMPT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
