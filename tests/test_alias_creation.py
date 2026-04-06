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


def build_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        service_api_key=API_KEY,
        jwt_secret=JWT_SECRET,
        gmail_address=BASE_GMAIL,
        database_path=str(tmp_path / 'gmail_temp_mail.db'),
        alias_ttl_minutes=60,
    )
    return TestClient(create_app(settings))


def test_generate_random_gmail_alias_keeps_same_identity() -> None:
    alias = generate_random_gmail_alias(BASE_GMAIL)

    assert alias.split('@')[1] in {'gmail.com', 'googlemail.com'}
    assert normalize_gmail_address(alias) == 'abcdef@gmail.com'


def test_new_address_requires_service_api_key(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    response = client.post('/api/new_address')

    assert response.status_code == 401
    assert response.json() == {'detail': 'Unauthorized'}


def test_new_address_returns_alias_and_jwt(tmp_path: Path) -> None:
    client = build_client(tmp_path)

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


def test_new_address_rejects_invalid_gmail_config(tmp_path: Path) -> None:
    settings = Settings(
        service_api_key=API_KEY,
        jwt_secret=JWT_SECRET,
        gmail_address='your_account@gmail.com',
        database_path=str(tmp_path / 'invalid.db'),
    )
    client = TestClient(create_app(settings), raise_server_exceptions=False)

    response = client.post('/api/new_address', headers={'x-custom-auth': API_KEY})

    assert response.status_code == 500
    assert response.json() == {'detail': 'Gmail address is invalid'}
