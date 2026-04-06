from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(slots=True)
class AliasRecord:
    id: int
    address: str
    created_at: datetime
    expires_at: datetime


class Database:
    def __init__(self, database_path: str):
        self.database_path = database_path
        self._ensure_parent_directory()

    def _ensure_parent_directory(self) -> None:
        if self.database_path == ':memory:':
            return
        Path(self.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                '''
            )
            connection.commit()

    def create_alias(self, address: str, created_at: datetime, expires_at: datetime) -> AliasRecord:
        with self.connect() as connection:
            cursor = connection.execute(
                'INSERT INTO aliases(address, created_at, expires_at) VALUES(?, ?, ?)',
                (address, created_at.isoformat(), expires_at.isoformat()),
            )
            connection.commit()
            alias_id = int(cursor.lastrowid)

        return AliasRecord(
            id=alias_id,
            address=address,
            created_at=created_at.astimezone(UTC),
            expires_at=expires_at.astimezone(UTC),
        )
