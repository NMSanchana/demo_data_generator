import json
import logging
import time

import httpx

from localedata.apm_modules import swagger_url, call_url
from service.apm_session import clean_header

logger = logging.getLogger(__name__)

_SPEC_CACHE: dict[str, dict] = {}
_SPEC_CACHE_AT: dict[str, float] = {}
SPEC_CACHE_SECONDS = 300
REQUEST_TIMEOUT = 20


async def get_swagger_spec(path_prefix: str, module_name: str, force_refresh: bool = False) -> dict:
    key = f"{path_prefix}/{module_name}"
    now = time.time()
    if (
        not force_refresh
        and key in _SPEC_CACHE
        and (now - _SPEC_CACHE_AT.get(key, 0)) < SPEC_CACHE_SECONDS
    ):
        return _SPEC_CACHE[key]

    url = swagger_url(path_prefix, module_name)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        spec = resp.json()

    _SPEC_CACHE[key] = spec
    _SPEC_CACHE_AT[key] = now
    return spec


def _extract_detail(parsed) -> str | None:
    """APM wraps failures in its standard envelope: {"Status": 400, "ErrorBody": "...",
    "ErrorInnerException": "..."}. That text is the real reason, so surface it verbatim."""
    if isinstance(parsed, dict):
        parts = []
        for key in ("ErrorBody", "ErrorInnerException"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
        if not parts:
            for key in ("message", "Message", "title", "detail", "error"):
                val = parsed.get(key)
                if isinstance(val, str) and val.strip():
                    parts.append(val.strip())
                    break
        if not parts and isinstance(parsed.get("errors"), dict):
            parts.append(json.dumps(parsed["errors"])[:600])
        return " | ".join(parts) if parts else None
    if isinstance(parsed, str) and parsed.strip():
        return parsed.strip()[:600]
    return None


async def execute_save(path_prefix: str, endpoint_path: str, login_header: str, body: dict) -> dict:
    """POST a single row to a real APM Save* endpoint. Returns
    {"ok", "status_code", "response", "error", "detail", "url"}.

    The Login header is sent exactly as given (only stray newlines removed) and encoded as
    UTF-8 bytes, so a login holding non-ASCII characters (e.g. a user name) cannot crash httpx."""
    url = call_url(path_prefix, endpoint_path)
    header_value = clean_header(login_header).encode("utf-8")
    logger.info("apm save -> %s  body=%s", url, json.dumps(body, default=str)[:1500])

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                url,
                headers={"Login": header_value, "Content-Type": "application/json", "accept": "application/json"},
                json=body,
            )
    except httpx.HTTPError as e:
        logger.warning("apm save failed to connect to %s: %s", url, e)
        return {"ok": False, "status_code": None, "response": None, "error": str(e), "detail": None, "url": url}

    try:
        parsed = resp.json()
    except ValueError:
        parsed = resp.text

    if resp.is_error:
        detail = _extract_detail(parsed)
        logger.warning("apm save HTTP %s from %s: %s", resp.status_code, url, str(parsed)[:1500])
        error = f"HTTP {resp.status_code}" + (f": {detail}" if detail else "")
        return {"ok": False, "status_code": resp.status_code, "response": parsed, "error": error, "detail": detail, "url": url}

    # APM can answer HTTP 200 with a failure code inside its envelope.
    if isinstance(parsed, dict) and parsed.get("Status") not in (None, "Ok", 200):
        status = parsed.get("Status")
        if isinstance(status, int) and status not in (200, 201, 202, 204):
            detail = _extract_detail(parsed)
            logger.warning("apm save envelope Status=%s from %s: %s", status, url, str(parsed)[:1500])
            error = f"APM Status={status}" + (f": {detail}" if detail else "")
            return {"ok": False, "status_code": resp.status_code, "response": parsed, "error": error, "detail": detail, "url": url}

    logger.info("apm save OK from %s", url)
    return {"ok": True, "status_code": resp.status_code, "response": parsed, "error": None, "detail": None, "url": url}