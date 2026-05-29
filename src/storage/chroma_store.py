"""Vector store backed by ChromaDB with three purpose-specific collections."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.api import ClientAPI as ChromaClientAPI
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from config import (
    CHUNK_OVERLAP_CONFLUENCE,
    CHUNK_OVERLAP_MEETING,
    CHUNK_SIZE_CONFLUENCE,
    CHUNK_SIZE_MEETING,
)

_CONFLUENCE_HEADERS = [("##", "section"), ("###", "subsection")]
_MEETING_SEPARATORS = ["[Action Items]", "[Attendees]", "\n\n", "\n", ". ", " "]

_COLLECTION_CONFLUENCE = "confluence_chunks"
_COLLECTION_MEETING = "meeting_chunks"
_COLLECTION_JIRA = "jira_descriptions"


class ChromaStore:
    """Wrapper around a ChromaDB PersistentClient with three collections."""

    def __init__(
        self,
        path: str = "",
        *,
        client: Optional[ChromaClientAPI] = None,
    ) -> None:
        """Initialise the store.

        Parameters
        ----------
        path:
            Filesystem path passed to ``chromadb.PersistentClient``.  Ignored
            when *client* is provided explicitly.
        client:
            A pre-built ChromaDB client (e.g. ``chromadb.EphemeralClient()``
            for unit tests).  When supplied, *path* is not used.
        """
        self._client: ChromaClientAPI = (
            client if client is not None else chromadb.PersistentClient(path=path)
        )

        self._confluence = self._client.get_or_create_collection(_COLLECTION_CONFLUENCE)
        self._meeting = self._client.get_or_create_collection(_COLLECTION_MEETING)
        self._jira = self._client.get_or_create_collection(_COLLECTION_JIRA)

        self._collections: Dict[str, chromadb.Collection] = {
            _COLLECTION_CONFLUENCE: self._confluence,
            _COLLECTION_MEETING: self._meeting,
            _COLLECTION_JIRA: self._jira,
        }

    # ------------------------------------------------------------------
    # Ingestion helpers
    # ------------------------------------------------------------------

    def add_confluence_chunks(self, page: Dict[str, Any]) -> int:
        """Split a Confluence page by Markdown headers and upsert the chunks.

        Parameters
        ----------
        page:
            A single page dict expected to contain at least ``content``,
            ``page_id``, ``space``, ``author``, ``last_updated``, ``status``,
            and ``linked_jira_epics``.

        Returns
        -------
        int
            Number of chunks added to the collection.
        """
        content: str = page.get("content") or ""
        if not content.strip():
            return 0

        # 1. Split on Markdown headers first
        header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=_CONFLUENCE_HEADERS,
            strip_headers=False,
        )
        header_docs = header_splitter.split_text(content)

        # 2. Further chunk oversized sections by character count
        char_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE_CONFLUENCE,
            chunk_overlap=CHUNK_OVERLAP_CONFLUENCE,
        )
        docs = char_splitter.split_documents(header_docs)

        if not docs:
            return 0

        epics: List[str] = page.get("linked_jira_epics") or []
        base_meta = {
            "page_id": str(page.get("page_id") or ""),
            "space": str(page.get("space") or ""),
            "author": str(page.get("author") or ""),
            "last_updated": str(page.get("last_updated") or ""),
            "status": str(page.get("status") or ""),
            "linked_jira_epics": ",".join(str(e) for e in epics),
        }

        ids, texts, metadatas = [], [], []
        for doc in docs:
            section = doc.metadata.get("section") or doc.metadata.get("subsection") or ""
            meta = {**base_meta, "section": str(section)}
            ids.append(str(uuid.uuid4()))
            texts.append(doc.page_content)
            metadatas.append(meta)

        self._confluence.add(ids=ids, documents=texts, metadatas=metadatas)
        return len(ids)

    def add_meeting_chunks(self, note: Dict[str, Any]) -> int:
        """Chunk a meeting note with a separator-aware splitter and upsert.

        Parameters
        ----------
        note:
            A meeting note dict with at least ``content``, ``meeting_id`` (or
            ``note_id``), ``date``, and ``project``.

        Returns
        -------
        int
            Number of chunks added to the collection.
        """
        content: str = note.get("content") or ""
        if not content.strip():
            return 0

        splitter = RecursiveCharacterTextSplitter(
            separators=_MEETING_SEPARATORS,
            chunk_size=CHUNK_SIZE_MEETING,
            chunk_overlap=CHUNK_OVERLAP_MEETING,
        )
        chunks = splitter.split_text(content)

        if not chunks:
            return 0

        note_id = str(note.get("note_id") or note.get("meeting_id") or "")
        meta = {
            "note_id": note_id,
            "date": str(note.get("date") or ""),
            "project": str(note.get("project") or ""),
        }

        ids = [str(uuid.uuid4()) for _ in chunks]
        metadatas = [meta for _ in chunks]

        self._meeting.add(ids=ids, documents=chunks, metadatas=metadatas)
        return len(ids)

    def add_jira_description(self, entity: Dict[str, Any]) -> None:
        """Store a Jira issue description as a single, un-chunked document.

        Parameters
        ----------
        entity:
            A normalised Jira entity dict (as produced by ``JiraConnector``).
            Must contain ``description`` and ``source_id``.
        """
        description: str = entity.get("description") or ""
        if not description.strip():
            return

        source_id = str(entity.get("source_id") or entity.get("task_id") or "")
        meta = {
            "source_id": source_id,
            "status": str(entity.get("status") or ""),
            "assignee": str(entity.get("assignee") or ""),
            "source": "jira",
        }

        # Use source_id as the document ID so repeated calls are idempotent.
        doc_id = source_id if source_id else str(uuid.uuid4())
        self._jira.upsert(ids=[doc_id], documents=[description], metadatas=[meta])

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        collection: str,
        query_text: str,
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Semantic search over a named collection.

        Parameters
        ----------
        collection:
            One of ``"confluence_chunks"``, ``"meeting_chunks"``, or
            ``"jira_descriptions"``.
        query_text:
            The natural-language query string.
        n_results:
            Maximum number of results to return.
        where:
            Optional ChromaDB metadata filter dict (e.g. ``{"status": "Done"}``).

        Returns
        -------
        list[dict]
            Each element is a dict with keys ``document``, ``metadata``, and
            ``distance``.

        Raises
        ------
        KeyError
            If *collection* is not one of the three managed collections.
        """
        col = self._collections.get(collection)
        if col is None:
            raise KeyError(
                f"Unknown collection {collection!r}. "
                f"Choose from: {list(self._collections.keys())}"
            )

        kwargs: Dict[str, Any] = {
            "query_texts": [query_text],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = col.query(**kwargs)

        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        return [
            {"document": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(documents, metadatas, distances)
        ]
