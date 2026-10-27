"""Password-protected shared workspace for the hosted capstone app."""

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


def signer():
    secret = os.getenv("COASTWISE_SESSION_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("Hosted session secret is not configured")
    return URLSafeTimedSerializer(secret, salt="coastwise-workspace-v1")


def valid_session(cookie: str | None) -> bool:
    if not cookie:
        return False
    try:
        return signer().loads(cookie, max_age=86400) == "workspace"
    except (BadSignature, SignatureExpired):
        return False


def valid_password(password: str) -> bool:
    expected = os.getenv("COASTWISE_ACCESS_PASSWORD", "")
    return bool(expected) and hmac.compare_digest(password, expected)


LOGIN_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in · Coastwise</title><link rel="stylesheet" href="/style.css"><body><main style="max-width:380px;margin:15vh auto;padding:25px"><span class="eyebrow">COASTWISE · PRIVATE WORKSPACE</span><h1>Welcome back.</h1><p class="intro">Enter the workspace password to access inspection reports.</p><form method="post" action="/login"><label class="field-label" for="password">Workspace password</label><input id="password" name="password" type="password" autocomplete="current-password" required autofocus><button class="primary-button" style="width:100%;margin-top:20px">Open workspace <span>↗</span></button></form>ERROR</main></body></html>"""
