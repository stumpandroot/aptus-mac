import pytest


@pytest.fixture(autouse=True)
def _allow_test_client(monkeypatch):
    """FastAPI's TestClient sends Host: testserver, which the local-only guard would refuse."""
    try:
        import app as aptus_app
    except Exception:
        return
    monkeypatch.setattr(aptus_app, "ALLOWED_HOSTS", aptus_app.ALLOWED_HOSTS | {"testserver"})
