"""Local configuration. A filled .env never overrides deployed runtime settings."""

import os
from pathlib import Path

from dotenv import load_dotenv


def load_environment(path: str | Path = ".env") -> None:
    load_dotenv(path, override=False)


def server_key() -> str:
    # Refresh when the user fills the initially empty local .env; process env wins.
    from dotenv import dotenv_values

    explicit = os.getenv("OPENROUTER_API_KEY", "").strip()
    if explicit:
        return explicit
    if not os.getenv("K_SERVICE"):
        return str(dotenv_values(".env").get("OPENROUTER_API_KEY") or "").strip()
    return ""
