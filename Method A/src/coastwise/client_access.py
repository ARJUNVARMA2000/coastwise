"""Client (Mira) password chosen by the client through a one-time setup link.

The team creates a setup link from a team session; the client opens it and chooses a password.
Only a salted scrypt hash is stored, so the team never learns the password. A new link replaces any
unused one; using a link replaces the stored password and ends every earlier client session.

State lives in one small JSON file on the persistent volume, next to (not inside) the runs directory,
because the release installer manages the runs directory. Single-process hosting only, like incognito.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

LOG = logging.getLogger("coastwise.client_access")
LINK_SECONDS = 24 * 3600
MIN_LENGTH = 12
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
_LOCK = threading.Lock()


class AccessStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    # ------------------------------------------------------------------ file
    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write(self, state: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        state = self._read()
        link = state.get("link") or {}
        pending = bool(link) and link.get("expires_at", 0) > time.time()
        return {"password_set": bool(state.get("hash")), "password_set_at": state.get("set_at"),
                "link_pending": pending, "link_expires_at": link.get("expires_at") if pending else None}

    def version(self) -> str | None:
        return self._read().get("version")

    # ------------------------------------------------------------------ setup links
    def create_link(self) -> str:
        token = secrets.token_urlsafe(32)
        with _LOCK:
            state = self._read()
            state["link"] = {"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                             "created_at": time.time(), "expires_at": time.time() + LINK_SECONDS}
            self._write(state)
        LOG.warning("Client setup link created (expires in 24 h); any earlier unused link is void")
        return token

    def link_valid(self, token: str) -> bool:
        link = self._read().get("link") or {}
        digest = hashlib.sha256((token or "").encode()).hexdigest()
        return (bool(link) and link.get("expires_at", 0) > time.time()
                and hmac.compare_digest(digest, link.get("token_sha256", "")))

    def use_link(self, token: str, password: str) -> str:
        """Set the password with a valid link. Returns the new session version."""
        with _LOCK:
            if not self.link_valid(token):
                raise PermissionError("This setup link is invalid, used or expired")
            state = self._read()
            state.pop("link", None)
            version = self._set(state, password)
        LOG.warning("Client password set through a setup link; earlier client sessions ended")
        return version

    # ------------------------------------------------------------------ password
    def _set(self, state: dict, password: str) -> str:
        salt = secrets.token_bytes(16)
        state.update(hash=hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT).hex(), salt=salt.hex(),
                     set_at=time.time(), version=secrets.token_hex(8))
        self._write(state)
        return state["version"]

    def check(self, password: str) -> str | None:
        """Return the session version if the password matches the stored one."""
        state = self._read()
        if not state.get("hash"):
            return None
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(state["salt"]), **_SCRYPT).hex()
        return state["version"] if hmac.compare_digest(digest, state["hash"]) else None

    def change(self, current: str, new: str) -> str:
        with _LOCK:
            if self.check(current) is None:
                raise PermissionError("The current password is incorrect")
            version = self._set(self._read(), new)
        LOG.warning("Client password changed by the client; earlier client sessions ended")
        return version


def password_problem(password: str, confirm: str) -> str | None:
    if password != confirm:
        return "The two passwords do not match."
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    workspace = os.getenv("COASTWISE_ACCESS_PASSWORD", "")
    if workspace and hmac.compare_digest(password.encode(), workspace.encode()):
        return "Choose a password different from the team password."
    return None
