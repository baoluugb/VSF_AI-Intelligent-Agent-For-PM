"""Unit tests for the live Jira/Confluence Cloud connectors (roadmap P1).

No network: ``httpx.Client`` is mocked. These prove pagination + normalization
(reusing ``JiraConnector._normalize_issue`` and the XHTML strip), not a live
round-trip — there's no test Atlassian instance.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ingestion.confluence_api_connector import ConfluenceApiConnector
from ingestion.jira_api_connector import JiraApiConnector


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


def _mock_client(responses):
    """A MagicMock standing in for ``httpx.Client`` used as a context manager."""
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.side_effect = responses
    return client


def _issue(key, status="In Progress"):
    return {
        "key": key,
        "self": f"https://acme.atlassian.net/rest/api/3/issue/{key}",
        "fields": {
            "summary": f"Work on {key}",
            "status": {"name": status},
            "assignee": {"displayName": "Minh Tuan"},
            "priority": {"name": "High"},
            "labels": ["backend"],
            "duedate": "2025-05-24",
            "description": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Ship it."}],
                    }
                ],
            },
            "created": "2025-05-01T09:00:00.000+0000",
            "updated": "2025-05-20T09:00:00.000+0000",
        },
    }


# --- Jira -------------------------------------------------------------------

def test_jira_api_requires_credentials():
    with pytest.raises(ValueError):
        JiraApiConnector("", "e@x.com", "tok")


def test_jira_api_paginates_and_normalizes():
    page1 = {"issues": [_issue("AIP-1")], "total": 2, "startAt": 0, "maxResults": 1}
    page2 = {"issues": [_issue("AIP-2", "Done")], "total": 2,
             "startAt": 1, "maxResults": 1}
    client = _mock_client([_resp(page1), _resp(page2)])

    with patch("ingestion.jira_api_connector.httpx.Client", return_value=client):
        docs = JiraApiConnector(
            "https://acme.atlassian.net/", "e@x.com", "tok", page_size=1
        ).load()

    assert client.get.call_count == 2                 # both pages fetched
    assert [d["source_id"] for d in docs] == ["AIP-1", "AIP-2"]
    assert docs[0]["source"] == "jira"
    assert docs[0]["description"] == "Ship it."       # ADF extracted via base class
    # HTTP-basic auth was forwarded.
    assert client.get.call_args.kwargs["auth"] == ("e@x.com", "tok")


# --- Confluence -------------------------------------------------------------

def test_confluence_api_normalizes_and_strips_html():
    page = {
        "id": "12345",
        "title": "Ingestion Architecture",
        "status": "current",
        "space": {"key": "AIP"},
        "version": {"when": "2025-05-20T00:00:00.000Z"},
        "body": {"storage": {"value": "<h2>Context</h2><p>Use <b>ChromaDB</b>.</p>"}},
        "metadata": {"labels": {"results": [{"name": "architecture"}]}},
    }
    # One short page (< page_size) → loop terminates after a single request.
    client = _mock_client([_resp({"results": [page]})])

    with patch("ingestion.confluence_api_connector.httpx.Client", return_value=client):
        docs = ConfluenceApiConnector(
            "https://acme.atlassian.net/wiki", "e@x.com", "tok"
        ).load()

    assert len(docs) == 1
    d = docs[0]
    assert d["source"] == "confluence"
    assert d["source_id"] == "12345"
    assert d["space"] == "AIP"
    assert d["tags"] == ["architecture"]
    assert "<" not in d["text_content"]               # tags stripped
    assert "Use ChromaDB" in d["text_content"]
