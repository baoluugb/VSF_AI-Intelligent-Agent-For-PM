"""Jira Cloud REST connector (roadmap P1) — live alternative to the synthetic
``JiraConnector``. Selected when ``SOURCE_MODE=api``.

It fetches issues from the Jira Cloud REST API (``/rest/api/3/search`` with JQL
+ pagination, HTTP-basic auth: Atlassian account email + API token) and reuses
``JiraConnector._normalize_issue`` (and its ADF text extraction) so a live issue
becomes the exact same normalized doc the rest of the pipeline already consumes.

NOTE: not exercised against a live Jira in CI (no test instance); covered by
mocked-``httpx`` unit tests. Point it at a real instance via ``.env``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import httpx

from ingestion.jira_connector import JiraConnector

logger = logging.getLogger(__name__)

_DEFAULT_JQL = "updated >= -7d ORDER BY updated DESC"
_PAGE_SIZE = 100
_TIMEOUT_S = 30.0
# Only the fields JiraConnector._normalize_issue reads — keeps payloads small.
_FIELDS = "summary,status,assignee,priority,labels,duedate,description,created,updated"


class JiraApiConnector(JiraConnector):
    """Fetch issues from Jira Cloud and normalize them via ``JiraConnector``."""

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        *,
        jql: str = _DEFAULT_JQL,
        page_size: int = _PAGE_SIZE,
    ) -> None:
        if not (base_url and email and api_token):
            raise ValueError(
                "JiraApiConnector requires base_url, email and api_token."
            )
        # Deliberately does NOT call super().__init__ (no json_path); only the
        # inherited _normalize_issue / _extract_adf_text helpers are reused.
        self.base_url = base_url.rstrip("/")
        self.auth = (email, api_token)
        self.jql = jql
        self.page_size = page_size

    def load(self) -> List[Dict[str, Any]]:
        return [self._normalize_issue(issue) for issue in self._fetch_all_issues()]

    def _fetch_all_issues(self) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/rest/api/3/search"
        issues: List[Dict[str, Any]] = []
        start_at = 0
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            while True:
                resp = client.get(
                    url,
                    auth=self.auth,
                    params={
                        "jql": self.jql,
                        "startAt": start_at,
                        "maxResults": self.page_size,
                        "fields": _FIELDS,
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
                batch = payload.get("issues", []) or []
                issues.extend(batch)
                start_at += len(batch)
                total = payload.get("total", len(issues))
                if not batch or start_at >= total:
                    break
        logger.info(
            "JiraApiConnector: fetched %d issue(s) from %s", len(issues), self.base_url
        )
        return issues
