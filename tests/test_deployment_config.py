from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / '.github' / 'workflows' / 'docker-publish.yml'
COMPOSE_PATH = REPO_ROOT / 'docker-compose.yml'
README_PATH = REPO_ROOT / 'README.md'
EXPECTED_IMAGE = 'ghcr.io/exynos967/gmail-temp-mail:latest'



def test_compose_defaults_to_ghcr_image() -> None:
    compose_text = COMPOSE_PATH.read_text()

    assert f'image: {EXPECTED_IMAGE}' in compose_text
    assert 'build:' not in compose_text



def test_github_actions_publish_workflow_targets_ghcr() -> None:
    workflow_text = WORKFLOW_PATH.read_text()

    assert 'docker/login-action' in workflow_text
    assert 'docker/build-push-action' in workflow_text
    assert 'registry: ghcr.io' in workflow_text
    assert 'packages: write' in workflow_text
    assert 'ghcr.io/exynos967/gmail-temp-mail' in workflow_text



def test_readme_documents_ghcr_image_usage() -> None:
    readme_text = README_PATH.read_text()

    assert EXPECTED_IMAGE in readme_text
    assert 'docker compose pull' in readme_text
