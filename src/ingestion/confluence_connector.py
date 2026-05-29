"""Connector that reads Confluence pages from a folder of JSON files."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS: frozenset[str] = frozenset({"page_id", "title", "space", "content"})
_VALID_STATUSES: frozenset[str] = frozenset({"current", "outdated", "draft"})
_CONFLUENCE_BASE_URL = "https://confluence.internal/pages"


class ConfluenceConnector:
    """Read and normalise Confluence pages from a directory of JSON files.

    Each JSON file in *folder_path* must represent a single Confluence page.
    Files that are missing required fields are skipped with a warning.
    Chunking is **not** performed here — pass the returned dicts to
    ``ChromaStore.add_confluence_chunks()`` for that step.

    Parameters
    ----------
    folder_path:
        Path to the directory containing ``*.json`` page files.
    """

    def __init__(self, folder_path: str) -> None:
        self.folder_path = Path(folder_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> List[Dict[str, Any]]:
        """Read every ``*.json`` file in the folder and return normalised pages.

        Files that cannot be parsed or that fail field validation are skipped.

        Returns
        -------
        list[dict]
            Normalised page dicts, one per valid JSON file.

        Raises
        ------
        FileNotFoundError
            If *folder_path* does not exist or is not a directory.
        """
        if not self.folder_path.exists():
            raise FileNotFoundError(
                f"Confluence folder not found: {self.folder_path}"
            )
        if not self.folder_path.is_dir():
            raise FileNotFoundError(
                f"Expected a directory, got a file: {self.folder_path}"
            )

        json_files = sorted(self.folder_path.glob("*.json"))
        if not json_files:
            logger.warning("No .json files found in %s", self.folder_path)
            return []

        pages: List[Dict[str, Any]] = []
        for filepath in json_files:
            result = self._parse_file(str(filepath))
            if result is not None:
                pages.append(result)

        logger.info(
            "ConfluenceConnector: loaded %d/%d pages from %s",
            len(pages),
            len(json_files),
            self.folder_path,
        )
        return pages

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_file(self, filepath: str) -> Optional[Dict[str, Any]]:
        """Parse and validate a single Confluence JSON file.

        Parameters
        ----------
        filepath:
            Absolute or relative path to the JSON file.

        Returns
        -------
        dict | None
            Normalised page dict, or ``None`` if the file is invalid.
        """
        path = Path(filepath)

        try:
            raw: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Skipping %s — could not read/parse: %s", path.name, exc)
            return None

        # Validate required fields
        missing = _REQUIRED_FIELDS - raw.keys()
        if missing:
            logger.warning(
                "Skipping %s — missing required fields: %s",
                path.name,
                sorted(missing),
            )
            return None

        # Validate non-empty required fields
        for field in _REQUIRED_FIELDS:
            if not raw.get(field):
                logger.warning(
                    "Skipping %s — required field %r is empty.", path.name, field
                )
                return None

        page_id: str = str(raw["page_id"])

        # Normalise status — fall back to "draft" if unrecognised
        status: str = str(raw.get("status") or "draft")
        if status not in _VALID_STATUSES:
            logger.warning(
                "Page %s has unrecognised status %r — treating as 'draft'.",
                page_id,
                status,
            )
            status = "draft"

        linked_jira_epics: List[str] = [
            str(e) for e in (raw.get("linked_jira_epics") or [])
        ]
        tags: List[str] = [str(t) for t in (raw.get("tags") or [])]

        return {
            "source": "confluence",
            "source_id": page_id,
            "title": str(raw["title"]),
            "space": str(raw["space"]),
            "author": str(raw.get("author") or ""),
            "last_updated": str(raw.get("last_updated") or ""),
            "status": status,
            "linked_jira_epics": linked_jira_epics,
            "tags": tags,
            "text_content": str(raw["content"]),
            "url": self._build_url(page_id),
        }

    def _build_url(self, page_id: str) -> str:
        """Return a placeholder Confluence URL for the given page ID.

        Parameters
        ----------
        page_id:
            The page identifier (e.g. ``"CONF-001"``).

        Returns
        -------
        str
            URL in the form ``"https://confluence.internal/pages/{page_id}"``.
        """
        return f"{_CONFLUENCE_BASE_URL}/{page_id}"
