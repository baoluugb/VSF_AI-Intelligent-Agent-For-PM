import pytest
from src.ingestion.entity_extractor import EntityExtractor

def test_entity_extractor_jira():
    extractor = EntityExtractor()
    docs = [
        {
            "source": "jira",
            "source_id": "AIP-1",
            "title": "Test Jira Issue",
            "assignee": "Minh Tuan"
        }
    ]
    
    entities, backlinks = extractor.extract(docs)
    assert len(entities) == 1
    assert len(backlinks) == 0
    assert entities[0]["task_id"] == "AIP-1"
    assert entities[0]["assignee"] == "Minh Tuan"

def test_entity_extractor_confluence():
    extractor = EntityExtractor()
    docs = [
        {
            "source": "confluence",
            "source_id": "CONF-123",
            "linked_jira_epics": ["AIP-10"],
            "text_content": "This mentions AIP-10 and also inline mentions AIP-45."
        }
    ]
    
    entities, backlinks = extractor.extract(docs)
    assert len(entities) == 0
    assert len(backlinks) == 2
    
    # Check linked epic
    epic_link = next(b for b in backlinks if b["target_entity_id"] == "AIP-10")
    assert epic_link["link_type"] == "mentions"
    assert epic_link["context"] == "linked_epic"
    assert epic_link["source_entity_id"] == "CONF-123"

    # Check inline mention
    inline_link = next(b for b in backlinks if b["target_entity_id"] == "AIP-45")
    assert inline_link["link_type"] == "mentions"
    assert inline_link["context"] == "inline_mention"

def test_entity_extractor_meeting_notes():
    extractor = EntityExtractor()
    docs = [
        {
            "source": "meeting_notes",
            "source_id": "MTG-001",
            "action_items": [
                {"issue_key": "AIP-20", "assignee": "Bao Chau", "text": "AIP-20: Do something"}
            ],
            "mentioned_keys": ["AIP-20", "AIP-30"]
        }
    ]
    
    entities, backlinks = extractor.extract(docs)
    assert len(entities) == 0
    assert len(backlinks) == 2
    
    # Action item link
    ai_link = next(b for b in backlinks if b["target_entity_id"] == "AIP-20")
    assert ai_link["link_type"] == "action_item"
    assert ai_link["context"] == "AIP-20: Do something"
    
    # Inline mention link (AIP-30)
    mention_link = next(b for b in backlinks if b["target_entity_id"] == "AIP-30")
    assert mention_link["link_type"] == "mentions"
    assert mention_link["context"] == "inline_mention"
