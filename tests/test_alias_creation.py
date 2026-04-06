from datetime import UTC, datetime
from pathlib import Path

import jwt
from fastapi.testclient import TestClient

from app.aliasing import generate_random_gmail_alias, normalize_gmail_address
from app.config import Settings
from app.main import create_app


API_KEY = 'service-secret'
JWT_SECRET = 'jwt-secret'
BASE_GMAIL = 'Abc.Def@gmail.com'


def build_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv('GMAIL_ACCOUNTS_1', BASE_GMAIL)
    monkeypatch.setenv('GMAIL_APP_PASSWORD_1', 'pass-one')
    settings = Settings(
        service_api_key=API_KEY,
        jwt_secret=JWT_SECRET,
        database_path=str(tmp_path / 'gmail_temp_mail.db'),
        alias_ttl_minutes=60,
    )
    return TestClient(create_app(settings))


def test_generate_random_gmail_alias_keeps_same_identity() -> None:
    alias = generate_random_gmail_alias(BASE_GMAIL)

    assert alias.split('@')[1] in {'gmail.com', 'googlemail.com'}
    assert normalize_gmail_address(alias) == 'abcdef@gmail.com'


def test_settings_parse_numbered_gmail_account_pool(monkeypatch) -> None:
    monkeypatch.setenv('GMAIL_ACCOUNTS_1', 'alpha.one@gmail.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD_1', 'pass one')
    monkeypatch.setenv('GMAIL_ACCOUNTS_2', 'beta.two@gmail.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD_2', 'pass-two')

    settings = Settings(
        service_api_key=API_KEY,
        jwt_secret=JWT_SECRET,
    )

    accounts = settings.get_gmail_accounts()

    assert [account.address for account in accounts] == [
        'alphaone@gmail.com',
        'betatwo@gmail.com',
    ]
    assert [account.app_password for account in accounts] == ['passone', 'pass-two']


def test_settings_no_longer_supports_single_account_fields() -> None:
    settings = Settings(
        service_api_key=API_KEY,
        jwt_secret=JWT_SECRET,
        gmail_address='alpha.one@gmail.com',
        gmail_app_password='pass-one',
    )

    assert settings.get_gmail_accounts() == []


def test_new_address_requires_service_api_key(tmp_path: Path, monkeypatch) -> None:
    client = build_client(tmp_path, monkeypatch)

    response = client.post('/api/new_address')

    assert response.status_code == 401
    assert response.json() == {'detail': 'Unauthorized'}


def test_new_address_returns_alias_and_jwt(tmp_path: Path, monkeypatch) -> None:
    client = build_client(tmp_path, monkeypatch)

    response = client.post('/api/new_address', headers={'x-custom-auth': API_KEY})

    assert response.status_code == 200
    payload = response.json()
    assert payload['address_id'] == 1
    assert normalize_gmail_address(payload['address']) == 'abcdef@gmail.com'
    assert payload['created_at'] < payload['expires_at']

    decoded = jwt.decode(payload['jwt'], JWT_SECRET, algorithms=['HS256'])
    assert decoded['address'] == payload['address']
    assert decoded['address_id'] == payload['address_id']
    assert decoded['exp'] > int(datetime.now(UTC).timestamp())


def test_new_address_uses_selected_account_from_pool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('GMAIL_ACCOUNTS_1', 'alpha.one@gmail.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD_1', 'pass-one')
    monkeypatch.setenv('GMAIL_ACCOUNTS_2', 'beta.two@gmail.com')
    monkeypatch.setenv('GMAIL_APP_PASSWORD_2', 'pass-two')
    settings = Settings(
        service_api_key=API_KEY,
        jwt_secret=JWT_SECRET,
        database_path=str(tmp_path / 'pool.db'),
    )
    client = TestClient(create_app(settings))
    selected_account = settings.get_gmail_accounts()[1]
    monkeypatch.setattr('app.main.select_random_gmail_account', lambda settings: selected_account)

    response = client.post('/api/new_address', headers={'x-custom-auth': API_KEY})

    assert response.status_code == 200
    payload = response.json()
    assert normalize_gmail_address(payload['address']) == 'betatwo@gmail.com'
    stored_alias = client.app.state.database.get_alias(payload['address_id'])
    assert stored_alias is not None
    assert stored_alias.account_address == 'betatwo@gmail.com'


def test_new_address_rejects_invalid_gmail_config(tmp_path: Path) -> None:
    settings = Settings(
        service_api_key=API_KEY,
        jwt_secret=JWT_SECRET,
        database_path=str(tmp_path / 'invalid.db'),
    )
    client = TestClient(create_app(settings), raise_server_exceptions=False)

    response = client.post('/api/new_address', headers={'x-custom-auth': API_KEY})

    assert response.status_code == 500
    assert response.json() == {'detail': 'Gmail address is not configured'}
