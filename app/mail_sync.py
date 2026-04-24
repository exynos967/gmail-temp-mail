from __future__ import annotations

import imaplib
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from typing import Callable, Protocol
from urllib.parse import unquote, urlparse

import socks

from app.config import GmailAccount, Settings
from app.db import Database


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RemoteMail:
    uid: int
    raw: bytes


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    scheme: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    rdns: bool = True


class MailboxClient(Protocol):
    def get_max_uid(self) -> int: ...
    def fetch_messages_since(self, last_seen_uid: int) -> list[RemoteMail]: ...
    def close(self) -> None: ...


class ProxyAwareIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(
        self,
        host: str,
        port: int,
        *,
        proxy_config: ProxyConfig,
        timeout: float | None = None,
    ):
        self.proxy_config = proxy_config
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout):
        proxy_type = _get_socks_proxy_type(self.proxy_config.scheme)
        socket_connection = socks.create_connection(
            (self.host, self.port),
            timeout=timeout,
            proxy_type=proxy_type,
            proxy_addr=self.proxy_config.host,
            proxy_port=self.proxy_config.port,
            proxy_username=self.proxy_config.username,
            proxy_password=self.proxy_config.password,
            proxy_rdns=self.proxy_config.rdns,
        )
        return self.ssl_context.wrap_socket(
            socket_connection,
            server_hostname=self.host,
        )


class GmailImapClient:
    def __init__(self, account: GmailAccount, proxy_url: str = ''):
        self.account = account
        self.proxy_config = _parse_proxy_config(proxy_url)
        self._connection: imaplib.IMAP4_SSL | None = None

    def _ensure_connection(self) -> imaplib.IMAP4_SSL:
        if self._connection is not None:
            return self._connection

        connection = _create_imap_connection(
            'imap.gmail.com',
            993,
            self.proxy_config,
        )
        connection.login(self.account.address, self.account.app_password)
        status, _ = connection.select('INBOX')
        if status != 'OK':
            raise RuntimeError('Failed to select INBOX')
        self._connection = connection
        return connection

    def get_max_uid(self) -> int:
        connection = self._ensure_connection()
        status, data = connection.uid('search', None, 'ALL')
        if status != 'OK':
            raise RuntimeError('Failed to search mailbox UIDs')
        uids = _parse_uid_search_response(data)
        return max(uids, default=0)

    def fetch_messages_since(self, last_seen_uid: int) -> list[RemoteMail]:
        connection = self._ensure_connection()
        start_uid = max(last_seen_uid + 1, 1)
        status, data = connection.uid('search', None, 'UID', f'{start_uid}:*')
        if status != 'OK':
            raise RuntimeError('Failed to search mailbox UIDs')
        new_uids = _parse_uid_search_response(data)
        result: list[RemoteMail] = []
        for uid in new_uids:
            status, message_data = connection.uid('fetch', str(uid), '(RFC822)')
            if status != 'OK':
                raise RuntimeError(f'Failed to fetch mail uid={uid}')
            raw = _extract_raw_message(message_data)
            if raw:
                result.append(RemoteMail(uid=uid, raw=raw))
        return result

    def close(self) -> None:
        if self._connection is None:
            return
        try:
            self._connection.logout()
        except Exception:
            logger.debug('logout failed', exc_info=True)
        finally:
            self._connection = None




