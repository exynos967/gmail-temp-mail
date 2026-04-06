from pathlib import Path

from fastapi.testclient import TestClient

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


def create_alias(client: TestClient) -> dict[str, str]:
    response = client.post('/api/new_address', headers={'x-custom-auth': API_KEY})
    assert response.status_code == 200
    return response.json()


def seed_mail(client: TestClient, alias: dict[str, str], raw: str) -> dict[str, str]:
    return client.app.state.database.create_mail(
        alias_id=alias['address_id'],
        address=alias['address'],
        source='sender@example.com',
        message_id=f"<{alias['address_id']}-{abs(hash(raw))}@example.com>",
        raw=raw,
    )


def test_list_mails_requires_bearer_token(tmp_path: Path, monkeypatch) -> None:
    client = build_client(tmp_path, monkeypatch)

    response = client.get('/api/mails?limit=10&offset=0')

    assert response.status_code == 401



def test_mail_endpoints_only_return_current_alias_records(tmp_path: Path, monkeypatch) -> None:
    client = build_client(tmp_path, monkeypatch)
    first_alias = create_alias(client)
    second_alias = create_alias(client)
    seed_mail(client, first_alias, 'Subject: First\n\nalpha')
    seed_mail(client, first_alias, 'Subject: Second\n\nbeta')
    other_mail = seed_mail(client, second_alias, 'Subject: Third\n\ngamma')

    response = client.get(
        '/api/mails?limit=10&offset=0',
        headers={'Authorization': f"Bearer {first_alias['jwt']}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload['count'] == 2
    assert len(payload['results']) == 2
    assert {item['address'] for item in payload['results']} == {first_alias['address']}

    other_response = client.get(
        f"/api/mail/{other_mail['id']}",
        headers={'Authorization': f"Bearer {first_alias['jwt']}"},
    )
    assert other_response.status_code == 200
    assert other_response.json() is None



def test_delete_mail_removes_current_alias_message(tmp_path: Path, monkeypatch) -> None:
    client = build_client(tmp_path, monkeypatch)
    alias = create_alias(client)
    mail = seed_mail(client, alias, 'Subject: Delete me\n\nbody')

    delete_response = client.delete(
        f"/api/mails/{mail['id']}",
        headers={'Authorization': f"Bearer {alias['jwt']}"},
    )

    assert delete_response.status_code == 200
    assert delete_response.json() == {'success': True}

    list_response = client.get(
        '/api/mails?limit=10&offset=0',
        headers={'Authorization': f"Bearer {alias['jwt']}"},
    )
    assert list_response.status_code == 200
    assert list_response.json() == {'results': [], 'count': 0}
