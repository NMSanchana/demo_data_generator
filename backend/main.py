import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from models import (
    GenerateRequest,
    GenerateResponse,
    ScreenResult,
    SettingsSaveRequest,
    SettingsResponse,
    SaveRowRequest,
    SaveRowResponse,
)
from workflow.graph import generator_graph
from service import settings_service
from service.apm_client import execute_save
from Agents import architecture_agent
from Agents.apm_resolver_agent import resolve_apm_schema
from Agents.data_generator_agent import build_entity_assignment_map
from Agents.dependency_agent import detect_dependencies
from service.testdb_client import fetch_master_values
from steps import schema_extraction
from steps.apm_schema_resolution import resolve_for_generation
from tools.row_mapper import map_row_to_schema
from localedata.domain import DOMAIN_LIST

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Demo Data Generator",
    description=(
        "Generates realistic, domain- and geography-aware demo data for any "
        "module/screen in the product, for use in sales demos. Every field "
        "value comes from an LLM call — nothing is templated or randomly "
        "assembled. Preview is always available; pushing reviewed rows into "
        "the real APM system is an explicit, per-row, opt-in action."
    ),
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    await settings_service.init_pool()


@app.on_event("shutdown")
async def on_shutdown():
    await settings_service.close_pool()


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------
# Metadata endpoints backing the frontend's selectors
# ---------------------------------------------------------------------

@app.get("/meta/domains", summary="Full worldwide Domain list backing the searchable Domain dropdown (quick-pick only -- any typed value is also accepted)")
def get_domains() -> dict:
    return {"domains": DOMAIN_LIST}


# ---------------------------------------------------------------------
# Settings — unchanged from the existing (correct) implementation
# ---------------------------------------------------------------------

@app.get("/settings", response_model=SettingsResponse)
async def get_settings(module: str, screen: str | None = None):
    """
    - module only    -> the module-level default (or all-enabled fallback)
    - module + screen -> the screen-specific row if saved, else the
                          module-level default, else all-enabled fallback
    """
    result = await settings_service.get_settings(module, screen)
    return SettingsResponse(**result)


@app.post("/settings", response_model=SettingsResponse)
async def save_settings(request: SettingsSaveRequest):
    """
    - screen omitted/None -> saves the module-level default
    - screen provided     -> saves an override for that specific screen only
    """
    result = await settings_service.save_settings(
        module=request.module,
        screen=request.screen,
        use_domain=request.use_domain,
        use_subdomain=request.use_subdomain,
        use_geography=request.use_geography,
    )
    return SettingsResponse(**result)


# ---------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------

async def _resolve_screens(module: str, screen: str | None) -> list[dict]:
    if screen:
        resolved = await architecture_agent.resolve_screen(module, screen)
        return [resolved]
    return await architecture_agent.resolve_module_screens(module)


def _values_from_master_rows(
    master_rows: list[dict],
    master_field_name: str | None,
    master_fields_schema: list[dict],
    dependent_field_name: str,
) -> list[str]:
    """
    Pull real, distinct values out of a master screen's already-generated
    rows for use by a dependent field elsewhere. Tries, in order: the
    agent-suggested master_field_name (if it's actually a key on the
    generated rows), the master screen's own "identifier"-kind field, the
    dependent field's own name (in case both screens happen to share it),
    then simply the first column present -- never raises, returns [] if
    nothing usable is found.
    """
    if not master_rows:
        return []

    first_row = master_rows[0]
    candidate_field = master_field_name if master_field_name in first_row else None

    if candidate_field is None:
        identifier_fields = [f["field_name"] for f in master_fields_schema if f.get("kind") == "identifier"]
        if identifier_fields and identifier_fields[0] in first_row:
            candidate_field = identifier_fields[0]

    if candidate_field is None and dependent_field_name in first_row:
        candidate_field = dependent_field_name

    if candidate_field is None:
        candidate_field = next(iter(first_row), None)

    if candidate_field is None:
        return []

    values: list[str] = []
    seen: set[str] = set()
    for row in master_rows:
        value = row.get(candidate_field)
        if value is None:
            continue
        value_str = str(value)
        if value_str not in seen:
            seen.add(value_str)
            values.append(value_str)

    return values


