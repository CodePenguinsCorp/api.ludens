from fastapi import Response

from app.config import settings

REFRESH_COOKIE = "refresh_token"
REFRESH_PATH = "/api/identity/authentication"

def set_refresh_cookie(response: Response, raw_refresh: str) -> None:
    # api.ludens (azurewebsites.net) and web.ludens (vercel.app) are different
    # eTLD+1 domains — SameSite=Strict/Lax never sends the cookie on a
    # cross-site request, silently breaking refresh from the real frontend.
    # SameSite=None requires Secure, which local dev (plain HTTP) can't set —
    # browsers reject the cookie outright if None ever ships without Secure,
    # so dev keeps Lax (same-site there anyway: SameSite ignores port).
    secure = settings.refresh_cookie_secure
    if secure is None:
        secure = settings.environment != "development"
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=raw_refresh,
        httponly=True,
        secure=secure,
        samesite="none" if secure else "lax",
        path=REFRESH_PATH,
        max_age=settings.refresh_token_expire_days * 24 * 3600,
    )
