from http.cookies import SimpleCookie

import pytest
from fastapi import Response

from app.config import Settings
from app.modules.identity.shared import cookies


@pytest.mark.parametrize("environment,override,secure,samesite", [
    ("development", None, False, "lax"),
    ("development", True, True, "none"),
    ("production", None, True, "none"),
    ("production", False, False, "lax"),
])
def test_refresh_cookie_security_is_independent_of_database(
    monkeypatch, environment, override, secure, samesite
):
    config = Settings(
        _env_file=None,
        environment=environment,
        database_url="postgresql+asyncpg://demo:demo@postgres/demo",
        jwt_secret_key="test-secret",
        refresh_cookie_secure=override,
    )
    monkeypatch.setattr(cookies, "settings", config)
    response = Response()
    cookies.set_refresh_cookie(response, "refresh-value")
    parsed = SimpleCookie(response.headers["set-cookie"])["refresh_token"]
    assert bool(parsed["secure"]) is secure
    assert parsed["samesite"] == samesite
    assert parsed["httponly"]
    assert parsed["path"] == "/api/identity/authentication"
    if environment == "development":
        assert config.db_connect_args == {}
