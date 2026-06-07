from __future__ import annotations
from ingestion.meeting_notes_connector import MeetingNotesConnector

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


SAMPLE_NOTE = (
    "date: 2025-05-21\n"
    "project: AIP\n"
    "attendees_raw: Minh Tuan, Bao Chau\n\n"
    "[Attendees]\n"
    "- Minh Tuan (Tech Lead)\n"
    "- Bao Chau (Backend)\n\n"
    "[Action Items]\n"
    "- AIP-45: Minh hoan thien ingestion pipeline truoc 2025-05-24\n"
    "- AIP-67: Bao Chau review vault schema - pending, no update\n"
)


def _write_sample_note(tmp_path: Path) -> Path:
    path = tmp_path / "MTG-2025-05-21.txt"
    path.write_text(SAMPLE_NOTE, encoding="utf-8")
    return path


def _load_sample(tmp_path: Path) -> list[dict]:
    _write_sample_note(tmp_path)
    connector = MeetingNotesConnector(str(tmp_path))
    return connector.load()


def test_load_single_note(tmp_path: Path) -> None:
    result = _load_sample(tmp_path)

    assert len(result) == 1
    assert result[0]["source"] == "meeting_notes"
    assert result[0]["source_id"] == "MTG-2025-05-21"


def test_parse_header(tmp_path: Path) -> None:
    result = _load_sample(tmp_path)

    assert result[0]["date"] == "2025-05-21"
    assert result[0]["project"] == "AIP"


def test_parse_attendees(tmp_path: Path) -> None:
    result = _load_sample(tmp_path)

    assert result[0]["attendees"] == [
        "Minh Tuan (Tech Lead)",
        "Bao Chau (Backend)",
    ]


def test_parse_action_items(tmp_path: Path) -> None:
    result = _load_sample(tmp_path)

    action_items = result[0]["action_items"]
    assert len(action_items) == 2
    assert action_items[0]["issue_key"] == "AIP-45"
    assert action_items[0]["assignee"] == "Minh"


def test_mentioned_keys(tmp_path: Path) -> None:
    result = _load_sample(tmp_path)

    assert set(result[0]["mentioned_keys"]) == {"AIP-45", "AIP-67"}


def test_text_content_is_full_file(tmp_path: Path) -> None:
    result = _load_sample(tmp_path)

    assert "AIP-45" in result[0]["text_content"]
    assert "[Action Items]" in result[0]["text_content"]


def test_missing_file_does_not_crash(tmp_path: Path) -> None:
    connector = MeetingNotesConnector(str(tmp_path))
    result = connector.load()

    assert result == []


def test_load_meeting_notes_json() -> None:
    connector = MeetingNotesConnector("data/meeting_notes")
    notes = connector.load()

    assert len(notes) == 5
