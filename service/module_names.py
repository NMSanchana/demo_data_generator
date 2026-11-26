"""
Module-name helpers shared by the KMS catalog reader and the /generate resolver.

Why this exists
---------------
KMS gives two different things that both look like a "module name":

  * the module CODE on every point (`module`, e.g. "inventory")  -> a single token, used as the
    catalog module id and sent to /generate;
  * the UsageModule `functionality` text, which for some modules is a LIST such as
    "Administration, ESS, Framework, Projects" -> only meant for display.

APM (swagger host) and the source repository (component folders) each use their OWN module
vocabulary (localedata/apm_modules.py for APM). So a KMS module has to be mapped, never assumed
equal. Mapping order for APM:

  1. optional alias file  localedata/module_aliases.json   {"<kms code>": "<APM module name>"}
     (empty by default -- fill it from GET /kms/modules where `matchedBy` is "none")
  2. exact match (normalised) on APM module name or path
  3. fuzzy match >= APM_MIN_SIMILARITY
  4. nothing -> the caller passes the cleaned KMS code on, and the existing LLM fallback in
     Agents/apm_resolver_agent.py decides.

When the text is combined ("A, B, C") every part is tried, not just the first: the first part is
not necessarily the one APM knows.
"""

import json
import logging
import os
import re
from difflib import SequenceMatcher
from pathlib import Path

from localedata.apm_modules import APM_MODULES

logger = logging.getLogger(__name__)

APM_MIN_SIMILARITY = 0.75

_ALIAS_FILE = Path(__file__).resolve().parent.parent / "localedata" / "module_aliases.json"
_aliases_cache: dict[str, str] | None = None


def _norm(text: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def title_from_code(code: str) -> str:
    return (code or "Unassigned").replace("_", " ").title()


def split_parts(text: object) -> list[str]:
    """'Administration, ESS, Framework' -> ['Administration', 'ESS', 'Framework']."""
    return [p.strip() for p in re.split(r"[,;|]", str(text or "")) if p.strip()]


def _aliases() -> dict[str, str]:
    global _aliases_cache
    if _aliases_cache is None:
        path = Path(os.getenv("KMS_MODULE_ALIASES_FILE") or _ALIAS_FILE)
        data: dict[str, str] = {}
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8") or "{}")
                data = {_norm(k): str(v) for k, v in raw.items() if not str(k).startswith("_")}
        except Exception as e:  # a broken alias file must never break generation
            logger.warning("module_names: could not read %s (%s)", path, e)
        _aliases_cache = data
    return _aliases_cache


def apm_match(text: object) -> dict:
    """Best APM_MODULES entry for a (possibly combined) KMS module text.

    Returns {"entry": {...}|None, "matchedBy": "alias"|"exact"|"fuzzy"|"none", "similarity": float}.
    """
    parts = split_parts(text)
    if not parts:
        return {"entry": None, "matchedBy": "none", "similarity": 0.0}

    aliases = _aliases()
    for p in parts:
        target = aliases.get(_norm(p))
        if target:
            entry = next((m for m in APM_MODULES if _norm(m["module"]) == _norm(target)), None)
            if entry:
                return {"entry": entry, "matchedBy": "alias", "similarity": 1.0}

    for p in parts:
        n = _norm(p)
        for m in APM_MODULES:
            if n and (n == _norm(m["module"]) or n == _norm(m["path"])):
                return {"entry": m, "matchedBy": "exact", "similarity": 1.0}

    best, best_score = None, 0.0
    for p in parts:
        n = _norm(p)
        for m in APM_MODULES:
            score = max(SequenceMatcher(None, n, _norm(m["module"])).ratio(),
                        SequenceMatcher(None, n, _norm(m["path"])).ratio())
            if score > best_score:
                best, best_score = m, score
    if best and best_score >= APM_MIN_SIMILARITY:
        return {"entry": best, "matchedBy": "fuzzy", "similarity": round(best_score, 3)}
    return {"entry": None, "matchedBy": "none", "similarity": round(best_score, 3)}


def clean_module_code(text: object) -> str:
    """The single module token to hand to source-repo resolution: first part, trimmed."""
    parts = split_parts(text)
    return parts[0] if parts else str(text or "").strip()


def apm_module_name(text: object) -> str:
    """Name to send to the APM resolver. Canonical APM name when known, else the cleaned code
    (the APM resolver's own fuzzy + LLM fallback then takes over)."""
    m = apm_match(text)
    return m["entry"]["module"] if m["entry"] else clean_module_code(text)


# KMS sometimes only has the raw module code ("Attendanceandpayroll"). These are readable names
# for such codes. More can be added without code in localedata/module_display_names.json
# ({"<kms module code>": "<name to show>"}, keys starting with "_" are ignored).
_BUILTIN_DISPLAY_NAMES = {
    "attendanceandpayroll": "Attendance & Payroll",
    "competencymanagement": "Competency Management",
    "dealerportal": "Dealer Portal",
    "gbservicedesk": "GB Service Desk",
    "sqlworkbench": "SQL Workbench",
}
_DISPLAY_FILE = Path(__file__).resolve().parent.parent / "localedata" / "module_display_names.json"
_display_cache: dict[str, str] | None = None


def _display_overrides() -> dict[str, str]:
    global _display_cache
    if _display_cache is None:
        data = dict(_BUILTIN_DISPLAY_NAMES)
        path = Path(os.getenv("KMS_MODULE_NAMES_FILE") or _DISPLAY_FILE)
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8") or "{}")
                data.update({_norm(k): str(v).strip() for k, v in raw.items()
                             if not str(k).startswith("_") and str(v).strip()})
        except Exception as e:  # a broken names file must never break the catalog
            logger.warning("module_names: could not read %s (%s)", path, e)
        _display_cache = data
    return _display_cache


def display_name(code: str, functionality: object) -> tuple[str, list[str]]:
    """(name for the catalog tree, list of functional areas it covers).

    A single value is used as is. A combined value ("Administration, ESS, Framework, Projects")
    is a list of areas, not a name: the tree shows a short name (canonical APM name if the code
    maps to one, else the title-cased code) and the areas go to the description.
    A readable-name override for the code (see _BUILTIN_DISPLAY_NAMES) wins over both.
    """
    parts = split_parts(functionality)
    overrides = _display_overrides()
    override = overrides.get(_norm(code)) or (overrides.get(_norm(parts[0])) if len(parts) == 1 else None)
    if override:
        return override, (parts if len(parts) > 1 else [])
    if len(parts) == 1:
        return parts[0], []
    if len(parts) > 1:
        m = apm_match(code)
        return (m["entry"]["module"] if m["entry"] else title_from_code(code)), parts
    return title_from_code(code), []