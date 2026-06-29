"""Confluence Cloud REST connector (roadmap P1) — live alternative to the
synthetic ``ConfluenceConnector``. Selected when ``SOURCE_MODE=api``.

Fetches pages from the Confluence Cloud REST API
(``/wiki/rest/api/content`` with ``body.storage`` expansion + pagination,
HTTP-basic auth: Atlassian account email + API token) and emits the same
normalized page dict the pipeline already routes into ChromaDB.

Limitations (MVP): the storage format is XHTML, lightly stripped to text here;
``linked_jira_epics`` isn't returned by this endpoint so it's left empty (epic
linkage stays a future refinement). Not exercised against a live instance in CI;
covered by mocked-``httpx`` unit tests.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_PAGE_SIZE = 50
_TIMEOUT_S = 30.0
_EXPAND = "body.storage,version,space,metadata.labels"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    """Rough XHTML→text: drop tags and collapse whitespace (MVP, no new dep)."""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", html or "")).strip()


class ConfluenceApiConnector:
    """Fetch and normalize Confluence Cloud pages."""

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        *,
        space_key: Optional[str] = None,
        page_size: int = _PAGE_SIZE,
    ) -> None:
        if not (base_url and email and api_token):
            raise ValueError(
                "ConfluenceApiConnector requires base_url, email and api_token."
            )
        self.base_url = base_url.rstrip("/")
        self.auth = (email, api_token)
        self.space_key = space_key
        self.page_size = page_size

    def load(self) -> List[Dict[str, Any]]:
        return [self._normalize(page) for page in self._fetch_all_pages()]

    def _fetch_all_pages(self) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/wiki/rest/api/content"
        pages: List[Dict[str, Any]] = []
        start = 0
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            while True:
                params: Dict[str, Any] = {
                    "expand": _EXPAND,
                    "start": start,
                    "limit": self.page_size,
                }
                if self.space_key:
                    params["spaceKey"] = self.space_key
                resp = client.get(url, auth=self.auth, params=params)
                resp.raise_for_status()
                results = resp.json().get("results", []) or []
                pages.extend(results)
                if len(results) < self.page_size:
                    break
                start += len(results)
        logger.info(
            "ConfluenceApiConnector: fetched %d page(s) from %s",
            len(pages),
            self.base_url,
        )
        return pages

    def _normalize(self, page: Dict[str, Any]) -> Dict[str, Any]:
        body = (((page.get("body") or {}).get("storage") or {}).get("value")) or ""
        meta = page.get("metadata") or {}
        labels = ((meta.get("labels") or {}).get("results")) or []
        return {
            "source": "confluence",
            "source_id": str(page.get("id")),
            "title": page.get("title") or "",
            "space": (page.get("space") or {}).get("key") or "",
            "author": "",
            "last_updated": (page.get("version") or {}).get("when") or "",
            "status": page.get("status") or "current",
            "linked_jira_epics": [],
            "tags": [lbl.get("name") for lbl in labels if lbl.get("name")],
            "text_content": _strip_html(body),
            "url": f"{self.base_url}/wiki/pages/{page.get('id')}",
        }
