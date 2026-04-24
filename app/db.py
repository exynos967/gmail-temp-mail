from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.aliasing import normalize_gmail_alias_identity


@dataclass(slots=True)
class AliasRecord:
    id: int
    address: str
    account_address: str
    created_at: datetime
    expires_at: datetime
    start_uid: int = 0


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
                    account_address TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    start_uid INTEGER NOT NULL DEFAULT 0
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
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS service_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                '''
            )
            connection.execute(
                '''
                CREATE INDEX IF NOT EXISTS idx_aliases_account_expiry_start_uid
                ON aliases(account_address, expires_at, start_uid)
                '''
            )
            connection.execute(
                '''
                CREATE INDEX IF NOT EXISTS idx_mails_address_id
                ON mails(address, id DESC)
                '''
            )
            self._ensure_aliases_account_address_column(connection)
            self._ensure_aliases_start_uid_column(connection)
            connection.commit()

    def _ensure_aliases_account_address_column(self, connection: sqlite3.Connection) -> None:
        columns = {
            row['name'] for row in connection.execute('PRAGMA table_info(aliases)').fetchall()
        }
        if 'account_address' not in columns:
            connection.execute(
                "ALTER TABLE aliases ADD COLUMN account_address TEXT NOT NULL DEFAULT ''"
            )

    def _ensure_aliases_start_uid_column(self, connection: sqlite3.Connection) -> None:
        columns = {
            row['name'] for row in connection.execute('PRAGMA table_info(aliases)').fetchall()
        }
        if 'start_uid' not in columns:
            connection.execute(
                'ALTER TABLE aliases ADD COLUMN start_uid INTEGER NOT NULL DEFAULT 0'
            )

    def create_alias(
        self,
        address: str,
        account_address: str,
        created_at: datetime,
        expires_at: datetime,
        start_uid: int = 0,
    ) -> AliasRecord:
        alias_identity = normalize_gmail_alias_identity(address)
        with self.connect() as connection:
            rows = connection.execute('SELECT address FROM aliases').fetchall()
            existing_addresses = [str(row['address']) for row in rows]
            has_plus_tag = alias_identity != normalize_gmail_alias_identity(account_address)
            alias_exists = (
                any(
                    normalize_gmail_alias_identity(existing_address) == alias_identity
                    for existing_address in existing_addresses
                )
                if has_plus_tag
                else any(existing_address.lower() == address.lower() for existing_address in existing_addresses)
            )
            if alias_exists:
                raise sqlite3.IntegrityError('alias address already exists')
            cursor = connection.execute(
                '''
                INSERT INTO aliases(address, account_address, created_at, expires_at, start_uid)
                VALUES(?, ?, ?, ?, ?)
                ''',
                (
                    address,
                    account_address,
                    created_at.isoformat(),
                    expires_at.isoformat(),
                    start_uid,
                ),
            )
            connection.commit()
            alias_id = int(cursor.lastrowid)

        return AliasRecord(
            id=alias_id,
            address=address,
            account_address=account_address,
            created_at=created_at.astimezone(UTC),
            expires_at=expires_at.astimezone(UTC),
            start_uid=start_uid,
        )

    def get_alias(self, alias_id: int) -> AliasRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                '''
                SELECT id, address, account_address, created_at, expires_at, start_uid
                FROM aliases
                WHERE id = ?
                ''',
                (alias_id,),
            ).fetchone()
        return self._row_to_alias(row)

    def get_lowest_alias_start_uid(self, account_address: str) -> int | None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                '''
                SELECT MIN(start_uid) AS start_uid
                FROM aliases
                WHERE account_address = ? AND expires_at > ?
                ''',
                (account_address, now),
            ).fetchone()
        if row is None or row['start_uid'] is None:
            return None
        return int(row['start_uid'])

    def find_matching_alias(
        self,
        candidate_addresses: list[str],
        gmail_uid: int,
        account_address: str,
    ) -> AliasRecord | None:
        if not candidate_addresses:
            return None

        normalized_addresses = list(dict.fromkeys(address.lower() for address in candidate_addresses))
        now = datetime.now(UTC).isoformat()
        exact_match = self._find_exact_matching_alias(
            normalized_addresses,
            gmail_uid,
            account_address,
            now,
        )
        if exact_match is not None:
            return exact_match

        return self._find_unambiguous_canonical_matching_alias(
            normalized_addresses,
            gmail_uid,
            account_address,
            now,
        )

    def _find_exact_matching_alias(
        self,
        normalized_addresses: list[str],
        gmail_uid: int,
        account_address: str,
        now: str,
    ) -> AliasRecord | None:
        placeholders = ', '.join('?' for _ in normalized_addresses)
        query = f'''
            SELECT id, address, account_address, created_at, expires_at, start_uid
            FROM aliases
            WHERE lower(address) IN ({placeholders})
              AND account_address = ?
              AND start_uid < ?
              AND expires_at > ?
            ORDER BY id DESC
            LIMIT 1
        '''
        params = [*normalized_addresses, account_address, gmail_uid, now]
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
        return self._row_to_alias(row)

    def _find_unambiguous_canonical_matching_alias(
        self,
        normalized_addresses: list[str],
        gmail_uid: int,
        account_address: str,
        now: str,
    ) -> AliasRecord | None:
        candidate_identities = self._canonical_gmail_identities(
            normalized_addresses,
            account_address,
        )
        if not candidate_identities:
            return None

        with self.connect() as connection:
            rows = connection.execute(
                '''
                SELECT id, address, account_address, created_at, expires_at, start_uid
                FROM aliases
                WHERE account_address = ?
                  AND start_uid < ?
                  AND expires_at > ?
                ORDER BY id DESC
                ''',
                (account_address, gmail_uid, now),
            ).fetchall()

        matches = [
            alias
            for row in rows
            if (alias := self._row_to_alias(row)) is not None
            and normalize_gmail_alias_identity(alias.address) in candidate_identities
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def _canonical_gmail_identities(
        self,
        addresses: list[str],
        account_address: str,
    ) -> set[str]:
        identities: set[str] = set()
        for address in addresses:
            if address == account_address:
                continue
            try:
                identity = normalize_gmail_alias_identity(address)
                if identity == normalize_gmail_alias_identity(account_address):
                    continue
                identities.add(identity)
            except ValueError:
                continue
        return identities

    def _row_to_alias(self, row: sqlite3.Row | None) -> AliasRecord | None:
        if row is None:
            return None
        return AliasRecord(
            id=int(row['id']),
            address=str(row['address']),
            account_address=str(row['account_address']),
            created_at=datetime.fromisoformat(str(row['created_at'])).astimezone(UTC),
            expires_at=datetime.fromisoformat(str(row['expires_at'])).astimezone(UTC),
            start_uid=int(row['start_uid']),
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


    def delete_expired_aliases(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(UTC)).isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                'DELETE FROM aliases WHERE expires_at <= ?',
                (cutoff,),
            )
            connection.commit()
        return cursor.rowcount

    def delete_expired_mails(self, cutoff: datetime) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                'DELETE FROM mails WHERE received_at <= ?',
                (cutoff.isoformat(),),
            )
            connection.commit()
        return cursor.rowcount

    def get_last_seen_uid(self, account_address: str) -> int:
        state_key = f'last_seen_uid:{account_address}'
        with self.connect() as connection:
            row = connection.execute(
                'SELECT value FROM service_state WHERE key = ?',
                (state_key,),
            ).fetchone()
        return int(row['value']) if row else -1

    def set_last_seen_uid(self, account_address: str, uid: int) -> None:
        state_key = f'last_seen_uid:{account_address}'
        with self.connect() as connection:
            connection.execute(
                '''
                INSERT INTO service_state(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                ''',
                (state_key, str(uid)),
            )
            connection.commit()
