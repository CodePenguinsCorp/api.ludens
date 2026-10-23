"""Run explicitly: python tests/smoke_demo.py https://YOUR-TUNNEL (uses fake data)."""
import secrets
import sys
from uuid import uuid4

import httpx


def main(base_url: str) -> None:
    digits = [secrets.randbelow(10) for _ in range(9)]
    for weights in (range(10, 1, -1), range(11, 1, -1)):
        value = sum(a * b for a, b in zip(digits, weights)) * 10 % 11
        digits.append(0 if value == 10 else value)
    email = f"smoke-{uuid4().hex}@example.com"
    password = uuid4().hex
    auth = "/api/identity/authentication"
    with httpx.Client(base_url=base_url, timeout=30, follow_redirects=False) as client:
        response = client.post("/api/identity/users", json={
            "name": "Demo diagnostic", "cpf": "".join(map(str, digits)),
            "email": email, "password": password,
        })
        assert response.status_code == 201, f"register: {response.status_code}"
        response = client.post(f"{auth}/login", json={"email": email, "password": password})
        assert response.status_code == 200, f"login: {response.status_code}"
        cookie = response.headers.get("set-cookie", "").lower()
        assert "httponly" in cookie and "secure" in cookie
        assert "path=/api/identity/authentication" in cookie
        token = response.json()["access_token"]
        # Explicit Cookie permits testing this HTTPS-only cookie via local HTTP proxy too.
        refresh = client.cookies.get("refresh_token")
        response = client.post(f"{auth}/refresh", headers={"Cookie": f"refresh_token={refresh}"})
        assert response.status_code == 200, f"refresh: {response.status_code}"
        token = response.json()["access_token"]
        response = client.post(f"{auth}/logout", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 204, f"logout: {response.status_code}"
        assert "max-age=0" in response.headers.get("set-cookie", "").lower()
        assert client.get("/api/catalog/genres").status_code == 200
        response = client.post("/api/catalog/sessions/", json={})
        assert response.status_code in (401, 403, 422), f"session redirect: {response.status_code}"
        assert "location" not in response.headers
    print("PASS: registration, login, Secure/HttpOnly cookie, refresh, logout, catalogue and trailing slash")


if __name__ == "__main__":
    main(sys.argv[1])
