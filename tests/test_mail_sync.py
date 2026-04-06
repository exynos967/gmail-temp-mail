from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import GmailAccount, Settings
from app.db import Database
from app.mail_sync import MailSyncService, RemoteMail
from app.main import create_app


API_KEY = 'service-secret'
JWT_SECRET = 'jwt-secret'
PRIMARY_ACCOUNT = GmailAccount(address='primaryone@gmail.com', app_password='pass-one')
SECONDARY_ACCOUNT = GmailAccount(address='secondarytwo@gmail.com', app_password='pass-two')


class FakeImapClient:
    def __init__(self, mailbox: list[RemoteMail]):
        self.mailbox = mailbox

    def get_max_uid(self) -> int:
        return max((mail.uid for mail in self.mailbox), default=0)

    def fetch_messages_since(self, last_seen_uid: int) -> list[RemoteMail]:
        return [mail for mail in self.mailbox if mail.uid > last_seen_uid]

    def close(self) -> None:
        return None



def build_service(
    tmp_path: Path,
    mailbox_by_account: dict[str, list[RemoteMail]],
    *,
    settings: Settings | None = None,
) -> MailSyncService:
    resolved_settings = settings or Settings(
        service_api_key=API_KEY,
        jwt_secret=JWT_SECRET,
        database_path=str(tmp_path / 'gmail_temp_mail.db'),
        alias_ttl_minutes=60,
        mail_ttl_minutes=60,
    )
    database = Database(resolved_settings.database_path)
    database.initialize()
    return MailSyncService(
        settings=resolved_settings,
        database=database,
        client_factory=lambda account: FakeImapClient(mailbox_by_account[account.address]),
    )



def build_raw_mail(*, to_address: str, subject: str, message_id: str) -> bytes:
    return (
        f'From: sender@example.com\r\n'
        f'To: {to_address}\r\n'
        f'Message-ID: {message_id}\r\n'
        f'Subject: {subject}\r\n'
        '\r\n'
        'body'
    ).encode('utf-8')



