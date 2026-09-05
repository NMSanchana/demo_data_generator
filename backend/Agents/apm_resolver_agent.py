"""
APM Resolver Agent — two-level resolution: module -> APM path prefix, then
screen -> real Save* endpoint + request schema, within that module's live
OpenAPI spec. Fuzzy pass first at each level; LLM fallback only when
unconfident. Never fabricates a path or endpoint — returns an explicit
error on total failure.

Ported from the old build's agents/service_resolver_agent.py, renamed per
the "never name the real ERP product" product decision, switched to async.

Section 3 of the remediation spec moves this resolution to run BEFORE data
generation (not just at save time) — see steps/apm_schema_resolution.py —
specifically so the Data Generator Agent can be told the real target type
of every field. This module also backs the actual /save-row endpoint via
resolve_apm_schema()'s per-module/screen cache, so a screen resolved during
/generate is never re-resolved when the person clicks Save.
"""

import json
import logging
import os
from pathlib import Path

from openai import AsyncOpenAI
from dotenv import load_dotenv

from localedata.apm_modules import APM_MODULES
from service.apm_client import get_swagger_spec
from tools import apm_resolver_fuzzy as fuzzy

load_dotenv()

logger = logging.getLogger(__name__)

MODULE_LLM_FALLBACK_THRESHOLD = 0.75
ENDPOINT_LLM_FALLBACK_THRESHOLD = 0.7

_client: AsyncOpenAI | None = None

# Per-module/screen resolution cache — the resolver only needs to hit the
# APM module manifest + swagger.json once per screen; every subsequent
# generate/save-row call for that screen reuses this (same pattern as the
# old build's _erp_resolution_cache, just module-scoped here instead of
# living in main.py).
_resolution_cache: dict[str, dict] = {}


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY must be set in .env")
        _client = AsyncOpenAI(api_key=api_key)
    return _client


def _model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _load_prompt() -> str:
    return (Path(__file__).resolve().parent.parent / "prompts" / "apm_resolver_prompt.txt").read_text(encoding="utf-8")


async def _ask_llm(user_message: str) -> dict:
    client = _get_client()
    try:
        response = await client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": _load_prompt()},
                {"role": "user", "content": user_message},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or "{}"
        return json.loads(text)
    except Exception as e:
        logger.warning("ApmResolverAgent LLM call failed: %s", e)
        return {"error": str(e)}


async def _llm_resolve_module(query: str) -> dict:
    candidates = [{"module": m["module"], "path": m["path"]} for m in APM_MODULES]
    return await _ask_llm(f"scope: module\nquery: {query}\n\ncandidates:\n{json.dumps(candidates)}")


async def _llm_resolve_endpoint(spec: dict, query: str) -> dict:
    candidates = [
        {"entity": entity, "path": path}
        for path, _op_id, entity, schema_name in fuzzy.iter_save_operations(spec)
        if schema_name
    ]
    return await _ask_llm(f"scope: endpoint\nquery: {query}\n\ncandidates:\n{json.dumps(candidates)}")


async def _resolve_uncached(module_query: str, screen_query: str) -> dict:
    module_match = fuzzy.resolve_module(module_query)
    if module_match is None or module_match.confidence < MODULE_LLM_FALLBACK_THRESHOLD:
        agent_result = await _llm_resolve_module(module_query)
        if "error" not in agent_result:
            entry = next((m for m in APM_MODULES if m["module"] == agent_result.get("module")), None)
            if entry:
                module_match = fuzzy.ResolvedModule(
                    module=entry["module"], path=entry["path"], cluster=entry["cluster"],
                    confidence=float(agent_result.get("confidence", 0.6)),
                )
        if module_match is None:
            return {"ok": False, "error": f"Could not resolve module '{module_query}' against the APM module manifest."}

    try:
        spec = await get_swagger_spec(module_match.path, module_match.module)
    except Exception as e:
        return {"ok": False, "error": f"Could not fetch APM schema for module '{module_match.module}': {e}"}

    endpoint_match = fuzzy.resolve_save_endpoint(spec, screen_query)
    if endpoint_match is None or endpoint_match.confidence < ENDPOINT_LLM_FALLBACK_THRESHOLD:
        agent_result = await _llm_resolve_endpoint(spec, screen_query)
        if "error" not in agent_result:
            for path, op_id, entity, schema_name in fuzzy.iter_save_operations(spec):
                if path == agent_result.get("path") and entity == agent_result.get("entity") and schema_name:
                    endpoint_match = fuzzy.ResolvedEndpoint(
                        entity=entity, path=path, operation_id=op_id, schema_name=schema_name,
                        confidence=float(agent_result.get("confidence", 0.6)),
                    )
                    break
        if endpoint_match is None:
            return {"ok": False, "error": f"Could not resolve screen '{screen_query}' to a Save endpoint under module '{module_match.module}'."}

    field_info = fuzzy.extract_schema_fields(spec, endpoint_match.schema_name)

    return {
        "ok": True,
        "module": module_match.module,
        "path_prefix": module_match.path,
        "entity": endpoint_match.entity,
        "endpoint_path": endpoint_match.path,
        "schema_name": endpoint_match.schema_name,
        "scalar_fields": field_info["scalar_fields"],
        "nested_fields": field_info["nested_fields"],
    }


async def resolve_apm_schema(module_query: str, screen_query: str, force_refresh: bool = False) -> dict:
    """
    Resolve module_query/screen_query to a real APM Save endpoint + its
    request schema. Cached per (module, screen) so the same screen is
    never re-resolved between /generate and /save-row.

    Returns {"ok": True, "module", "path_prefix", "entity", "endpoint_path",
    "schema_name", "scalar_fields", "nested_fields"} on success, or
    {"ok": False, "error": str} on failure — generation still proceeds on
    failure (see steps/apm_schema_resolution.py), only Save is disabled.
    """
    cache_key = f"{module_query}|{screen_query}"
    if not force_refresh and cache_key in _resolution_cache:
        return _resolution_cache[cache_key]

    result = await _resolve_uncached(module_query, screen_query)
    _resolution_cache[cache_key] = result
    return result
