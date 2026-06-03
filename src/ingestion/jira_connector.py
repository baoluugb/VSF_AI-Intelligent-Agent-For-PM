from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class JiraConnector:
    def __init__(self, json_path: str) -> None:
        self.json_path = Path(json_path)

    def load(self) -> List[Dict[str, Any]]:
        payload = json.loads(self.json_path.read_text(encoding="utf-8"))
        issues = payload.get("issues", [])
        if not isinstance(issues, list):
            raise ValueError("Invalid Jira payload: 'issues' must be a list.")

        normalized: List[Dict[str, Any]] = []
        for issue in issues:
            normalized.append(self._normalize_issue(issue))
        return normalized

    def _normalize_issue(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        fields = issue.get("fields", {}) or {}
        issuetype = fields.get("issuetype", {}) or {}
        status = fields.get("status", {}) or {}
        assignee = fields.get("assignee") or {}
        priority = fields.get("priority") or {}

        description = fields.get("description")
        if isinstance(description, dict):
            description_text = self._extract_adf_text(description)
        else:
            description_text = description

        labels = fields.get("labels")
        if labels is None:
            labels = []

        return {
            # Canonical type discriminator used for routing — NOT the payload's
            # dataset origin (the synthetic file declares "source": "Apache").
            "source": "jira",
            "source_id": issue.get("key"),
            "title": fields.get("summary"),
            "status": status.get("name"),
            "assignee": assignee.get("displayName") or assignee.get("name"),
            "priority": priority.get("name"),
            "labels": labels,
            "due_date": fields.get("duedate"),
            "description": description_text,
            "url": issue.get("self"),
            "created_at": fields.get("created"),
            "updated_at": fields.get("updated"),
        }

    def _extract_adf_text(self, adf: Dict[str, Any]) -> str:
        parts: List[str] = []
        block_types = {
            "paragraph",
            "heading",
            "listItem",
            "blockquote",
            "codeBlock",
        }

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                node_type = node.get("type")
                if node_type == "hardBreak":
                    parts.append("\n")
                text = node.get("text")
                if text:
                    parts.append(text)
                for child in node.get("content", []) or []:
                    walk(child)
                if node_type in block_types:
                    parts.append("\n")
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(adf)
        joined = "".join(parts)
        cleaned = "\n".join(line.strip()
                            for line in joined.splitlines()).strip()
        return cleaned
