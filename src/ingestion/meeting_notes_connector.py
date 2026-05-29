"""Connector that reads Meeting Notes from JSON files in a folder."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Matches Jira-style issue keys, e.g. "AIP-1", "STORM-42", "ZOOKEEPER-100"
_ISSUE_KEY_RE = re.compile(r"[A-Z]+-\d+")
_SECTION_ATTENDEES = "[Attendees]"
_SECTION_ACTION_ITEMS = "[Action Items]"


class MeetingNotesConnector:
    """Read and normalise meeting notes from a directory of ``.json`` files.

    Each ``.json`` file may contain either:

    * A **single** meeting object (keys: ``meeting_id``, ``date``, …), or
    * A **collection** object with a ``"meetings"`` key whose value is a list
      of meeting objects (the format used by ``meeting_notes.json``).

    The ``text_content`` field carries the pre-rendered ``content`` string
    from the JSON and is passed downstream to
    ``ChromaStore.add_meeting_chunks()`` for vector indexing.

    Parameters
    ----------
    folder_path:
        Path to the directory containing ``.json`` meeting note files.
    """

    def __init__(self, folder_path: str) -> None:
        self.folder_path = Path(folder_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> List[Dict[str, Any]]:
        """Read every ``.json`` file in the folder and return normalised notes.

        Files that cannot be read or parsed are skipped with a warning.

        Returns
        -------
        list[dict]
            Normalised meeting-note dicts.

        Raises
        ------
        FileNotFoundError
            If *folder_path* does not exist or is not a directory.
        """
        if not self.folder_path.exists():
            raise FileNotFoundError(
                f"Meeting notes folder not found: {self.folder_path}"
            )
        if not self.folder_path.is_dir():
            raise FileNotFoundError(
                f"Expected a directory, got a file: {self.folder_path}"
            )

        json_files = sorted(self.folder_path.glob("*.json"))
        text_files = sorted(self.folder_path.glob("*.txt"))
        if not json_files and not text_files:
            logger.warning("No .json or .txt files found in %s",
                           self.folder_path)
            return []

        notes: List[Dict[str, Any]] = []
        for filepath in json_files:
            notes.extend(self._load_file(filepath))
        for filepath in text_files:
            parsed = self._load_text_file(filepath)
            if parsed is not None:
                notes.append(parsed)

        logger.info(
            "MeetingNotesConnector: loaded %d meeting(s) from %d file(s) in %s",
            len(notes),
            len(json_files) + len(text_files),
            self.folder_path,
        )
        return notes

    # ------------------------------------------------------------------
    # Private — file-level
    # ------------------------------------------------------------------

    def _load_file(self, path: Path) -> List[Dict[str, Any]]:
        """Parse one JSON file and return all meetings found inside it.

        Handles both single-meeting objects and collection objects that
        contain a ``"meetings"`` list.

        Parameters
        ----------
        path:
            Path to the ``.json`` file.

        Returns
        -------
        list[dict]
            Zero or more normalised meeting dicts.
        """
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Skipping %s — could not read/parse: %s", path.name, exc)
            return []

        if not isinstance(payload, dict):
            logger.warning("Skipping %s — expected a JSON object, got %s.",
                           path.name, type(payload).__name__)
            return []

        # Collection format: {"meetings": [...]}
        if "meetings" in payload:
            raw_meetings = payload["meetings"]
            if not isinstance(raw_meetings, list):
                logger.warning(
                    "Skipping %s — 'meetings' must be a list.", path.name)
                return []
        else:
            # Single-meeting format: the root object IS the meeting
            raw_meetings = [payload]

        results: List[Dict[str, Any]] = []
        for raw in raw_meetings:
            parsed = self._parse_meeting(raw, source_file=path.name)
            if parsed is not None:
                results.append(parsed)
        return results

    def _load_text_file(self, path: Path) -> Optional[Dict[str, Any]]:
        """Parse a single plain-text meeting note.

        The file is expected to have a header section with ``date:`` and
        ``project:`` lines, followed by ``[Attendees]`` and ``[Action Items]``
        sections.
        """
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Skipping %s — could not read: %s", path.name, exc)
            return None

        if not content.strip():
            logger.warning("Skipping %s — file is empty.", path.name)
            return None

        meeting_id = path.stem
        lines = content.splitlines()
        header = self._parse_text_header(lines)

        return {
            "source": "meeting_notes",
            "source_id": meeting_id,
            "date": header.get("date", ""),
            "project": header.get("project", ""),
            "attendees": self._parse_text_attendees(lines),
            "action_items": self._parse_text_action_items(lines),
            "text_content": content,
            "mentioned_keys": self._extract_issue_keys(content),
        }

    # ------------------------------------------------------------------
    # Private — meeting-level
    # ------------------------------------------------------------------

    def _parse_meeting(
        self, meeting: Dict[str, Any], source_file: str = ""
    ) -> Optional[Dict[str, Any]]:
        """Normalise a single meeting dict from the JSON payload.

        Parameters
        ----------
        meeting:
            Raw meeting object as decoded from JSON.
        source_file:
            Filename (for logging only).

        Returns
        -------
        dict | None
            Normalised dict, or ``None`` if ``meeting_id`` is missing.
        """
        meeting_id = meeting.get("meeting_id")
        if not meeting_id:
            logger.warning(
                "Skipping a meeting in %s — missing 'meeting_id'.", source_file
            )
            return None

        meeting_id = str(meeting_id)
        text_content: str = str(meeting.get("content") or "")

        return {
            "source": "meeting_notes",
            "source_id": meeting_id,
            "date": str(meeting.get("date") or ""),
            "project": str(meeting.get("project") or ""),
            "attendees": self._parse_attendees(meeting.get("attendees") or []),
            "action_items": self._parse_action_items(meeting.get("action_items") or []),
            "text_content": text_content,
            "mentioned_keys": self._extract_issue_keys(text_content),
        }

    def _parse_attendees(self, raw: List[Any]) -> List[str]:
        """Convert the attendee list to ``"Name (Role)"`` strings.

        Parameters
        ----------
        raw:
            List of attendee dicts, each with at least ``"name"`` and
            optionally ``"role"``.

        Returns
        -------
        list[str]
            Formatted attendee strings.
        """
        result: List[str] = []
        for person in raw:
            if not isinstance(person, dict):
                continue
            name = str(person.get("name") or "").strip()
            role = str(person.get("role") or "").strip()
            result.append(f"{name} ({role})" if role else name)
        return result

    def _parse_action_items(self, raw: List[Any]) -> List[Dict[str, str]]:
        """Convert action-item dicts to the normalised ``{issue_key, assignee, text}`` shape.

        Parameters
        ----------
        raw:
            List of action-item dicts from the JSON, each expected to have
            ``"jira_key"``, ``"owner"``, and ``"description"``.

        Returns
        -------
        list[dict]
            Each element has keys ``"issue_key"``, ``"assignee"``, ``"text"``.
        """
        result: List[Dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            issue_key = str(item.get("jira_key") or "")
            assignee = str(item.get("owner") or "")
            description = str(item.get("description") or "")
            text = f"{issue_key}: {assignee} {description}".strip(": ")
            result.append(
                {"issue_key": issue_key, "assignee": assignee, "text": text})
        return result

    def _parse_text_header(self, lines: List[str]) -> Dict[str, str]:
        header: Dict[str, str] = {}
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if self._is_section_header(stripped):
                break
            if ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if key in {"date", "project"}:
                header[key] = value
        return header

    def _parse_text_attendees(self, lines: List[str]) -> List[str]:
        attendees: List[str] = []
        for line in self._collect_section_lines(lines, _SECTION_ATTENDEES):
            cleaned = line.lstrip("-").strip()
            if cleaned:
                attendees.append(cleaned)
        return attendees

    def _parse_text_action_items(self, lines: List[str]) -> List[Dict[str, str]]:
        items: List[Dict[str, str]] = []
        for line in self._collect_section_lines(lines, _SECTION_ACTION_ITEMS):
            cleaned = line.lstrip("-").strip()
            if not cleaned:
                continue
            match = _ISSUE_KEY_RE.search(cleaned)
            if not match:
                continue
            issue_key = match.group()
            rest = cleaned.split(":", 1)[1].strip() if ":" in cleaned else ""
            assignee = rest.split()[0] if rest else ""
            text = f"{issue_key}: {rest}".strip(": ")
            items.append(
                {"issue_key": issue_key, "assignee": assignee, "text": text})
        return items

    def _collect_section_lines(self, lines: List[str], section: str) -> List[str]:
        collected: List[str] = []
        in_section = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if self._is_section_header(stripped):
                in_section = stripped == section
                continue
            if in_section:
                collected.append(stripped)
        return collected

    @staticmethod
    def _is_section_header(line: str) -> bool:
        return line.startswith("[") and line.endswith("]")

    def _extract_issue_keys(self, text: str) -> List[str]:
        """Find every Jira-style issue key in *text*, deduplicated, order preserved.

        Uses the pattern ``r"[A-Z]+-\\d+"`` to match keys such as
        ``"AIP-1"``, ``"STORM-42"``, ``"ZOOKEEPER-100"``.

        Parameters
        ----------
        text:
            Arbitrary text to scan (typically the ``content`` field).

        Returns
        -------
        list[str]
            Unique keys in order of first appearance.
        """
        seen: dict[str, None] = {}  # ordered-set idiom (Python 3.7+)
        for match in _ISSUE_KEY_RE.finditer(text):
            seen[match.group()] = None
        return list(seen)
