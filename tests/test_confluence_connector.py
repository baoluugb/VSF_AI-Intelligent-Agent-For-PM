"""Tests for ConfluenceConnector.

Uses a real page from data/confluence/confluence_synthetic.json as the
canonical fixture, and tmp_path for isolated per-test file creation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from src.ingestion.confluence_connector import ConfluenceConnector

# ---------------------------------------------------------------------------
# Shared fixture data — sourced from the first page in confluence_synthetic.json
# ---------------------------------------------------------------------------

_REAL_PAGE: Dict[str, Any] = {
    "page_id": "CONF-001",
    "title": "AIP — Project Overview",
    "space": "AIP",
    "author": "bob.miller",
    "last_updated": "2025-05-27",
    "status": "current",
    "linked_jira_epics": ["AIP-28", "AIP-43", "AIP-77"],
    "tags": ["overview", "onboarding", "aip"],
    "content": (
        "## Chào mừng bạn đến với AIP!\n\n"
        "Document này giúp developer mới nhanh chóng làm quen với project AIP.\n\n"
        "## Tổng quan Project\n\n"
        "AIP là một Apache open-source project tập trung vào AI-powered project intelligence.\n"
    ),
}

_REQUIRED_KEYS = {
    "source",
    "source_id",
    "title",
    "space",
    "author",
    "last_updated",
    "status",
    "linked_jira_epics",
    "tags",
    "text_content",
}


def _write_page(directory: Path, filename: str, data: Dict[str, Any]) -> Path:
    """Helper: write *data* as JSON into *directory/filename* and return the path."""
    path = directory / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Single valid page
# ---------------------------------------------------------------------------

def test_load_single_page(tmp_path: Path) -> None:
    """load() on a folder with one valid JSON returns a list of length 1."""
    _write_page(tmp_path, "CONF-001.json", _REAL_PAGE)

    connector = ConfluenceConnector(str(tmp_path))
    result = connector.load()

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["source"] == "confluence"
    assert result[0]["source_id"] == _REAL_PAGE["page_id"]


# ---------------------------------------------------------------------------
# 2. Multiple pages
# ---------------------------------------------------------------------------

def test_load_multiple_pages(tmp_path: Path) -> None:
    """load() returns one normalised dict per valid JSON file in the folder."""
    pages = [
        {**_REAL_PAGE, "page_id": "CONF-001", "title": "Page One"},
        {**_REAL_PAGE, "page_id": "CONF-002", "title": "Page Two"},
        {**_REAL_PAGE, "page_id": "CONF-003", "title": "Page Three"},
    ]
    for page in pages:
        _write_page(tmp_path, f"{page['page_id']}.json", page)

    connector = ConfluenceConnector(str(tmp_path))
    result = connector.load()

    assert len(result) == 3
    loaded_ids = {doc["source_id"] for doc in result}
    assert loaded_ids == {"CONF-001", "CONF-002", "CONF-003"}


# ---------------------------------------------------------------------------
# 3. Missing required field → file skipped
# ---------------------------------------------------------------------------

def test_missing_required_field_returns_none(tmp_path: Path) -> None:
    """A JSON file without the 'content' field must be skipped silently."""
    bad_page = {k: v for k, v in _REAL_PAGE.items() if k != "content"}
    _write_page(tmp_path, "CONF-bad.json", bad_page)

    connector = ConfluenceConnector(str(tmp_path))
    result = connector.load()

    assert result == [], (
        f"Expected empty list for file missing 'content', got {result}"
    )


# ---------------------------------------------------------------------------
# 4. All required keys present
# ---------------------------------------------------------------------------

def test_all_required_keys_present(tmp_path: Path) -> None:
    """Every normalised dict must contain exactly the documented set of keys."""
    _write_page(tmp_path, "CONF-001.json", _REAL_PAGE)

    connector = ConfluenceConnector(str(tmp_path))
    result = connector.load()

    assert len(result) == 1
    doc = result[0]
    # The returned dict must contain all required keys
    assert _REQUIRED_KEYS.issubset(doc.keys()), (
        f"Missing keys: {_REQUIRED_KEYS - doc.keys()}"
    )


# ---------------------------------------------------------------------------
# 5. Empty folder
# ---------------------------------------------------------------------------

def test_empty_folder(tmp_path: Path) -> None:
    """load() on an empty directory returns an empty list without raising."""
    connector = ConfluenceConnector(str(tmp_path))
    result = connector.load()

    assert result == []


# ---------------------------------------------------------------------------
# Bonus: field value correctness
# ---------------------------------------------------------------------------

def test_field_values_match_source_json(tmp_path: Path) -> None:
    """Every normalised field value must match the raw JSON input exactly."""
    _write_page(tmp_path, "CONF-001.json", _REAL_PAGE)

    connector = ConfluenceConnector(str(tmp_path))
    doc = connector.load()[0]

    assert doc["source"] == "confluence"
    assert doc["source_id"] == "CONF-001"
    assert doc["title"] == "AIP — Project Overview"
    assert doc["space"] == "AIP"
    assert doc["author"] == "bob.miller"
    assert doc["last_updated"] == "2025-05-27"
    assert doc["status"] == "current"
    assert doc["linked_jira_epics"] == ["AIP-28", "AIP-43", "AIP-77"]
    assert doc["tags"] == ["overview", "onboarding", "aip"]
    assert doc["text_content"] == _REAL_PAGE["content"]
    assert doc["url"] == "https://confluence.internal/pages/CONF-001"


def test_mixed_valid_and_invalid_files(tmp_path: Path) -> None:
    """Valid files are loaded; invalid ones are silently skipped."""
    _write_page(tmp_path, "CONF-good.json", _REAL_PAGE)
    # Missing 'page_id'
    _write_page(tmp_path, "CONF-no-id.json", {k: v for k, v in _REAL_PAGE.items() if k != "page_id"})
    # Missing 'title'
    _write_page(tmp_path, "CONF-no-title.json", {k: v for k, v in _REAL_PAGE.items() if k != "title"})

    connector = ConfluenceConnector(str(tmp_path))
    result = connector.load()

    assert len(result) == 1
    assert result[0]["source_id"] == "CONF-001"


def test_linked_jira_epics_is_always_a_list(tmp_path: Path) -> None:
    """linked_jira_epics must be a list even when the JSON field is absent."""
    page_without_epics = {**_REAL_PAGE, "linked_jira_epics": None}
    _write_page(tmp_path, "CONF-001.json", page_without_epics)

    connector = ConfluenceConnector(str(tmp_path))
    doc = connector.load()[0]

    assert isinstance(doc["linked_jira_epics"], list)


def test_status_fallback_for_unknown_value(tmp_path: Path) -> None:
    """Unrecognised status values fall back to 'draft' and the page is still loaded."""
    page_bad_status = {**_REAL_PAGE, "status": "archived"}
    _write_page(tmp_path, "CONF-001.json", page_bad_status)

    connector = ConfluenceConnector(str(tmp_path))
    result = connector.load()

    assert len(result) == 1
    assert result[0]["status"] == "draft"


def test_file_not_found_raises(tmp_path: Path) -> None:
    """load() raises FileNotFoundError when the folder does not exist."""
    connector = ConfluenceConnector(str(tmp_path / "nonexistent"))
    with pytest.raises(FileNotFoundError):
        connector.load()


def test_url_format(tmp_path: Path) -> None:
    """_build_url must return the expected placeholder URL format."""
    connector = ConfluenceConnector(str(tmp_path))
    assert connector._build_url("CONF-042") == "https://confluence.internal/pages/CONF-042"
