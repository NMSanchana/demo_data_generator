"""
Schema cache — keyed on resolved component path(s), so the same screen's
fields are extracted once and reused on every subsequent request instead of
being re-fetched and re-parsed from GitLab every time.

Disk-backed JSON (survives process restarts) with an in-memory mirror for
speed within a running process. Ported/added per Section 5.3 of the
remediation spec ("port old's cache-store pattern").
"""

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "schema_cache.json"

_lock = threading.Lock()
_memory_cache: dict[str, list[dict]] | None = None


def _load() -> dict[str, list[dict]]:
    global _memory_cache
    if _memory_cache is not None:
        return _memory_cache

    if _CACHE_FILE.exists():
        try:
            _memory_cache = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("schema_cache: failed to load cache file, starting fresh: %s", e)
            _memory_cache = {}
    else:
        _memory_cache = {}
    return _memory_cache


def _persist() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(_memory_cache, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("schema_cache: failed to persist cache file: %s", e)


def get(cache_key: str) -> list[dict] | None:
    with _lock:
        cache = _load()
        return cache.get(cache_key)


def set(cache_key: str, fields: list[dict]) -> None:
    with _lock:
        cache = _load()
        cache[cache_key] = fields
        _persist()


def clear() -> None:
    """Mainly for tests / manual cache-busting."""
    global _memory_cache
    with _lock:
        _memory_cache = {}
        if _CACHE_FILE.exists():
            try:
                _CACHE_FILE.unlink()
            except OSError:
                pass


def enabled() -> bool:
    return os.getenv("DISABLE_SCHEMA_CACHE", "").lower() not in ("1", "true", "yes")
