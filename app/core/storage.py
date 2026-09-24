"""Uploaded files (JDs now; CVs and DP sheets later). Local disk in v1, S3 in Phase 7.

Stored paths are keys relative to the storage root, so switching backends doesn't rewrite rows.
"""

import re
from pathlib import Path

from app.core.config import get_settings

JD_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt", ".rtf"}


class StorageError(ValueError):
    pass


def _root() -> Path:
    s = get_settings()
    if s.storage_backend != "local":
        raise NotImplementedError("S3 storage arrives in Phase 7")
    return s.local_path(s.storage_dir)


def safe_name(filename: str) -> str:
    name = Path(filename or "file").name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .") or "file"
    return name[:120]


def check(filename: str, data: bytes, allowed: set[str]) -> str:
    """Validate an upload before anything is written; returns the safe file name."""
    name = safe_name(filename)
    if Path(name).suffix.lower() not in allowed:
        raise StorageError(f"use one of {', '.join(sorted(allowed))}")
    limit = get_settings().max_upload_mb
    if len(data) > limit * 1024 * 1024:
        raise StorageError(f"the file is larger than {limit} MB")
    if not data:
        raise StorageError("the file is empty")
    return name


def save(key_dir: str, filename: str, data: bytes, allowed: set[str]) -> str:
    name = check(filename, data, allowed)
    key = f"{key_dir}/{name}"
    path = _root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key


def open_path(key: str) -> Path:
    root = _root().resolve()
    path = (root / key).resolve()
    if root not in path.parents or not path.is_file():
        raise FileNotFoundError(key)
    return path
