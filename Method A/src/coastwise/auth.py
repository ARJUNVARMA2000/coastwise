"""Password-protected shared workspace for the hosted capstone app.

Two optional passwords give two roles:
  COASTWISE_ACCESS_PASSWORD  -> "workspace": the team. Uploads may be saved to the shared library.
  COASTWISE_CLIENT_PASSWORD  -> "client": Mira. Every upload is incognito (visible only to the uploading
                                browser, never saved to the shared library); saved pages stay readable.
"""

import hmac
import os

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE = "coastwise_session"


def required() -> bool:
    return os.getenv("COASTWISE_AUTH_REQUIRED") == "1"


def hosted() -> bool:
    return bool(os.getenv("K_SERVICE") or os.getenv("RAILWAY_SERVICE_ID") or os.getenv("COASTWISE_PUBLIC_URL"))


def forwarded_allow_ips() -> str:
    return os.getenv("COASTWISE_FORWARDED_ALLOW_IPS", "*" if hosted() else "127.0.0.1")


def validate_hosted_configuration():
    if hosted() and os.getenv("COASTWISE_ROLE") != "worker":
        if not required() or not os.getenv("COASTWISE_ACCESS_PASSWORD"):
            raise RuntimeError("Hosted workspace authentication must be configured before serving reports")
        signer()
        client = os.getenv("COASTWISE_CLIENT_PASSWORD", "")
        if client and hmac.compare_digest(client, os.getenv("COASTWISE_ACCESS_PASSWORD", "")):
            raise RuntimeError("The client password must differ from the workspace password")


def signer():
    secret = os.getenv("COASTWISE_SESSION_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("Hosted session secret is not configured")
    return URLSafeTimedSerializer(secret, salt="coastwise-workspace-v1")


ROLES = ("workspace", "client")


def session_role(cookie: str | None) -> str | None:
    """The signed-in role, or None. A client session stops working once the client password is removed."""
    if not cookie:
        return None
    try:
        role = signer().loads(cookie, max_age=86400)
    except (BadSignature, SignatureExpired):
        return None
    if role == "client" and not os.getenv("COASTWISE_CLIENT_PASSWORD"):
        return None
    return role if role in ROLES else None


def valid_session(cookie: str | None) -> bool:
    return session_role(cookie) is not None


def password_role(password: str) -> str | None:
    """Return the role a password opens. Both are compared so timing does not reveal which one matched."""
    found = None
    for role, variable in (("workspace", "COASTWISE_ACCESS_PASSWORD"), ("client", "COASTWISE_CLIENT_PASSWORD")):
        expected = os.getenv(variable, "")
        if hmac.compare_digest(password.encode(), expected.encode()) and expected and found is None:
            found = role
    return found


def valid_password(password: str) -> bool:
    return password_role(password) is not None


def private_only(request) -> bool:
    """Client sessions may only create incognito uploads and runs (only enforced when sign-in is required)."""
    return required() and session_role(request.cookies.get(COOKIE)) == "client"


LOGIN_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in · Coastwise</title><link rel="stylesheet" href="/style.css"><body><main style="max-width:380px;margin:15vh auto;padding:25px"><span class="eyebrow">COASTWISE · PRIVATE WORKSPACE</span><h1>Welcome back.</h1><p class="intro">Enter the workspace password to access inspection reports.</p><form method="post" action="/login"><label class="field-label" for="password">Workspace password</label><input id="password" name="password" type="password" autocomplete="current-password" required autofocus><button class="primary-button" style="width:100%;margin-top:20px">Open workspace <span>↗</span></button></form>ERROR</main></body></html>"""