def test_first_sync_initializes_uid_baseline_without_importing_old_mail(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('GMAIL_ACCOUNTS_1', 'primary.one@gmail.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD_1', 'pass-one')
    monkeypatch.setenv('GMAIL_ACCOUNTS_2', 'secondary.two@gmail.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD_2', 'pass-two')
    mailbox_by_account = {
        PRIMARY_ACCOUNT.address: [
            RemoteMail(uid=1, raw=build_raw_mail(to_address='nobody@gmail.com', subject='old', message_id='<old@example.com>')),
        ],
        SECONDARY_ACCOUNT.address: [
            RemoteMail(uid=7, raw=build_raw_mail(to_address='none@gmail.com', subject='old2', message_id='<old2@example.com>')),
        ],
    }
    service = build_service(tmp_path, mailbox_by_account)

    inserted = service.sync_once()

    assert inserted == 0
    assert service.database.get_last_seen_uid(PRIMARY_ACCOUNT.address) == 1
    assert service.database.get_last_seen_uid(SECONDARY_ACCOUNT.address) == 7



def test_new_address_uses_live_mailbox_uid_as_start_boundary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('GMAIL_ACCOUNTS_1', 'primary.one@gmail.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD_1', 'pass-one')
    monkeypatch.setenv('GMAIL_ACCOUNTS_2', 'secondary.two@gmail.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD_2', 'pass-two')
    mailbox_by_account = {
        PRIMARY_ACCOUNT.address: [
            RemoteMail(uid=5, raw=build_raw_mail(to_address='someone@gmail.com', subject='existing', message_id='<existing@example.com>')),
        ],
        SECONDARY_ACCOUNT.address: [],
    }
    settings = Settings(
        service_api_key=API_KEY,
        jwt_secret=JWT_SECRET,
        database_path=str(tmp_path / 'gmail_temp_mail.db'),
        alias_ttl_minutes=60,
        mail_ttl_minutes=60,
    )
    service = build_service(tmp_path, mailbox_by_account, settings=settings)
    client = TestClient(create_app(service.settings, mail_sync_service=service))

    response = client.post('/api/new_address', headers={'x-custom-auth': API_KEY})

    assert response.status_code == 200
    payload = response.json()
    stored_alias = service.database.get_alias(payload['address_id'])
    assert stored_alias is not None
    baseline = service.get_current_uid_baseline(stored_alias.account_address)
    assert stored_alias.start_uid == baseline



def test_sync_imports_only_mail_from_same_account_after_alias_start_uid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('GMAIL_ACCOUNTS_1', 'primary.one@gmail.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD_1', 'pass-one')
    monkeypatch.setenv('GMAIL_ACCOUNTS_2', 'secondary.two@gmail.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD_2', 'pass-two')
    mailbox_by_account = {
        PRIMARY_ACCOUNT.address: [],
        SECONDARY_ACCOUNT.address: [],
    }
    service = build_service(tmp_path, mailbox_by_account)
    now = datetime.now(UTC)
    alias = service.database.create_alias(
        address='alias.one@gmail.com',
        account_address=PRIMARY_ACCOUNT.address,
        created_at=now,
        expires_at=now + timedelta(minutes=60),
        start_uid=10,
    )
    service.database.set_last_seen_uid(PRIMARY_ACCOUNT.address, 10)
    service.database.set_last_seen_uid(SECONDARY_ACCOUNT.address, 20)
    mailbox_by_account[PRIMARY_ACCOUNT.address].extend([
        RemoteMail(uid=10, raw=build_raw_mail(to_address=alias.address, subject='old', message_id='<old@example.com>')),
        RemoteMail(uid=11, raw=build_raw_mail(to_address=alias.address, subject='fresh', message_id='<fresh@example.com>')),
    ])
    mailbox_by_account[SECONDARY_ACCOUNT.address].append(
        RemoteMail(uid=21, raw=build_raw_mail(to_address=alias.address, subject='wrong-account', message_id='<wrong@example.com>')),
    )

    inserted = service.sync_once()

    assert inserted == 1
    assert service.database.count_mails(alias.address) == 1
    stored_mail = service.database.list_mails(alias.address, limit=10, offset=0)[0]
    assert 'Subject: fresh' in stored_mail['raw']

    service.database.set_last_seen_uid(PRIMARY_ACCOUNT.address, 10)
    inserted_again = service.sync_once()

    assert inserted_again == 0
    assert service.database.count_mails(alias.address) == 1



def test_sync_once_cleans_expired_aliases_and_old_mail(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('GMAIL_ACCOUNTS_1', 'primary.one@gmail.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD_1', 'pass-one')
    monkeypatch.setenv('GMAIL_ACCOUNTS_2', 'secondary.two@gmail.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD_2', 'pass-two')
    mailbox_by_account = {
        PRIMARY_ACCOUNT.address: [],
        SECONDARY_ACCOUNT.address: [],
    }
    service = build_service(tmp_path, mailbox_by_account)
    now = datetime.now(UTC)
    expired_at = now - timedelta(minutes=5)
    old_received_at = now - timedelta(minutes=120)
    expired_alias = service.database.create_alias(
        address='expired.alias@gmail.com',
        account_address=PRIMARY_ACCOUNT.address,
        created_at=old_received_at,
        expires_at=expired_at,
        start_uid=0,
    )
    active_alias = service.database.create_alias(
        address='active.alias@gmail.com',
        account_address=SECONDARY_ACCOUNT.address,
        created_at=now,
        expires_at=now + timedelta(minutes=60),
        start_uid=0,
    )
    service.database.create_mail(
        alias_id=expired_alias.id,
        address=expired_alias.address,
        source='sender@example.com',
        message_id='<expired-mail@example.com>',
        raw='expired raw',
        received_at=old_received_at,
    )
    service.database.create_mail(
        alias_id=active_alias.id,
        address=active_alias.address,
        source='sender@example.com',
        message_id='<active-mail@example.com>',
        raw='active raw',
        received_at=now,
    )
    service.database.set_last_seen_uid(PRIMARY_ACCOUNT.address, 0)
    service.database.set_last_seen_uid(SECONDARY_ACCOUNT.address, 0)

    inserted = service.sync_once()

    assert inserted == 0
    assert service.database.get_alias(expired_alias.id) is None
    assert service.database.count_mails(expired_alias.address) == 0
    assert service.database.count_mails(active_alias.address) == 1
