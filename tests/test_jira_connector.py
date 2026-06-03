from pathlib import Path

from src.ingestion.jira_connector import JiraConnector


def _get_fixture_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "jira" / "jira_synthetic_AIP.json"


def test_load_returns_non_empty_list() -> None:
    connector = JiraConnector(str(_get_fixture_path()))
    items = connector.load()

    assert isinstance(items, list)
    assert items


def test_entities_contain_required_keys() -> None:
    connector = JiraConnector(str(_get_fixture_path()))
    items = connector.load()

    required_keys = {
        "source",
        "source_id",
        "title",
        "status",
        "assignee",
        "priority",
        "labels",
        "due_date",
        "description",
        "url",
        "created_at",
        "updated_at",
    }

    for item in items:
        assert required_keys.issubset(item.keys())


def test_extract_adf_text_flattens_nested_content() -> None:
    connector = JiraConnector("/tmp/unused.json")

    adf = {
        "type": "doc",
        "content": [
                {
                    "type": "paragraph",
                    "content": [
                            {"type": "text", "text": "Hello"},
                            {"type": "text", "text": " "},
                            {"type": "text", "text": "world"},
                    ],
                },
            {
                    "type": "paragraph",
                    "content": [
                            {"type": "text", "text": "Nested"},
                            {
                                "type": "blockquote",
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {"type": "text",
                                             "text": "content"},
                                        ],
                                    }
                                ],
                            },
                    ],
                },
        ],
    }

    flattened = connector._extract_adf_text(adf)

    assert "Hello world" in flattened
    assert "Nested" in flattened
    assert "content" in flattened


def test_ground_truth_not_in_output() -> None:
    connector = JiraConnector(str(_get_fixture_path()))
    items = connector.load()

    assert all("_ground_truth" not in item for item in items)


def test_done_status_entities_are_preserved() -> None:
    connector = JiraConnector(str(_get_fixture_path()))
    items = connector.load()

    assert any(item.get("status") == "Done" for item in items)


def test_source_is_normalized_to_jira_type() -> None:
    """The payload declares "source": "Apache", but normalized docs must use the
    canonical "jira" discriminator so downstream routing/extraction works."""
    connector = JiraConnector(str(_get_fixture_path()))
    items = connector.load()

    assert items
    assert all(item["source"] == "jira" for item in items)
