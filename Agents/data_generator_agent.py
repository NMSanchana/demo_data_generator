import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from Agents.llm import get_client, get_model

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_ROW_COUNT = 20


def _load_prompt(filename: str) -> str:
    return (Path(__file__).resolve().parent.parent / "prompts" / filename).read_text(encoding="utf-8")


async def _ask_llm_json(system_prompt: str, user_message: str, temperature: float) -> dict:
    client = get_client()
    try:
        response = await client.chat.completions.create(
            model=get_model(),
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


async def build_entity_assignment_map(
    module: str,
    domain: str | None,
    subdomain: str | None,
    geography: str | None,
    picklist_field_names: list[str],
) -> dict[str, list[str]]:
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


def _build_user_message(state: dict) -> str:
    row_count = state.get("row_count") or DEFAULT_ROW_COUNT
    fields = state.get("generated_fields") or []

    parts = [
        f"module: {state['module']}",
        f"screen: {state['screen']}",
        f"row_count: {row_count}",
    ]

    if state.get("domain"):
        parts.append(f"domain: {state['domain']}")

    if state.get("subdomain"):
        parts.append(f"subdomain: {state['subdomain']}")

    if state.get("geography"):
        parts.append(f"geography: {state['geography']}")
        parts.append(
            "geography_already_resolved: "
            + ("true" if state.get("geography_already_resolved") else "false")
        )

    apm_type_hints = state.get("apm_type_hints") or {}
    if apm_type_hints:
        parts.append("")
        parts.append(
            "apm_field_types (the REAL save-target type for these field names -- "
            "generate a value of exactly this type, not display text, for any "
            "field listed here. Each entry may also carry: is_required (true "
            "means APM will reject the save if this field is left blank -- "
            "never leave it empty); and value-shape constraints APM enforces "
            "-- maxLength/minLength (character count), pattern (a regex the "
            "value must match), minimum/maximum (numeric range). Respect all "
            "of these when generating a value for a field that has them):"
        )
        parts.append(json.dumps(apm_type_hints))

        fk_options_block = {
            field_name: field_meta["fk_options"]
            for field_name, field_meta in apm_type_hints.items()
            if field_meta.get("fk_options")
        }
        if fk_options_block:
            parts.append("")
            parts.append(
                "apm_fk_options (for these fields, you MUST pick one of the "
                "real id values listed here, matched to whichever option "
                "best fits the row's context -- never invent a new id):"
            )
            parts.append(json.dumps(fk_options_block))

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

    already = state.get("already_generated") or {}
    if already:
        parts.append("")
        parts.append(
            "already_generated (an earlier batch of this same request already produced "
            "these values. Do NOT repeat any of them, and continue identifier numbering "
            "after them):"
        )
        parts.append(json.dumps(already))

    parts.append("")
    parts.append("fields:")
    parts.append(json.dumps(fields))

    return "\n".join(parts)


def _rows_per_call() -> int:
    """Rows asked for in ONE LLM call. A single call for many rows x many fields can exceed the
    model's output limit and come back cut off (broken JSON), so big requests are split."""
    try:
        return max(1, int(os.getenv("ROWS_PER_LLM_CALL", "5")))
    except ValueError:
        return 5


async def data_generator_agent_node(state: dict) -> dict:
    fields = state.get("generated_fields") or []
    if not fields:
        return {
            "generated_rows": [],
            "resolved_geography": None,
            "generation_error": "No fields available — schema extraction must run first.",
        }

    logger.info(
        "data_generator_agent_node: generating for module=%r screen=%r "
        "domain=%s subdomain=%s geography=%s apm_ready=%s",
        state.get("module"), state.get("screen"),
        bool(state.get("domain")), bool(state.get("subdomain")), bool(state.get("geography")),
        bool(state.get("apm_type_hints")),
    )

    total = state.get("row_count") or DEFAULT_ROW_COUNT
    per_call = _rows_per_call()
    system_prompt = _load_prompt("data_generator_prompt.txt")
    id_like = [f["field_name"] for f in fields if f.get("kind") in ("identifier", "name")]

    rows: list[dict] = []
    resolved_geography = None
    batch_state = dict(state)

    while len(rows) < total:
        want = min(per_call, total - len(rows))
        batch_state["row_count"] = want
        if rows:
            seen = {n: [str(r[n]) for r in rows if r.get(n) is not None] for n in id_like}
            batch_state["already_generated"] = {n: v for n, v in seen.items() if v}

        result = await _ask_llm_json(system_prompt, _build_user_message(batch_state), temperature=0.8)

        if "error" in result:
            if rows:
                logger.warning("data_generator_agent_node: a later batch failed (%s) -- keeping the %d row(s) already generated", result["error"], len(rows))
                break
            return {"generated_rows": [], "resolved_geography": None, "generation_error": result["error"]}

        batch = [r for r in (result.get("rows") or []) if isinstance(r, dict)]
        if not batch:
            if rows:
                break
            return {"generated_rows": [], "resolved_geography": None, "generation_error": "Agent returned no rows."}

        rows.extend(batch[:want])
        logger.info("data_generator_agent_node: batch of %d done (%d/%d rows)", len(batch[:want]), len(rows), total)

        # The first batch resolves the typed place; later batches reuse it unchanged.
        if state.get("geography") and resolved_geography is None:
            resolved_geography = result.get("resolved_geography") or state["geography"]
            batch_state["geography"] = resolved_geography
            batch_state["geography_already_resolved"] = True

    logger.info("data_generator_agent_node: generated %d row(s)", len(rows))
    return {"generated_rows": rows, "resolved_geography": resolved_geography, "generation_error": None}