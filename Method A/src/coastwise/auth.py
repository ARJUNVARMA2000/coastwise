"""Password-protected shared workspace for the hosted capstone app.

Two optional passwords give two roles:
  COASTWISE_ACCESS_PASSWORD  -> "workspace": the team. Uploads may be saved to the shared library.
  COASTWISE_CLIENT_PASSWORD  -> "client": Mira. Every upload is incognito (visible only to the uploading
                                browser, never saved to the shared library); saved pages stay readable.
A client password can also be chosen by Mira through a one-time setup link (client_access.py); it opens
the same client role, and the team never sees it.
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

# Set by web.py: returns the client_access.AccessStore for the password Mira chose through a setup link.
client_store = lambda: None  # noqa: E731


def _payload_role(payload) -> str | None:
    """Session payloads: "workspace"; "client" or "client:env" (env password); "client:<version>" (chosen password).
    A client session ends when its password is removed or replaced."""
    if payload == "workspace":
        return "workspace"
    if payload in ("client", "client:env"):
        return "client" if os.getenv("COASTWISE_CLIENT_PASSWORD") else None
    if isinstance(payload, str) and payload.startswith("client:"):
        store = client_store()
        version = store.version() if store else None
        return "client" if version and hmac.compare_digest(payload[7:], version) else None
    return None


def session_role(cookie: str | None) -> str | None:
    """The signed-in role ("workspace" or "client"), or None."""
    if not cookie:
        return None
    try:
        payload = signer().loads(cookie, max_age=86400)
    except (BadSignature, SignatureExpired):
        return None
    return _payload_role(payload)


def valid_session(cookie: str | None) -> bool:
    return session_role(cookie) is not None


def password_payload(password: str) -> str | None:
    """Return the session payload a password opens, or None.
    Environment passwords are all compared so timing does not reveal which one matched."""
    found = None
    for payload, variable in (("workspace", "COASTWISE_ACCESS_PASSWORD"), ("client:env", "COASTWISE_CLIENT_PASSWORD")):
        expected = os.getenv(variable, "")
        if hmac.compare_digest(password.encode(), expected.encode()) and expected and found is None:
            found = payload
    if found is None and password and (store := client_store()):
        version = store.check(password)
        if version:
            found = "client:" + version
    return found


def password_role(password: str) -> str | None:
    return _payload_role(password_payload(password))


def valid_password(password: str) -> bool:
    return password_role(password) is not None


def private_only(request) -> bool:
    """Client sessions may only create incognito uploads and runs (only enforced when sign-in is required)."""
    return required() and session_role(request.cookies.get(COOKIE)) == "client"


def is_team(request) -> bool:
    """A team (workspace) session, or any local request when sign-in is not required."""
    return not required() or session_role(request.cookies.get(COOKIE)) == "workspace"


LOGIN_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in · Coastwise</title><link rel="stylesheet" href="/style.css"><body><main style="max-width:380px;margin:15vh auto;padding:25px"><span class="eyebrow">COASTWISE · PRIVATE WORKSPACE</span><h1>Welcome back.</h1><p class="intro">Enter the workspace password to access inspection reports.</p><form method="post" action="/login"><label class="field-label" for="password">Workspace password</label><input id="password" name="password" type="password" autocomplete="current-password" required autofocus><button class="primary-button" style="width:100%;margin-top:20px">Open workspace <span>↗</span></button></form>ERROR</main></body></html>"""
