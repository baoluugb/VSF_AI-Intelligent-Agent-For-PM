from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from config import DB_PATH


# Fields whose change counts as a "real" day-over-day update. Everything else
# (notably ``updated_at``, which moves on any edit) is cosmetic churn that must
# not be reported as a status change.
_MEANINGFUL_FIELDS = ("status", "assignee", "due_date", "priority")


def meaningful_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """Project an entity/snapshot dict down to the fields a diff cares about."""
    data = data or {}
    return {k: data.get(k) for k in _MEANINGFUL_FIELDS}


class SQLiteStore:
    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> "SQLiteStore":
        self._ensure_connection()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._connection is None:
            return
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()
        self._connection.close()
        self._connection = None

    def _ensure_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(self.db_path)
            self._connection.row_factory = sqlite3.Row
        return self._connection

    def _serialize_value(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return value

    def run_query(self, sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        """Run a read-only SELECT and return rows as plain dicts.

        Used by read-side consumers (e.g. the Concern Engine) that need ad-hoc
        SQL against the entities / snapshots / backlinks tables.
        """
        connection = self._ensure_connection()
        cursor = connection.execute(sql, tuple(params))
        return [dict(row) for row in cursor.fetchall()]

    def insert_audit_log(
        self, source_id: str, field: str, flag_type: str, snippet: str
    ) -> None:
        """Append a guardrail audit record: timestamp | source_id | field | flag_type | snippet."""
        connection = self._ensure_connection()
        connection.execute(
            """
            INSERT INTO audit_log (timestamp, source_id, field, flag_type, snippet)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                source_id,
                field,
                flag_type,
                snippet,
            ),
        )
        connection.commit()

    def upsert_entity(self, entity: Dict[str, Any]) -> None:
        connection = self._ensure_connection()
        connection.execute(
            """
			INSERT OR REPLACE INTO entities (
				task_id,
				source,
				title,
				status,
				assignee,
				priority,
				due_date,
				labels,
				description,
				url,
				created_at,
				updated_at
			) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			""",
            (
                entity.get("source_id") or entity.get("task_id"),
                entity.get("source"),
                entity.get("title"),
                entity.get("status"),
                entity.get("assignee"),
                entity.get("priority"),
                entity.get("due_date"),
                self._serialize_value(entity.get("labels")),
                entity.get("description"),
                entity.get("url"),
                entity.get("created_at"),
                entity.get("updated_at"),
            ),
        )
        connection.commit()

    def bulk_upsert(self, entities: List[Dict[str, Any]]) -> int:
        if not entities:
            return 0
        connection = self._ensure_connection()
        values = [
            (
                entity.get("source_id") or entity.get("task_id"),
                entity.get("source"),
                entity.get("title"),
                entity.get("status"),
                entity.get("assignee"),
                entity.get("priority"),
                entity.get("due_date"),
                self._serialize_value(entity.get("labels")),
                entity.get("description"),
                entity.get("url"),
                entity.get("created_at"),
                entity.get("updated_at"),
            )
            for entity in entities
        ]
        with connection:
            connection.executemany(
                """
				INSERT OR REPLACE INTO entities (
					task_id,
					source,
					title,
					status,
					assignee,
					priority,
					due_date,
					labels,
					description,
					url,
					created_at,
					updated_at
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
				""",
                values,
            )
        return len(values)

    def save_snapshot(
        self,
        task_id: str,
        data: Dict[str, Any],
        diff: Dict[str, Any] | None,
        snapshot_date: Optional[str] = None,
    ) -> None:
        connection = self._ensure_connection()
        snapshot_date = snapshot_date or date.today().isoformat()
        data_json = json.dumps(data)
        diff_json = json.dumps(diff) if diff is not None else None
        # INSERT OR REPLACE + the UNIQUE(task_id, snapshot_date) index keep one
        # snapshot per task per date, so a repeated same-day ingest updates the
        # row rather than accumulating duplicates.
        connection.execute(
            """
			INSERT OR REPLACE INTO snapshots (task_id, snapshot_date, data, diff_from_prev)
			VALUES (?, ?, ?, ?)
			""",
            (task_id, snapshot_date, data_json, diff_json),
        )
        connection.commit()

    def load_current_states(self) -> Dict[str, Dict[str, Any]]:
        """Return ``{task_id: meaningful_fields}`` for every stored entity in one
        query — the prior baseline used by incremental ingest to decide which
        entities actually changed (and thus need a fresh snapshot)."""
        rows = self.run_query(
            "SELECT task_id, status, assignee, due_date, priority FROM entities"
        )
        return {r["task_id"]: meaningful_fields(r) for r in rows}

    def get_daily_diff(self, date_value: str) -> List[Dict[str, Any]]:
        """Tasks whose *meaningful* fields changed at ``date_value`` vs. their most
        recent earlier snapshot.

        Robust to gaps: compares against ``MAX(snapshot_date) < date_value`` rather
        than strictly the day before (so a weekend/holiday with no run doesn't blank
        the diff). Ignores cosmetic churn (diffs only :data:`_MEANINGFUL_FIELDS`), and
        skips tasks with no prior snapshot (a baseline is not a "change"). Each row
        carries a ``changes`` dict of ``{field: [old, new]}``.
        """
        connection = self._ensure_connection()
        cursor = connection.execute(
            """
			SELECT
				today.task_id,
				today.snapshot_date AS snapshot_date,
				prev.snapshot_date AS previous_date,
				prev.data AS data_yesterday,
				today.data AS data_today,
				today.diff_from_prev AS diff_from_prev
			FROM snapshots AS today
			LEFT JOIN snapshots AS prev
			  ON prev.task_id = today.task_id
			 AND prev.snapshot_date = (
				 SELECT MAX(s.snapshot_date) FROM snapshots s
				 WHERE s.task_id = today.task_id AND s.snapshot_date < ?
			 )
			WHERE today.snapshot_date = ?
			""",
            (date_value, date_value),
        )

        results: List[Dict[str, Any]] = []
        for row in cursor.fetchall():
            row = dict(row)
            if not row.get("data_yesterday"):
                continue  # no prior snapshot → baseline, not a change
            today_state = meaningful_fields(json.loads(row["data_today"] or "{}"))
            prev_state = meaningful_fields(json.loads(row["data_yesterday"] or "{}"))
            changes = {
                k: [prev_state.get(k), today_state.get(k)]
                for k in _MEANINGFUL_FIELDS
                if prev_state.get(k) != today_state.get(k)
            }
            if not changes:
                continue
            row["changes"] = changes
            results.append(row)
        return results

    def diff_since(self, from_date: str) -> List[Dict[str, Any]]:
        """Net diff: how each task's meaningful fields differ **now** (the live
        ``entities`` state) vs. its state **as of** ``from_date`` — i.e. "what
        changed since last week / month / year".

        "State as of ``from_date``" is the most recent snapshot with
        ``snapshot_date <= from_date`` (point-in-time reconstruction over the
        delta-only changelog), so an exact snapshot on that day isn't needed — the
        last known state at or before it is used.

        A task is reported when either:
          * it has a prior state and a meaningful field changed, or
          * it was *created after* ``from_date`` (genuinely new since then).
        A task with **no** snapshot on/before ``from_date`` that was also created
        on/before it is skipped — there's no history that far back, so no change
        can be asserted (avoids flooding "everything is new" before the baseline).
        """
        rows = self.run_query(
            """
			SELECT e.task_id, e.title, e.created_at,
			       e.status, e.assignee, e.due_date, e.priority,
			       p.data AS past_data
			FROM entities e
			LEFT JOIN (
				SELECT s.task_id, s.data
				FROM snapshots s
				JOIN (
					SELECT task_id, MAX(snapshot_date) AS md
					FROM snapshots
					WHERE snapshot_date <= ?
					GROUP BY task_id
				) m ON m.task_id = s.task_id AND m.md = s.snapshot_date
			) p ON p.task_id = e.task_id
			""",
            (from_date,),
        )

        results: List[Dict[str, Any]] = []
        for r in rows:
            current = meaningful_fields(r)
            if r.get("past_data"):
                past = meaningful_fields(json.loads(r["past_data"]))
                changes = {
                    k: [past.get(k), current.get(k)]
                    for k in _MEANINGFUL_FIELDS
                    if past.get(k) != current.get(k)
                }
                if changes:
                    results.append(
                        {"task_id": r["task_id"], "title": r["title"],
                         "is_new": False, "changes": changes}
                    )
            else:
                created = (r.get("created_at") or "")[:10]
                if created and created > from_date:
                    results.append(
                        {"task_id": r["task_id"], "title": r["title"], "is_new": True,
                         "changes": {k: [None, current.get(k)]
                                     for k in _MEANINGFUL_FIELDS if current.get(k) is not None}}
                    )
                # else: no history before from_date → cannot assert a change → skip.
        return results

    def query_entity(self, task_id: str) -> Dict[str, Any] | None:
        connection = self._ensure_connection()
        cursor = connection.execute(
            "SELECT * FROM entities WHERE task_id = ?",
            (task_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_sync_log(self, source: str) -> None:
        connection = self._ensure_connection()
        connection.execute(
            """
			INSERT INTO sync_log (source, last_run_date)
			VALUES (?, DATE('now'))
			ON CONFLICT(source) DO UPDATE
			SET last_run_date = excluded.last_run_date
			""",
            (source,),
        )
        connection.commit()

    def insert_backlinks(self, backlinks: List[Dict[str, Any]]) -> int:
        if not backlinks:
            return 0
        connection = self._ensure_connection()
        values = [
            (
                link.get("source_entity_id"),
                link.get("target_entity_id"),
                link.get("link_type"),
                link.get("context"),
            )
            for link in backlinks
        ]
        with connection:
            connection.executemany(
                """
				INSERT INTO backlinks (
					source_entity_id,
					target_entity_id,
					link_type,
					context
				) VALUES (?, ?, ?, ?)
				""",
                values,
            )
        return len(values)

    def replace_backlinks(self, backlinks: List[Dict[str, Any]]) -> int:
        """Clear and re-insert all backlinks.

        Backlinks are fully derived from the current document set, so an
        incremental re-ingest (which doesn't reset the store) must replace them
        rather than append — otherwise repeated runs duplicate every link and
        inflate the blocker rule's ``dependent_count``.
        """
        connection = self._ensure_connection()
        with connection:
            connection.execute("DELETE FROM backlinks")
        return self.insert_backlinks(backlinks)
