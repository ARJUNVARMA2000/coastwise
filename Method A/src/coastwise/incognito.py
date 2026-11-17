"""Temporary, session-owned runs. Never writes to the shared report index."""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import secrets
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

COOKIE = "coastwise_incognito"
TTL = 3600


def owner_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_token(existing: str | None) -> str:
    return existing if existing and len(existing) == 43 else secrets.token_urlsafe(32)


@dataclass
class Entry:
    path: Path
    owner: str
    expires_at: float
    group: str
    busy: bool = False
    readers: int = 0
    deleted: bool = False


class PrivateRuns:
    def __init__(self, base: Path | None = None, on_remove=None):
        self.base = base or Path(tempfile.gettempdir()) / f"coastwise-incognito-{os.getuid()}"
        self.root = None
        self.lockfile = None
        self.entries: dict[str, Entry] = {}
        self.lock = threading.RLock()
        self.on_remove = on_remove or (lambda identity: None)

    def start(self):
        with self.lock:
            if self.root is not None:
                return
            self.base.mkdir(mode=0o700, parents=True, exist_ok=True)
            with (self.base / "startup.lock").open("a") as startup:
                fcntl.flock(startup, fcntl.LOCK_EX)
                self._start_locked()

    def _start_locked(self):
        # A live process holds its directory lock. Only reclaim crashed processes.
        for path in self.base.glob("session-*"):
            try:
                with (path / "owner.lock").open("a") as handle:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    shutil.rmtree(path)
            except (BlockingIOError, FileNotFoundError):
                continue
        self.root = Path(tempfile.mkdtemp(prefix="session-", dir=self.base))
        self.lockfile = (self.root / "owner.lock").open("w")
        fcntl.flock(self.lockfile, fcntl.LOCK_EX)

    def create(self, identity: str, token: str, *, busy=False, group: str | None = None) -> Path:
        with self.lock:
            self.start()
            path = self.root / identity
            path.mkdir(mode=0o700)
            self.entries[identity] = Entry(path, owner_digest(token), time.time() + TTL,
                                           group or identity, busy=busy)
            return path

    def acquire(self, identity: str, token: str | None):
        with self.lock:
            entry = self.entries.get(identity)
            if (not entry or entry.deleted or (not entry.busy and entry.expires_at <= time.time())
                    or not token or not hmac.compare_digest(entry.owner, owner_digest(token))):
                raise HTTPException(404, "Private run unavailable or expired")
            entry.readers += 1
            return entry

    def release(self, identity: str):
        with self.lock:
            if entry := self.entries.get(identity):
                entry.readers -= 1
                self._collect(identity, entry)

    def finish(self, identity: str):
        with self.lock:
            if entry := self.entries.get(identity):
                entry.busy = False
                entry.expires_at = time.time() + TTL
                self._collect(identity, entry)

    def delete(self, identity: str):
        with self.lock:
            if entry := self.entries.get(identity):
                entry.deleted = True
                self._collect(identity, entry)

    def delete_group(self, identity: str):
        with self.lock:
            group = self.entries[identity].group
            for key, entry in list(self.entries.items()):
                if entry.group == group:
                    entry.deleted = True
                    self._collect(key, entry)

    def _collect(self, identity: str, entry: Entry):
        if not entry.busy and not entry.readers and (entry.deleted or entry.expires_at <= time.time()):
            shutil.rmtree(entry.path)
            del self.entries[identity]
            self.on_remove(identity)

    def sweep(self):
        with self.lock:
            for identity, entry in list(self.entries.items()):
                self._collect(identity, entry)

    def close(self):
        with self.lock:
            for identity in list(self.entries):
                self.on_remove(identity)
            self.entries.clear()
            if self.root:
                shutil.rmtree(self.root)
                self.root = None
            if self.lockfile:
                self.lockfile.close()
                self.lockfile = None
