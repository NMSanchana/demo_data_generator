"""
Reads the APM "Login" header (the JSON an APM login returns) and works out the
system / audit values a Save payload needs, so the same code works for ANY login.

Nothing here talks to the network. The header itself is forwarded to APM untouched
(see service/apm_client.execute_save); we only READ it to fill fields.
"""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Keys a usable login always carries. Missing ones are reported, not fatal.
EXPECTED_LOGIN_KEYS = ("UserId", "DatabaseName", "ClientId")


def clean_header(raw: str) -> str:
    """A pasted header may carry stray newlines/tabs (invalid in an HTTP header). Strip them."""
    return "".join(ch for ch in (raw or "") if ch not in "\r\n\t").strip()


def parse_login(raw: str) -> tuple[dict | None, str | None]:
    """Returns (login_dict, None) on success or (None, human-readable problem)."""
    text = clean_header(raw)
    if not text:
        return None, "The Login header is empty."
    try:
        data = json.loads(text)
    except ValueError as e:
        return None, (
            "The Login header is not valid JSON "
            f"({e}). Paste the whole login response exactly as returned, starting with '{{'."
        )
    if not isinstance(data, dict):
        return None, "The Login header must be a JSON object (the login response)."
    return data, None


def login_warnings(login: dict) -> list[str]:
    out = [f"Login has no '{k}'." for k in EXPECTED_LOGIN_KEYS if login.get(k) in (None, "")]
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def system_values(login: dict) -> tuple[dict, dict]:
    """
    Returns (always_from_login, default_if_missing), both {APM field name -> value}.

    always_from_login: identity/audit fields. The login is the truth for these, so they
        replace whatever the row holds (an LLM-invented TenantId would be wrong).
    default_if_missing: bookkeeping fields we do not know real values for. Filled with 0
        (what Swagger's own example uses) only when the row has nothing for them.
    """
    user_id = login.get("UserId")
    now = _now_iso()
    always = {
        "CreatedById": user_id,
        "ModifiedById": user_id,
        "CreatedBy": user_id,
        "ModifiedBy": user_id,
        "CreatedOn": now,
        "ModifiedOn": now,
        "SourceType": login.get("SourceType"),
        "TenantId": login.get("ClientId"),
        "TenantName": login.get("OuName"),
    }
    always = {k: v for k, v in always.items() if v is not None}
    default_if_missing = {"Version": 0, "Status": 0, "SortOrder": 0}
    return always, default_if_missing