class NullMailSyncService:
    def __init__(self, database: Database):
        self.database = database

    def get_current_uid_baseline(self, account_address: str) -> int:
        return max(self.database.get_last_seen_uid(account_address), 0)

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class MailSyncService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        client_factory: Callable[[GmailAccount], MailboxClient] | None = None,
    ):
        self.settings = settings
        self.database = database
        self.client_factory = client_factory or (
            lambda account: GmailImapClient(
                account,
                settings.get_imap_proxy_url(),
            )
        )
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def get_current_uid_baseline(self, account_address: str) -> int:
        client = self.client_factory(self.settings.get_gmail_account(account_address))
        try:
            return client.get_max_uid()
        finally:
            client.close()

    def sync_once(self) -> int:
        self._run_cleanup()
        inserted = 0
        for account in self.settings.get_gmail_accounts():
            inserted += self._sync_account(account)
        return inserted

    def _sync_account(self, account: GmailAccount) -> int:
        client = self.client_factory(account)
        try:
            last_seen_uid = self.database.get_last_seen_uid(account.address)
            min_start_uid = self.database.get_lowest_alias_start_uid(account.address)
            if last_seen_uid < 0:
                if min_start_uid is None:
                    baseline_uid = client.get_max_uid()
                    self.database.set_last_seen_uid(account.address, baseline_uid)
                    return 0
                fetch_after_uid = min_start_uid - 1
            else:
                fetch_after_uid = last_seen_uid
                if min_start_uid is not None:
                    fetch_after_uid = min(fetch_after_uid, min_start_uid - 1)

            remote_mails = sorted(
                client.fetch_messages_since(fetch_after_uid),
                key=lambda item: item.uid,
            )
            inserted_count = 0
            max_seen_uid = max(last_seen_uid, fetch_after_uid)
            for remote_mail in remote_mails:
                max_seen_uid = max(max_seen_uid, remote_mail.uid)
                matched_alias = self.database.find_matching_alias(
                    _extract_candidate_addresses(remote_mail.raw),
                    remote_mail.uid,
                    account.address,
                )
                if matched_alias is None:
                    continue
                try:
                    self.database.create_mail(
                        alias_id=matched_alias.id,
                        address=matched_alias.address,
                        source=_extract_source_address(remote_mail.raw),
                        message_id=_extract_message_id(remote_mail.raw, remote_mail.uid),
                        raw=remote_mail.raw.decode('latin-1'),
                        gmail_uid=remote_mail.uid,
                    )
                    inserted_count += 1
                except sqlite3.IntegrityError:
                    logger.debug('skip duplicate mail uid=%s alias_id=%s', remote_mail.uid, matched_alias.id)
            if max_seen_uid != last_seen_uid:
                self.database.set_last_seen_uid(account.address, max_seen_uid)
            return inserted_count
        finally:
            client.close()


    def _run_cleanup(self) -> None:
        now = datetime.now(UTC)
        self.database.delete_expired_aliases(now)
        self.database.delete_expired_mails(now - timedelta(minutes=self.settings.mail_ttl_minutes))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        try:
            accounts = self.settings.get_gmail_accounts()
            _parse_proxy_config(self.settings.get_imap_proxy_url())
        except ValueError:
            logger.exception('mail sync disabled because Gmail or proxy settings are invalid')
            return
        if not accounts:
            logger.info('mail sync disabled because Gmail credentials are not configured')
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name='gmail-mail-sync',
            daemon=True,
        )
        self._thread.start()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.sync_once()
            except Exception:
                logger.exception('mail sync loop failed')
            self._stop_event.wait(self.settings.poll_interval_seconds)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)



def _parse_uid_search_response(data: list[bytes | bytearray | None]) -> list[int]:
    if not data or data[0] is None:
        return []
    if isinstance(data[0], bytearray):
        raw = bytes(data[0])
    else:
        raw = data[0]
    return [int(item) for item in raw.decode().split() if item.strip()]


def _create_imap_connection(
    host: str,
    port: int,
    proxy_config: ProxyConfig | None,
) -> imaplib.IMAP4_SSL:
    if proxy_config is None:
        return imaplib.IMAP4_SSL(host, port)
    return ProxyAwareIMAP4SSL(host, port, proxy_config=proxy_config)


def _parse_proxy_config(proxy_url: str) -> ProxyConfig | None:
    raw_proxy_url = proxy_url.strip()
    if not raw_proxy_url:
        return None

    parsed = urlparse(raw_proxy_url)
    scheme = parsed.scheme.lower()
    if scheme not in {'http', 'socks4', 'socks4a', 'socks5', 'socks5h'}:
        raise ValueError('Unsupported IMAP proxy scheme')
    if not parsed.hostname:
        raise ValueError('Invalid IMAP proxy URL')

    default_port = 8080 if scheme == 'http' else 1080
    return ProxyConfig(
        scheme=scheme,
        host=parsed.hostname,
        port=parsed.port or default_port,
        username=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
        rdns=scheme not in {'socks5', 'socks4'},
    )


def _get_socks_proxy_type(scheme: str) -> int:
    if scheme in {'socks5', 'socks5h'}:
        return socks.SOCKS5
    if scheme in {'socks4', 'socks4a'}:
        return socks.SOCKS4
    return socks.HTTP



def _extract_raw_message(message_data: list[object]) -> bytes:
    for item in message_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return b''



def _parse_message(raw_message: bytes):
    return BytesParser(policy=policy.default).parsebytes(raw_message)



def _extract_candidate_addresses(raw_message: bytes) -> list[str]:
    message = _parse_message(raw_message)
    header_values: list[str] = []
    for header_name in ('Delivered-To', 'X-Original-To', 'To', 'Cc', 'Resent-To'):
        header_values.extend(message.get_all(header_name, []))
    addresses = [
        email_address.strip().lower()
        for _, email_address in getaddresses(header_values)
        if email_address and '@' in email_address
    ]
    return list(dict.fromkeys(addresses))



def _extract_source_address(raw_message: bytes) -> str:
    message = _parse_message(raw_message)
    addresses = getaddresses(message.get_all('From', []))
    if not addresses:
        return ''
    display_name, email_address = addresses[0]
    if display_name:
        return f'{display_name} <{email_address}>'
    return email_address



def _extract_message_id(raw_message: bytes, uid: int) -> str:
    message = _parse_message(raw_message)
    message_id = message.get('Message-ID')
    if message_id:
        return str(message_id)
    return f'<gmail-temp-mail-{uid}@local>'