async def _resolve_dependency_field_values(
    module: str,
    dependencies: list[dict],
    generated_rows_by_screen: dict[str, list[dict]],
    fields_by_screen: dict[str, list[dict]],
) -> dict[str, list[str]]:
    """
    Implements the three-tier fallback for every field this screen's
    dependency_agent call flagged as referencing another screen:

      1. Reuse that other screen's rows if they were already generated
         earlier in this same request.
      2. Otherwise, look up real, currently-existing values in the real
         TESTDB (service/testdb_client.py) -- engine-agnostic, fuzzy table/
         column matching, never raises.
      3. Otherwise, this field is simply omitted from the returned dict --
         Agents/data_generator_agent.py generates it exactly as before
         (invented), with no error and no interruption.
    """
    dependency_field_values: dict[str, list[str]] = {}

    for dep in dependencies:
        field_name = dep["field_name"]
        master_screen = dep["depends_on_screen"]
        master_field_name = dep.get("master_field_name")

        # Tier 1 -- reuse in-run generated data, if the master screen has
        # already produced rows earlier in this same request's loop.
        master_rows = generated_rows_by_screen.get(master_screen)
        if master_rows:
            values = _values_from_master_rows(
                master_rows, master_field_name, fields_by_screen.get(master_screen, []), field_name,
            )
            if values:
                dependency_field_values[field_name] = values
                continue

        # Tier 2 -- real TESTDB lookup. Never allowed to raise or block.
        try:
            values = await fetch_master_values(master_screen, master_field_name or field_name)
        except Exception:
            logger.exception(
                "testdb_client crashed resolving %s/%s -- falling back to invented value",
                master_screen, master_field_name or field_name,
            )
            values = None

        if values:
            dependency_field_values[field_name] = values
            continue

        # Tier 3 -- neither available; leave this field out entirely so
        # data_generator_agent invents it exactly as it does today.
        logger.info(
            "dependency fallback: no in-run data or TESTDB match for %s -> %s/%s -- inventing as usual",
            field_name, master_screen, master_field_name or field_name,
        )

    return dependency_field_values


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    settings = await settings_service.get_settings(request.module, request.screen)

    domain_value = request.domain if settings["use_domain"] else None
    subdomain_value = request.subdomain if (settings["use_subdomain"] and request.subdomain) else None
    # Raw, as-typed geography text ("Singanallur", "near Coimbatore", ...).
    # Resolving this to a real place is NOT done here -- it happens inside
    # the first screen's data_generator_agent_node call below, and that
    # resolved value is then reused for every subsequent screen in this
    # same request (see the loop further down).
    raw_geography = request.geography if settings["use_geography"] else None

    resolved_screens = await _resolve_screens(request.module, request.screen)
    if not resolved_screens:
        detail = f"Could not resolve module '{request.module}'"
        if request.screen:
            detail += f" / screen '{request.screen}'"
        detail += " against the source repository."
        raise HTTPException(status_code=422, detail=detail)

    # Phase 1: schema extraction (cached, deterministic) + APM schema
    # resolution (cached) for every resolved screen.
    screen_contexts: list[dict] = []
    for r in resolved_screens:
        if not r.get("ok"):
            screen_contexts.append({"ok": False, "screen_name": r["screen_name"], "error": r.get("error", "Resolution failed")})
            continue

        fields = schema_extraction.extract_fields(r["path"], r["component_html"])
        if not fields:
            screen_contexts.append({"ok": False, "screen_name": r["screen_name"], "error": "No form fields found on this screen."})
            continue

        apm_resolution = await resolve_for_generation(request.module, r["screen_name"])

        screen_contexts.append({
            "ok": True,
            "screen_name": r["screen_name"],
            "resolved_path": r["path"],
            "fields": fields,
            "apm_resolution": apm_resolution,
        })

    if not any(c["ok"] for c in screen_contexts):
        # every screen failed to resolve/extract -- surface the first error
        first_error = next((c["error"] for c in screen_contexts if not c["ok"]), "Generation failed.")
        raise HTTPException(status_code=422, detail=first_error)

    # Phase 1.5: dependency detection — for every resolved screen, ask
    # Agents/dependency_agent.py (LLM-based reasoning over that screen's
    # own field list, given the names of every other screen resolved in
    # this same request) whether any field is a foreign-key-like reference
    # into another screen's master data. This never blocks generation: a
    # screen with no detected dependencies (or a failed/empty LLM call)
    # just gets an empty dependency list, and generates exactly as before.
    ok_screen_names = [c["screen_name"] for c in screen_contexts if c["ok"]]
    fields_by_screen: dict[str, list[dict]] = {c["screen_name"]: c["fields"] for c in screen_contexts if c["ok"]}

    for ctx in screen_contexts:
        if not ctx["ok"]:
            continue
        other_screens = [name for name in ok_screen_names if name != ctx["screen_name"]]
        try:
            dependency_result = await detect_dependencies(ctx["screen_name"], ctx["fields"], other_screens)
        except Exception:
            logger.exception("dependency_agent crashed for %s/%s -- treating as no dependencies", request.module, ctx["screen_name"])
            dependency_result = {"dependencies": []}
        ctx["dependencies"] = dependency_result.get("dependencies", [])

    # Phase 2: ONE shared entity-assignment call across every resolved
    # screen's picklist fields (cross-screen consistency for full-module runs).
    picklist_field_names = sorted({
        f["field_name"]
        for ctx in screen_contexts if ctx["ok"]
        for f in ctx["fields"] if f.get("kind") == "picklist"
    })
    entity_assignment_map = await build_entity_assignment_map(
        module=request.module,
        domain=domain_value,
        subdomain=subdomain_value,
        geography=raw_geography,
        picklist_field_names=picklist_field_names,
    )

    # Phase 3: per-screen row generation via the LangGraph pipeline.
    # geography starts as the raw typed text; the FIRST screen's
    # generation call resolves it to a real place (e.g. "Singanallur" ->
    # "Tamil Nadu, India") and every screen after that reuses the
    # resolved value directly instead of re-resolving the same raw text
    # independently per screen (see Agents/data_generator_agent.py).
    geography_for_next_screen = raw_geography
    geography_resolved_yet = False

    # Accumulates each screen's rows as they're generated, so a later
    # screen in this same loop whose dependency_agent result points at an
    # earlier screen can reuse its real generated data (see
    # _resolve_dependency_field_values's Tier 1). Screens that fail, or
    # that are processed before their master screen resolves, simply fall
    # through to Tier 2 (TESTDB) or Tier 3 (invent) below.
    generated_rows_by_screen: dict[str, list[dict]] = {}

    results: list[ScreenResult] = []
    for ctx in screen_contexts:
        if not ctx["ok"]:
            results.append(ScreenResult(status="error", message=ctx["error"], screen=ctx["screen_name"]))
            continue

        apm_resolution = ctx["apm_resolution"]
        apm_ready = bool(apm_resolution.get("ok"))

        dependency_field_values = await _resolve_dependency_field_values(
            request.module, ctx.get("dependencies", []), generated_rows_by_screen, fields_by_screen,
        )

        initial_state = {
            "module": request.module,
            "screen": ctx["screen_name"],
            "domain": domain_value,
            "subdomain": subdomain_value,
            "geography": geography_for_next_screen,
            "geography_already_resolved": geography_resolved_yet,
            "row_count": request.row_count,
            "use_domain": settings["use_domain"],
            "use_subdomain": settings["use_subdomain"],
            "use_geography": settings["use_geography"],
            "generated_fields": ctx["fields"],
            "apm_type_hints": apm_resolution.get("scalar_fields", {}) if apm_ready else {},
            "entity_assignment_map": entity_assignment_map,
            "dependency_field_values": dependency_field_values,
        }

        try:
            final_state = await generator_graph.ainvoke(initial_state)
        except Exception as e:
            logger.exception("Generation pipeline crashed for %s/%s", request.module, ctx["screen_name"])
            results.append(ScreenResult(status="error", message=str(e), screen=ctx["screen_name"]))
            continue

        if final_state.get("generation_error"):
            results.append(ScreenResult(status="error", message=final_state["generation_error"], screen=ctx["screen_name"]))
            continue

        if settings["use_geography"] and final_state.get("resolved_geography"):
            geography_for_next_screen = final_state["resolved_geography"]
            geography_resolved_yet = True

        rows = final_state.get("generated_rows") or []
        generated_rows_by_screen[ctx["screen_name"]] = rows
        results.append(ScreenResult(
            status="ok",
            screen=ctx["screen_name"],
            resolved_path=ctx["resolved_path"],
            fields=ctx["fields"],
            rows=rows,
            row_count=len(rows),
            apm_ready=apm_ready,
            apm_disabled_reason=None if apm_ready else apm_resolution.get("error"),
            resolved_geography=final_state.get("resolved_geography"),
        ))

    return GenerateResponse(module=request.module, results=results)


# ---------------------------------------------------------------------
# APM save — explicit, per-row, opt-in
# ---------------------------------------------------------------------

@app.post("/save-row", response_model=SaveRowResponse)
async def save_row(request: SaveRowRequest, raw_request: Request):
    login_header = raw_request.headers.get("Login", "")
    if not login_header.strip():
        raise HTTPException(status_code=400, detail="A Login header is required to save to APM.")

    resolution = await resolve_apm_schema(request.module, request.screen)
    if not resolution.get("ok"):
        return SaveRowResponse(ok=False, error=resolution.get("error"))

    body = map_row_to_schema(request.row, resolution["scalar_fields"])
    result = await execute_save(resolution["path_prefix"], resolution["endpoint_path"], login_header, body)

    return SaveRowResponse(
        ok=result["ok"], status_code=result["status_code"],
        error=result["error"], response=result["response"],
    )