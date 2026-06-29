"""Unit tests for Slack delivery (no network — httpx.post is mocked).

Covers the two guarantees: it posts a digest (with the date + top concern) when
a webhook is configured, and it is a no-op (no HTTP call) when it isn't — so the
default run_agent flow is unaffected.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from delivery.slack import build_digest_text, post_concerns_digest

_CONCERNS = [
    {"type": "deadline_risk", "task_id": "FLINK-40", "severity": 5,
     "explanation": "Overdue 2 days", "details": {}},
    {"type": "unresolved_blocker", "task_id": "KAFKA-64", "severity": 4,
     "explanation": "Blocker open 11 days", "details": {}},
    {"type": "stalled_task", "task_id": "OLD-1", "severity": 2,
     "explanation": "chronic backlog", "details": {"chronic": True}},
]


def test_build_digest_text_has_date_summary_and_top_actions():
    text = build_digest_text("2025-05-30", _CONCERNS, lang="en")
    assert "2025-05-30" in text
    assert "Risk summary" in text          # from format_summary (en)
    assert "FLINK-40" in text              # highest-severity actionable item
    assert "OLD-1" not in text             # chronic excluded from top actions


def test_posts_digest_when_webhook_set():
    with patch("delivery.slack.httpx.post") as mock_post:
        mock_post.return_value = MagicMock()  # raise_for_status() is a no-op
        delivered = post_concerns_digest(
            "2025-05-30", _CONCERNS, "https://hooks.slack.com/services/X", lang="en")

    assert delivered is True
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert "FLINK-40" in payload["text"]
    assert "2025-05-30" in payload["text"]


def test_noop_when_webhook_unset():
    with patch("delivery.slack.httpx.post") as mock_post:
        delivered = post_concerns_digest("2025-05-30", _CONCERNS, "")

    assert delivered is False
    mock_post.assert_not_called()
