"""Configuration and secret helpers for local and deployed environments."""

import os
from pathlib import Path
from typing import Optional


BASE_DIR = Path(__file__).resolve().parent.parent


def get_secret(name: str, fallback_file: Optional[str] = None) -> Optional[str]:
    """Return a secret from env first, then an optional local fallback file.

    This helper never logs or prints secret values.
    """
    value = os.getenv(name, "").strip()
    if value:
        return value

    if fallback_file:
        path = Path(fallback_file)
        if not path.is_absolute():
            path = BASE_DIR / path
        if path.exists():
            file_value = path.read_text(encoding="utf-8").strip()
            if file_value:
                return file_value

    return None
