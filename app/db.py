from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS mails (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alias_id INTEGER NOT NULL,
                    address TEXT NOT NULL,
                    source TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    raw TEXT NOT NULL,
                    gmail_uid INTEGER,
                    received_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(alias_id, gmail_uid)
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

    def create_mail(
        self,
        *,
        alias_id: int,
        address: str,
        source: str,
        message_id: str,
        raw: str,
        gmail_uid: int | None = None,
        received_at: datetime | None = None,
    ) -> dict[str, Any]:
        created_at = datetime.now(UTC)
        mail_received_at = received_at or created_at
        with self.connect() as connection:
            cursor = connection.execute(
                '''
                INSERT INTO mails(alias_id, address, source, message_id, raw, gmail_uid, received_at, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    alias_id,
                    address,
                    source,
                    message_id,
                    raw,
                    gmail_uid,
                    mail_received_at.isoformat(),
                    created_at.isoformat(),
                ),
            )
            connection.commit()
            mail_id = int(cursor.lastrowid)
        return self.get_mail(mail_id, address)

    def list_mails(self, address: str, limit: int, offset: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                '''
                SELECT id, address, source, message_id, raw, received_at, created_at
                FROM mails
                WHERE address = ?
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                ''',
                (address, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_mails(self, address: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                'SELECT count(*) AS count FROM mails WHERE address = ?',
                (address,),
            ).fetchone()
        return int(row['count']) if row else 0

    def get_mail(self, mail_id: int, address: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                '''
                SELECT id, address, source, message_id, raw, received_at, created_at
                FROM mails
                WHERE id = ? AND address = ?
                ''',
                (mail_id, address),
            ).fetchone()
        return dict(row) if row else None

    def delete_mail(self, mail_id: int, address: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                'DELETE FROM mails WHERE id = ? AND address = ?',
                (mail_id, address),
            )
            connection.commit()
        return cursor.rowcount > 0
