"""Incremental ingestion: re-embedding is skipped for unchanged docs.

The ChromaDB embed step is the slow part of a daily run. ``run_pipeline`` now
hashes each normalized doc and skips re-embedding when the hash matches the
stored one, so an unchanged re-run embeds nothing. Proven here via the returned
``embedded_docs`` / ``skipped_unchanged`` counters (deterministic, no timing).

Tiny Jira fixture into isolated temp SQLite + temp ChromaDB (no network: Chroma
uses its bundled local embedder, same as test_run_pipeline.py).
"""
from __future__ import annotations

import json

from ingestion.run_pipeline import run_pipeline


def _jira_file(tmp_path, name, issues):
    """issues: list of (key, description). Returns the written file path."""
    payload = {
        "source": "Apache",
        "issues": [
            {
                "key": key,
                "self": f"https://jira/{key}",
                "fields": {
                    "summary": f"Summary {key}",
                    "status": {"name": "In Progress"},
                    "assignee": {"displayName": "Minh Tuan"},
                    "priority": {"name": "High"},
                    "labels": ["backend"],
                    "duedate": "2025-05-24",
                    "description": desc,
                    "created": "2025-05-01T09:00:00.000+0000",
                    "updated": "2025-05-20T09:00:00.000+0000",
                },
            }
            for key, desc in issues
        ],
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _run(path, db, chroma, as_of):
    return run_pipeline(path, None, None, db_path=db, chroma_path=chroma, as_of=as_of)


def test_unchanged_rerun_skips_all_embeds(tmp_path):
    db, chroma = str(tmp_path / "v.db"), str(tmp_path / "ch")
    f = _jira_file(tmp_path, "d.json", [("AIP-1", "alpha"), ("AIP-2", "beta")])

    first = _run(f, db, chroma, "2025-05-30")
    assert first["embedded_docs"] == 2 and first["skipped_unchanged"] == 0

    again = _run(f, db, chroma, "2025-05-31")  # same data, no reset
    assert again["embedded_docs"] == 0
    assert again["skipped_unchanged"] == 2


def test_only_changed_doc_re_embeds(tmp_path):
    db, chroma = str(tmp_path / "v.db"), str(tmp_path / "ch")
    f1 = _jira_file(tmp_path, "d1.json", [("AIP-1", "alpha"), ("AIP-2", "beta")])
    _run(f1, db, chroma, "2025-05-30")

    f2 = _jira_file(
        tmp_path, "d2.json", [("AIP-1", "alpha"), ("AIP-2", "beta CHANGED")]
    )
    stats = _run(f2, db, chroma, "2025-05-31")

    assert stats["embedded_docs"] == 1      # only AIP-2 re-embedded
    assert stats["skipped_unchanged"] == 1  # AIP-1 unchanged


def test_new_doc_embeds_only_the_new_one(tmp_path):
    db, chroma = str(tmp_path / "v.db"), str(tmp_path / "ch")
    f1 = _jira_file(tmp_path, "d1.json", [("AIP-1", "alpha")])
    _run(f1, db, chroma, "2025-05-30")

    f2 = _jira_file(tmp_path, "d2.json", [("AIP-1", "alpha"), ("AIP-2", "beta")])
    stats = _run(f2, db, chroma, "2025-05-31")

    assert stats["embedded_docs"] == 1      # only the new AIP-2
    assert stats["skipped_unchanged"] == 1  # AIP-1 unchanged
