import re
from typing import Any, Dict, List, Tuple

# Matches Jira-style issue keys, e.g. "AIP-1", "STORM-42"
_ISSUE_KEY_RE = re.compile(r"[A-Z]+-\d+")

class EntityExtractor:
    """Orchestrator to extract structured entities and backlinks from normalized docs.
    
    This implements the "Entity Extraction & Routing" phase.
    It takes normalized documents from the connectors and outputs:
      - entities: list of primary records (usually Jira issues) to upsert into the `entities` table.
      - backlinks: list of relationship records (mentions, action items) to insert into `backlinks`.
    """

    def extract(self, docs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        entities: List[Dict[str, Any]] = []
        backlinks: List[Dict[str, Any]] = []

        for doc in docs:
            source = doc.get("source")
            if source == "jira":
                # Jira documents are primarily entities.
                # Ensure task_id is present for sqlite upsert
                entity = dict(doc)
                if "task_id" not in entity and "source_id" in entity:
                    entity["task_id"] = entity["source_id"]
                entities.append(entity)

            elif source == "confluence":
                source_id = doc.get("source_id")
                
                # 1. Linked Jira Epics
                linked_epics = doc.get("linked_jira_epics", [])
                for epic in linked_epics:
                    backlinks.append({
                        "source_entity_id": source_id,
                        "target_entity_id": epic,
                        "link_type": "mentions",
                        "context": "linked_epic"
                    })
                
                # 2. Inline mentions
                text_content = doc.get("text_content", "")
                mentioned_keys = set(_ISSUE_KEY_RE.findall(text_content))
                for key in mentioned_keys:
                    if key not in linked_epics:
                        backlinks.append({
                            "source_entity_id": source_id,
                            "target_entity_id": key,
                            "link_type": "mentions",
                            "context": "inline_mention"
                        })

            elif source == "meeting_notes":
                source_id = doc.get("source_id")
                
                # 1. Action items (strong links)
                action_items = doc.get("action_items", [])
                action_item_keys = set()
                for ai in action_items:
                    key = ai.get("issue_key")
                    if key:
                        action_item_keys.add(key)
                        backlinks.append({
                            "source_entity_id": source_id,
                            "target_entity_id": key,
                            "link_type": "action_item",
                            "context": ai.get("text", "")
                        })

                # 2. General inline mentions
                mentioned_keys = doc.get("mentioned_keys", [])
                # The connector might have already extracted them, if not, fallback to regex
                if not mentioned_keys:
                    text_content = doc.get("text_content", "")
                    mentioned_keys = list(set(_ISSUE_KEY_RE.findall(text_content)))

                for key in mentioned_keys:
                    if key not in action_item_keys:
                        backlinks.append({
                            "source_entity_id": source_id,
                            "target_entity_id": key,
                            "link_type": "mentions",
                            "context": "inline_mention"
                        })
            else:
                # Unknown source, just skip
                pass

        return entities, backlinks
