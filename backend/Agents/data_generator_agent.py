"""
Data Generator Agent — generates demo rows for fields that schema
extraction already found (steps/schema_extraction.py).

No Faker, no random/regex-based content anywhere in this file. The LLM
owns every field's value for every row, including picklist choices and
values for fields whose real APM save-schema type is numeric/boolean (it's
told explicitly what type to produce for those — see Section 6.3 of the
remediation spec, this is the actual fix for the old byte/integer 400
error, not a row_mapper patch).

Two entry points:
  - build_entity_assignment_map(...): ONE shared LLM call per request,
    asking for a small set of reusable named entities per picklist field
    that recurs across screens, so full-module runs show the SAME
    entities on every screen (cross-screen consistency), still
    agent-generated, just shared across the batch instead of re-invented
    per screen.
  - data_generator_agent_node(state): the LangGraph node that generates
    one screen's row batch, given the shared entity map (if any) and the
    APM type hints (if APM resolution for this screen succeeded).
"""

import json
import logging
import os
from pathlib import Path

from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None

DEFAULT_ROW_COUNT = 20


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


def _load_prompt(filename: str) -> str:
    return (Path(__file__).resolve().parent.parent / "prompts" / filename).read_text(encoding="utf-8")


async def _ask_llm_json(system_prompt: str, user_message: str, temperature: float) -> dict:
    client = _get_client()
    try:
        response = await client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or "{}"
        return json.loads(text)
    except Exception as e:
        logger.warning("DataGeneratorAgent LLM call failed: %s", e)
        return {"error": str(e)}


# --------------------------------------------------------------------
# Shared entity assignment — one call per request, not per screen
# --------------------------------------------------------------------

async def build_entity_assignment_map(
    module: str,
    domain: str | None,
    subdomain: str | None,
    geography: str | None,
    picklist_field_names: list[str],
) -> dict[str, list[str]]:
    """
    Asks the LLM for a small reusable pool of named entities per picklist
    field name, once per request, so every screen in a full-module run
    that shares a picklist field name (e.g. "Status", "Department") shows
    the SAME entity pool rather than each screen inventing its own.
    """
    if not picklist_field_names:
        return {}

    system_prompt = _load_prompt("entity_assignment_prompt.txt")
    parts = [f"module: {module}"]
    if domain:
        parts.append(f"domain: {domain}")
    if subdomain:
        parts.append(f"subdomain: {subdomain}")
    if geography:
        parts.append(f"geography: {geography}")
    parts.append("")
    parts.append("picklist_field_names:")
    parts.append(json.dumps(picklist_field_names))
    user_message = "\n".join(parts)

    result = await _ask_llm_json(system_prompt, user_message, temperature=0.7)
    if "error" in result:
        logger.warning("build_entity_assignment_map: LLM call failed, screens will generate independently: %s", result["error"])
        return {}

    pools = result.get("pools") or {}
    return {k: v for k, v in pools.items() if isinstance(v, list) and v}


# --------------------------------------------------------------------
# Per-screen row generation
# --------------------------------------------------------------------

def _build_user_message(state: dict) -> str:
    """
    Only include domain/subdomain/geography when their settings flag is
    True. A disabled component must never reach the agent, even if the
    frontend sent a value for it.
    """
    row_count = state.get("row_count") or DEFAULT_ROW_COUNT
    fields = state.get("generated_fields") or []

    parts = [
        f"module: {state['module']}",
        f"screen: {state['screen']}",
        f"row_count: {row_count}",
    ]

    if state.get("use_domain", True) and state.get("domain"):
        parts.append(f"domain: {state['domain']}")

    if state.get("use_subdomain", True) and state.get("subdomain"):
        parts.append(f"subdomain: {state['subdomain']}")

    if state.get("use_geography", True) and state.get("geography"):
        parts.append(f"geography: {state['geography']}")

    apm_type_hints = state.get("apm_type_hints") or {}
    if apm_type_hints:
        parts.append("")
        parts.append(
            "apm_field_types (the REAL save-target type for these field names -- "
            "generate a value of exactly this type, not display text, for any "
            "field listed here):"
        )
        parts.append(json.dumps(apm_type_hints))

    entity_assignment_map = state.get("entity_assignment_map") or {}
    relevant_pools = {
        f["field_name"]: entity_assignment_map[f["field_name"]]
        for f in fields
        if f.get("kind") == "picklist" and f["field_name"] in entity_assignment_map
    }
    if relevant_pools:
        parts.append("")
        parts.append(
            "preferred_picklist_values (use ONLY these values for the matching "
            "field, so the same named entities appear consistently across every "
            "screen in this request):"
        )
        parts.append(json.dumps(relevant_pools))

    parts.append("")
    parts.append("fields:")
    parts.append(json.dumps(fields))

    return "\n".join(parts)


async def data_generator_agent_node(state: dict) -> dict:
    """
    LangGraph node — generates demo rows for the fields schema extraction
    already found, using only the enabled components.

    Reads:
      state["generated_fields"], state["module"], state["screen"],
      state["row_count"], state["domain"], state["subdomain"],
      state["geography"], state["use_domain"], state["use_subdomain"],
      state["use_geography"], state["apm_type_hints"],
      state["entity_assignment_map"]

    Writes:
      state["generated_rows"]   — list of row dicts (one per demo record)
      state["generation_error"] — str | None
    """
    fields = state.get("generated_fields") or []
    if not fields:
        return {
            "generated_rows": [],
            "generation_error": "No fields available — schema extraction must run first.",
        }

    logger.info(
        "data_generator_agent_node: generating for module=%r screen=%r "
        "use_domain=%s use_subdomain=%s use_geography=%s apm_ready=%s",
        state.get("module"), state.get("screen"),
        state.get("use_domain", True), state.get("use_subdomain", True), state.get("use_geography", True),
        bool(state.get("apm_type_hints")),
    )

    system_prompt = _load_prompt("data_generator_prompt.txt")
    user_message = _build_user_message(state)
    result = await _ask_llm_json(system_prompt, user_message, temperature=0.8)

    if "error" in result:
        return {"generated_rows": [], "generation_error": result["error"]}

    rows = result.get("rows") or []
    if not rows:
        return {"generated_rows": [], "generation_error": "Agent returned no rows."}

    logger.info("data_generator_agent_node: generated %d row(s)", len(rows))
    return {"generated_rows": rows, "generation_error": None}
