from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_check_returns_ok() -> None:
    response = client.get('/health_check')

    assert response.status_code == 200
    assert response.text == 'OK'
