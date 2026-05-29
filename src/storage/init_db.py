import argparse
import sqlite3
from pathlib import Path
from typing import Union

PathLike = Union[str, Path]

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "vault.db"


def get_connection(db_path: PathLike = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path: PathLike = DEFAULT_DB_PATH) -> None:
    connection = get_connection(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.executescript(
            """
			CREATE TABLE IF NOT EXISTS entities (
				task_id TEXT PRIMARY KEY,
				source TEXT,
				title TEXT,
				status TEXT,
				assignee TEXT,
				priority TEXT,
				due_date TEXT,
				labels TEXT,
				description TEXT,
				url TEXT,
				created_at TEXT,
				updated_at TEXT
			);

			CREATE TABLE IF NOT EXISTS snapshots (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				task_id TEXT NOT NULL,
				snapshot_date TEXT NOT NULL,
				data TEXT,
				diff_from_prev TEXT,
				FOREIGN KEY (task_id) REFERENCES entities(task_id)
			);

			CREATE TABLE IF NOT EXISTS backlinks (
				id INTEGER PRIMARY KEY,
				source_entity_id TEXT,
				target_entity_id TEXT,
				link_type TEXT,
				context TEXT
			);

			CREATE TABLE IF NOT EXISTS sync_log (
				source TEXT PRIMARY KEY,
				last_run_date TEXT
			);

			CREATE TABLE IF NOT EXISTS audit_log (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				timestamp TEXT,
				source_id TEXT,
				field TEXT,
				flag_type TEXT,
				snippet TEXT
			);

			CREATE INDEX IF NOT EXISTS idx_entities_status
				ON entities(status);
			CREATE INDEX IF NOT EXISTS idx_entities_updated_at
				ON entities(updated_at);
			CREATE INDEX IF NOT EXISTS idx_snapshots_task_id_snapshot_date
				ON snapshots(task_id, snapshot_date);
			"""
        )
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize the SQLite database.")
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="Path to the SQLite database file.",
    )
    args = parser.parse_args()
    init_db(args.db_path)


if __name__ == "__main__":
    main()
