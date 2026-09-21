"""
Resolves a frontend feature's dataTemplate name to a real {module, screen}
pair that Agents.architecture_agent can then resolve to an actual component
file.

Resolution order (a fallback chain, not an either/or switch):

  1. KMS (Qdrant, collection PIE_collection) -- real data, but from a single
     POC run of PIE, so coverage is partial. Confirmed reachable with no
     auth. Queried by pulling every point that has a module+screen
     (cached, refreshed every KMS_CACHE_SECONDS) and locally scoring each
     one against the dataTemplate's words -- NOT vector/semantic search,
     since PIE_collection's data is small (currently ~174 points) and this
     avoids needing to replicate whatever embedding model PIE used just to
     query it.

  2. Manual mapping -- a hand-curated, backend-owned stand-in in
     localedata/feature_screen_map.py, for anything KMS doesn't cover
     (which, given partial POC coverage, will be most features for now).

  3. Nothing -- if neither has an answer, the caller gets an explicit
     "unresolved" result rather than a guess.

This module deliberately does NOT do any fuzzy/LLM matching of the
resolved module+screen against the real source repo -- that's
architecture_agent's job, and only runs once a module+screen name is
already known (from step 1 or 2 here).
"""

import logging
import os
import re
import time

from qdrant_client import AsyncQdrantClient

from localedata.feature_screen_map import FEATURE_SCREEN_MAP

logger = logging.getLogger(__name__)

QDRANT_HOST = os.getenv("QDRANT_HOST", "217.217.249.121")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "PIE_collection")

# Jaccard similarity threshold (intersection / union of query and candidate
# tokens). Recalibrated from real test data: legitimate matches scored
# 0.25-0.38, an unrelated candidate scored 0.11 -- 0.2 was chosen to sit
# between those, but this is still a starting point, not a measured
# value; tune it once more real /generate runs show actual behavior.
KMS_MATCH_THRESHOLD = 0.2
KMS_CACHE_SECONDS = 300

_qdrant_client: AsyncQdrantClient | None = None
_kms_points_cache: list[dict] | None = None
_kms_points_cache_at: float = 0.0


def _get_client() -> AsyncQdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = AsyncQdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    return _qdrant_client


def _tokenize(name: str) -> set[str]:
    """'approval_requests' -> {'approval','requests'}; 'CustomerMaster' -> {'customer','master'}."""
    s = re.sub(r"[_\-]+", " ", name)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    return {t.lower() for t in s.split() if t}


async def _load_kms_points(force_refresh: bool = False) -> list[dict]:
    """Pulls every point from PIE_collection that has a module+screen
    (skips pure documentation/how-to records that don't). Cached for
    KMS_CACHE_SECONDS so a burst of /generate calls doesn't re-scroll the
    whole collection each time."""
    global _kms_points_cache, _kms_points_cache_at
    now = time.time()
    if not force_refresh and _kms_points_cache is not None and (now - _kms_points_cache_at) < KMS_CACHE_SECONDS:
        return _kms_points_cache

    client = _get_client()
    points: list[dict] = []
    try:
        offset = None
        while True:
            batch, offset = await client.scroll(
                collection_name=QDRANT_COLLECTION,
                limit=200,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in batch:
                payload = p.payload or {}
                if payload.get("module") and payload.get("screen"):
                    points.append(payload)
            if offset is None:
                break
    except Exception as e:
        logger.warning(
            "feature_resolver: could not load KMS points from Qdrant at %s:%s (%s) -- "
            "every feature will fall through to the manual mapping for this request.",
            QDRANT_HOST, QDRANT_PORT, e,
        )
        return _kms_points_cache or []

    _kms_points_cache = points
    _kms_points_cache_at = now
    logger.info("feature_resolver: loaded %d module/screen-tagged point(s) from KMS collection %r", len(points), QDRANT_COLLECTION)
    return points


def _score(query_tokens: set[str], payload: dict) -> float:
    """Jaccard similarity: |shared words| / |all distinct words in either
    side|. Plain query-coverage (shared/len(query)) was tried first but a
    single-word query like 'tracking' scored a false 1.00 the instant that
    one word appeared ANYWHERE in a candidate's text, regardless of how
    unrelated the rest of it was. Jaccard penalizes that by also counting
    the candidate's other, non-matching words against the score."""
    haystack = " ".join([
        str(payload.get("screen", "")),
        str(payload.get("title", "")),
        str(payload.get("module", "")),
        " ".join(payload.get("tags", []) or []),
    ])
    haystack_tokens = _tokenize(haystack)
    if not query_tokens or not haystack_tokens:
        return 0.0
    intersection = query_tokens & haystack_tokens
    union = query_tokens | haystack_tokens
    return len(intersection) / len(union)


async def _resolve_from_kms(data_template: str) -> dict | None:
    query_tokens = _tokenize(data_template)
    if len(query_tokens) < 2:
        # A single word is too ambiguous to trust from token overlap alone
        # -- e.g. 'tracking' could mean asset tracking, serial tracking, or
        # leave tracking, and nothing about matching that one word tells
        # you which. Skip straight to the manual mapping for these.
        logger.info("feature_resolver: skipping KMS match for single-word dataTemplate=%r (too ambiguous) -- falling through to manual mapping.", data_template)
        return None

    points = await _load_kms_points()
    if not points:
        return None

    best_score, best_payload = 0.0, None
    for payload in points:
        s = _score(query_tokens, payload)
        if s > best_score:
            best_score, best_payload = s, payload

    if best_payload is not None and best_score >= KMS_MATCH_THRESHOLD:
        logger.info(
            "feature_resolver: KMS matched dataTemplate=%r -> module=%r screen=%r (score=%.2f)",
            data_template, best_payload["module"], best_payload["screen"], best_score,
        )
        return {
            "module": best_payload["module"],
            "screen": best_payload["screen"],
            # PIE_collection records sometimes carry a plain field-name
            # list (e.g. the "Reason Master Configuration" example) --
            # carry it through so the caller can use it as the primary
            # field source instead of parsing live source code.
            "kms_fields": best_payload.get("fields") or None,
        }

    return None


def _resolve_from_manual_mapping(data_template: str) -> dict | None:
    entry = FEATURE_SCREEN_MAP.get(data_template)
    if entry is None:
        return None
    return {"module": entry["module"], "screen": entry["screen"]}


async def resolve_feature_screen(data_template: str) -> dict:
    """
    Returns:
      {"ok": True, "module": str, "screen": str, "resolved_via": "kms" | "manual_mapping"}
      or
      {"ok": False, "error": str}
    """
    kms_result = await _resolve_from_kms(data_template)
    if kms_result is not None:
        return {"ok": True, "resolved_via": "kms", **kms_result}

    manual_result = _resolve_from_manual_mapping(data_template)
    if manual_result is not None:
        return {"ok": True, "resolved_via": "manual_mapping", **manual_result}

    logger.warning(
        "feature_resolver: no KMS or manual mapping for dataTemplate=%r -- "
        "add it to localedata/feature_screen_map.py, or wait for KMS coverage.",
        data_template,
    )
    return {
        "ok": False,
        "error": (
            f"No module/screen mapping found for feature '{data_template}'. "
            "KMS doesn't have data yet and there's no manual mapping entry "
            "for it either -- add one to localedata/feature_screen_map.py."
        ),
    }