from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

from config import DB_PATH


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

    def save_snapshot(self, task_id: str, data: Dict[str, Any], diff: Dict[str, Any] | None) -> None:
        connection = self._ensure_connection()
        snapshot_date = date.today().isoformat()
        data_json = json.dumps(data)
        diff_json = json.dumps(diff) if diff is not None else None
        connection.execute(
            """
			INSERT INTO snapshots (task_id, snapshot_date, data, diff_from_prev)
			VALUES (?, ?, ?, ?)
			""",
            (task_id, snapshot_date, data_json, diff_json),
        )
        connection.commit()

    def get_daily_diff(self, date_value: str) -> List[Dict[str, Any]]:
        connection = self._ensure_connection()
        cursor = connection.execute(
            """
			SELECT
				today.task_id,
				today.snapshot_date AS snapshot_date,
				yesterday.snapshot_date AS previous_date,
				yesterday.data AS data_yesterday,
				today.data AS data_today,
				today.diff_from_prev AS diff_from_prev
			FROM snapshots AS today
			JOIN snapshots AS yesterday
			  ON today.task_id = yesterday.task_id
			WHERE today.snapshot_date = ?
			  AND yesterday.snapshot_date = DATE(?, '-1 day')
			  AND (today.data != yesterday.data OR today.diff_from_prev IS NOT NULL)
			""",
            (date_value, date_value),
        )
        return [dict(row) for row in cursor.fetchall()]

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
