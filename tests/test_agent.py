"""Tests for the Report Agent (Week 3).

Covers:
  - ReportAgent class instantiation
  - TOOLS schema structure (3 tools with correct names and required fields)
  - dispatch_tool routing (mocked stores, no network calls)
  - run_report_agent happy-path via mocked OpenAI client
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# ReportAgent & tools
# ---------------------------------------------------------------------------

from agents.report_agent import ReportAgent, SYSTEM_PROMPT, run_report_agent
from tools.registry import TOOLS, dispatch_tool


# ---------------------------------------------------------------------------
# 3.1 — Tool schema tests
# ---------------------------------------------------------------------------


class TestToolSchemas:
    def test_three_tools_defined(self):
        assert len(TOOLS) == 3

    def test_tool_names(self):
        names = {t["function"]["name"] for t in TOOLS}
        assert names == {"query_chroma", "query_sqlite", "get_daily_diff"}

    def test_query_chroma_required_fields(self):
        schema = next(t for t in TOOLS if t["function"]["name"] == "query_chroma")
        assert "query" in schema["function"]["parameters"]["required"]

    def test_query_sqlite_required_fields(self):
        schema = next(t for t in TOOLS if t["function"]["name"] == "query_sqlite")
        assert "entity_id" in schema["function"]["parameters"]["required"]

    def test_get_daily_diff_required_fields(self):
        schema = next(t for t in TOOLS if t["function"]["name"] == "get_daily_diff")
        assert "date" in schema["function"]["parameters"]["required"]

    def test_all_tools_have_type_function(self):
        for tool in TOOLS:
            assert tool["type"] == "function"

    def test_source_filter_enum_values(self):
        schema = next(t for t in TOOLS if t["function"]["name"] == "query_chroma")
        enum_vals = schema["function"]["parameters"]["properties"]["source_filter"]["enum"]
        assert set(enum_vals) == {"confluence", "meeting_notes", "all"}


# ---------------------------------------------------------------------------
# 3.1 — dispatch_tool routing tests (no real DB / network)
# ---------------------------------------------------------------------------


class TestDispatchTool:
    def test_dispatch_unknown_tool_raises(self):
        with pytest.raises(ValueError, match="Unknown tool"):
            dispatch_tool("nonexistent_tool", {})

    def test_dispatch_query_sqlite_not_found(self):
        mock_store = MagicMock()
        mock_store.query_entity.return_value = None
        result = dispatch_tool("query_sqlite", {"entity_id": "AIP-999"}, sqlite_store=mock_store)
        assert result["found"] is False
        assert result["entity_id"] == "AIP-999"

    def test_dispatch_query_sqlite_found(self):
        mock_store = MagicMock()
        mock_store.query_entity.return_value = {
            "task_id": "AIP-1",
            "status": "In Progress",
            "assignee": "Alice",
            "labels": '["backend"]',
        }
        result = dispatch_tool("query_sqlite", {"entity_id": "AIP-1"}, sqlite_store=mock_store)
        assert result["found"] is True
        assert result["source_id"] == "AIP-1"
        assert result["status"] == "In Progress"
        # labels JSON should be deserialised
        assert result["labels"] == ["backend"]

    def test_dispatch_get_daily_diff(self):
        mock_store = MagicMock()
        mock_store.get_daily_diff.return_value = [
            {"task_id": "AIP-2", "data_today": '{"status": "Done"}', "data_yesterday": '{"status": "In Progress"}'}
        ]
        result = dispatch_tool("get_daily_diff", {"date": "2025-05-21"}, sqlite_store=mock_store)
        assert isinstance(result, list)
        assert result[0]["task_id"] == "AIP-2"
        # data fields should be deserialised from JSON strings
        assert result[0]["data_today"]["status"] == "Done"

    def test_dispatch_query_chroma_calls_store(self):
        mock_store = MagicMock()
        mock_store.query.return_value = [
            {"document": "some text", "metadata": {"page_id": "CONF-1"}, "distance": 0.1}
        ]
        result = dispatch_tool(
            "query_chroma",
            {"query": "architecture", "source_filter": "confluence"},
            chroma_store=mock_store,
        )
        assert isinstance(result, list)
        assert result[0]["source_id"] == "CONF-1"
        assert result[0]["source"] == "confluence"


# ---------------------------------------------------------------------------
# 3.2 — ReAct loop tests (mocked OpenAI)
# ---------------------------------------------------------------------------


class TestRunReportAgent:
    def _make_mock_client(self, final_content: str):
        """Return a mock openai.OpenAI client that skips tool calls and returns final_content."""
        mock_msg = MagicMock()
        mock_msg.tool_calls = None
        mock_msg.content = final_content

        mock_choice = MagicMock()
        mock_choice.message = mock_msg

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        return mock_client

    def test_returns_string_on_no_tool_calls(self):
        with patch("agents.report_agent.openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value = self._make_mock_client("Final report text [AIP-1].")
            result = run_report_agent("Generate today's report.", report_date="2025-05-21")
        assert "Final report text" in result

    def test_client_initialization_uses_env_configs(self):
        """Verify that the OpenAI client constructor receives correct api_key and base_url from config."""
        with patch("agents.report_agent.openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value = self._make_mock_client("Test report.")
            run_report_agent("Generate report.")
            
            MockOpenAI.assert_called_once()
            _, kwargs = MockOpenAI.call_args
            assert kwargs.get("api_key") == "sk-8d1196cbd753bc193c99b377298828135916b4ccd5204ef5fdacb77a197987d0"
            assert kwargs.get("base_url") == "https://ckey.vn/v1"


    def test_max_iterations_returns_warning(self):
        """Agent should return a warning string if it always calls tools and never finishes."""
        mock_tc = MagicMock()
        mock_tc.id = "call_abc"
        mock_tc.function.name = "get_daily_diff"
        mock_tc.function.arguments = json.dumps({"date": "2025-05-21"})

        mock_msg_with_tool = MagicMock()
        mock_msg_with_tool.tool_calls = [mock_tc]
        mock_msg_with_tool.content = None

        mock_choice = MagicMock()
        mock_choice.message = mock_msg_with_tool

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_sqlite = MagicMock()
        mock_sqlite.get_daily_diff.return_value = []

        with patch("agents.report_agent.openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.chat.completions.create.return_value = mock_response
            result = run_report_agent(
                "Generate report.",
                report_date="2025-05-21",
                max_iterations=2,
                sqlite_store=mock_sqlite,
            )
        assert "iteration limit" in result.lower() or "incomplete" in result.lower()


# ---------------------------------------------------------------------------
# 3.3 — Citation enforcement / SYSTEM_PROMPT tests
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_is_nonempty(self):
        assert len(SYSTEM_PROMPT.strip()) > 100

    def test_system_prompt_contains_citation_rule(self):
        assert "[source_id]" in SYSTEM_PROMPT

    def test_system_prompt_forbids_unsourced_claims(self):
        assert "FORBIDDEN" in SYSTEM_PROMPT

    def test_system_prompt_names_all_three_tools(self):
        assert "query_sqlite" in SYSTEM_PROMPT
        assert "query_chroma" in SYSTEM_PROMPT
        assert "get_daily_diff" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# ReportAgent class wrapper
# ---------------------------------------------------------------------------


class TestReportAgentClass:
    def test_instantiation(self):
        agent = ReportAgent()
        assert agent is not None
        assert isinstance(agent, ReportAgent)

    def test_run_calls_run_report_agent(self):
        with patch("agents.report_agent.run_report_agent") as mock_fn:
            mock_fn.return_value = "Mock report [AIP-1]."
            agent = ReportAgent()
            result = agent.run("Test query.", report_date="2025-05-21")
        assert result == "Mock report [AIP-1]."
        mock_fn.assert_called_once()
