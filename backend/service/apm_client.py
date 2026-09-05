import logging
import time

import httpx

from localedata.apm_modules import swagger_url, call_url

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


async def execute_save(path_prefix: str, endpoint_path: str, login_header: str, body: dict) -> dict:
    """POST a single row to a real APM Save* endpoint. Returns
    {"ok": bool, "status_code": int, "response": dict | str, "error": str | None}."""
    url = call_url(path_prefix, endpoint_path)
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                url,
                headers={"Login": login_header, "Content-Type": "application/json", "accept": "application/json"},
                json=body,
            )
    except httpx.HTTPError as e:
        return {"ok": False, "status_code": None, "response": None, "error": str(e)}

    try:
        parsed = resp.json()
    except ValueError:
        parsed = resp.text

    if resp.is_error:
        return {"ok": False, "status_code": resp.status_code, "response": parsed, "error": f"HTTP {resp.status_code}"}

    if isinstance(parsed, dict) and parsed.get("Status") not in (None, "Ok", 200):
        status = parsed.get("Status")
        if isinstance(status, int) and status not in (200, 201, 202, 204):
            return {"ok": False, "status_code": resp.status_code, "response": parsed, "error": f"APM Status={status}"}

    return {"ok": True, "status_code": resp.status_code, "response": parsed, "error": None}
