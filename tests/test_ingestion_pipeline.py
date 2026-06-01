import pytest
from unittest.mock import patch, MagicMock

from src.ingestion.run_pipeline import run_pipeline

@patch("src.ingestion.run_pipeline.SQLiteStore")
@patch("src.ingestion.run_pipeline.ChromaStore")
@patch("src.ingestion.run_pipeline.JiraConnector")
@patch("src.ingestion.run_pipeline.ConfluenceConnector")
@patch("src.ingestion.run_pipeline.MeetingNotesConnector")
def test_run_pipeline(
    MockMeetingNotes,
    MockConfluence,
    MockJira,
    MockChroma,
    MockSQLite
):
    # Setup mock data for connectors
    mock_jira_conn = MockJira.return_value
    mock_jira_conn.load.return_value = [
        {"source": "jira", "source_id": "AIP-1", "title": "Jira Task"}
    ]
    
    mock_conf_conn = MockConfluence.return_value
    mock_conf_conn.load.return_value = [
        {"source": "confluence", "source_id": "CONF-1", "linked_jira_epics": ["AIP-1"], "text_content": ""}
    ]
    
    mock_notes_conn = MockMeetingNotes.return_value
    mock_notes_conn.load.return_value = [
        {"source": "meeting_notes", "source_id": "MTG-1", "action_items": [{"issue_key": "AIP-2", "text": "Do this"}], "mentioned_keys": []}
    ]

    # Mock SQLite context manager
    mock_sqlite_instance = MagicMock()
    MockSQLite.return_value.__enter__.return_value = mock_sqlite_instance

    mock_chroma_instance = MockChroma.return_value
    
    # Run the pipeline
    run_pipeline("jira.json", "conf_dir", "notes_dir")
    
    # Assert Connectors were instantiated and load was called
    MockJira.assert_called_once_with("jira.json")
    MockConfluence.assert_called_once_with("conf_dir")
    MockMeetingNotes.assert_called_once_with("notes_dir")
    
    mock_jira_conn.load.assert_called_once()
    mock_conf_conn.load.assert_called_once()
    mock_notes_conn.load.assert_called_once()
    
    # Assert SQLite operations
    # bulk_upsert should be called with 1 entity (from jira)
    mock_sqlite_instance.bulk_upsert.assert_called_once()
    entities_arg = mock_sqlite_instance.bulk_upsert.call_args[0][0]
    assert len(entities_arg) == 1
    assert entities_arg[0]["source_id"] == "AIP-1"
    
    # insert_backlinks should be called with backlinks (1 from conf, 1 from meeting)
    mock_sqlite_instance.insert_backlinks.assert_called_once()
    backlinks_arg = mock_sqlite_instance.insert_backlinks.call_args[0][0]
    assert len(backlinks_arg) == 2
    
    # save_snapshot should be called once for the Jira entity
    mock_sqlite_instance.save_snapshot.assert_called_once_with("AIP-1", entities_arg[0], None)
    
    # update_sync_log should be called for all 3 sources
    assert mock_sqlite_instance.update_sync_log.call_count == 3
    
    # Assert ChromaDB operations
    mock_chroma_instance.add_jira_description.assert_called_once()
    mock_chroma_instance.add_confluence_chunks.assert_called_once()
    mock_chroma_instance.add_meeting_chunks.assert_called_once()

def test_run_pipeline_no_args():
    # If paths are empty, it shouldn't instantiate those connectors
    with patch("src.ingestion.run_pipeline.JiraConnector") as MockJira:
        with patch("src.ingestion.run_pipeline.SQLiteStore") as MockSQLite:
            with patch("src.ingestion.run_pipeline.ChromaStore"):
                run_pipeline("", "", "")
                MockJira.assert_not_called()
