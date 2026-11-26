import asyncio
import logging
import os
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from models import (
    GenerateRequest,
    GenerateResponse,
    ScreenResult,
    SaveRowRequest,
    SaveRowResponse,
    StorageInfo,
)
from workflow.graph import generator_graph
from service.apm_client import execute_save
from service.apm_session import parse_login, login_warnings
from service import feature_resolver
from service.postgres_client import init_pool, close_pool
from service import profile_db, run_store
from Agents import architecture_agent
from Agents.apm_resolver_agent import resolve_apm_schema
from Agents.data_generator_agent import build_entity_assignment_map
from steps import schema_extraction
from tools.schema_parser import classify_fields
from steps.apm_schema_resolution import resolve_for_generation
from tools.row_mapper import map_row_to_schema
from localedata.domain import DOMAIN_LIST
from service.table_setup import run_migrations
from routers import profiles, targets, workspaces, schedules, approvals, masking, dimensions, dashboard, kms

load_dotenv()

# --- Generation-only controls (all optional, set in .env) -------------------
# APM_ENABLED=false  -> skip every APM / swagger / foreign-key lookup. Generation
#                       then needs only KMS (or GitLab) + OpenAI. Push-to-APM is off.
# MAX_FIELDS_PER_SCREEN -> some KMS screens hold hundreds of fields. Only the first N
#                       (mandatory fields first) are generated. 0 = no cap.
APM_ENABLED = os.getenv("APM_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
try:
    MAX_FIELDS_PER_SCREEN = int(os.getenv("MAX_FIELDS_PER_SCREEN", "40"))
except ValueError:
    MAX_FIELDS_PER_SCREEN = 40

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

# Register store routers
app.include_router(profiles.router)
app.include_router(targets.router)
app.include_router(workspaces.router)
app.include_router(schedules.router)
app.include_router(approvals.router)
app.include_router(masking.router)
app.include_router(dimensions.router)
app.include_router(dashboard.router)
app.include_router(kms.router)


@app.on_event("startup")
async def on_startup():
    await init_pool()
    await run_migrations()
    # give every existing profile a database (in the background, so startup is not held up)
    app.state.backfill_task = asyncio.create_task(profile_db.backfill_profile_dbs())


@app.on_event("shutdown")
async def on_shutdown():
    await profile_db.close_all_pools()
    await close_pool()


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
# Generate
# ---------------------------------------------------------------------

async def _resolve_feature_screens(features: list) -> list[dict]:
    """
    For each requested feature, for EACH of its data_templates: resolve
    that dataTemplate to a real {module, screen} (service.feature_resolver).

    If the match came from KMS AND that KMS record carries a fields list,
    use it directly (via classify_fields_from_names) and skip
    architecture_agent/schema_extraction entirely for that screen -- KMS's
    fields are used as PRIMARY when available, per the agreed direction.
    Otherwise (manual_mapping match, or a KMS match with no fields data)
    fall back to the original flow: architecture_agent resolves the real
    component file, schema_extraction parses its actual HTML.

    One feature can therefore produce several results, one per
    data_template, and different data_templates within the same feature
    can take different paths (one from KMS fields, another via real source
    parsing) independently.
    """
    out: list[dict] = []
    for feat in features:
        for data_template in feat.data_templates:
            mapping = await feature_resolver.resolve_feature_screen(data_template)
            if not mapping.get("ok"):
                out.append({
                    "ok": False, "feature": feat, "data_template": data_template,
                    "screen_name": data_template,
                    "error": mapping["error"],
                })
                continue

            module, apm_module, screen = mapping["module"], mapping["apm_module"], mapping["screen"]
            screen_id = mapping.get("screen_id")
            kms_fields = mapping.get("kms_fields")

            if kms_fields:
                out.append({
                    "ok": True, "feature": feat, "data_template": data_template,
                    "module": module, "apm_module": apm_module, "screen_id": screen_id, "screen_name": screen,
                    "resolved_via": mapping["resolved_via"],
                    "fields_source": "kms",
                    "path": None,
                    "fields": classify_fields(kms_fields),
                })
                continue

            # GitLab is only the fallback for screens KMS has no fields for. If it is unreachable or
            # misconfigured, that ONE screen fails with a clear message -- not the whole request (500).
            try:
                resolved = await architecture_agent.resolve_screen(module, screen)
            except Exception as e:
                logger.warning("feature_resolver: GitLab fallback failed for %r (%s)", screen, e)
                out.append({
                    "ok": False, "feature": feat, "data_template": data_template, "screen_name": screen,
                    "error": (
                        f"KMS has no field data for '{screen}' (screen id {screen_id}), and the GitLab fallback failed: {e}. "
                        "Check SOURCE_GITLAB_TOKEN, SOURCE_GITLAB_PROJECT_ID and SOURCE_GITLAB_BRANCH in .env."
                    ),
                })
                continue
            if not resolved.get("ok"):
                out.append({"ok": False, "feature": feat, "data_template": data_template, "screen_name": screen, "error": resolved.get("error", "Resolution failed")})
                continue

            out.append({**resolved, "ok": True, "feature": feat, "data_template": data_template, "module": module, "apm_module": apm_module, "screen_id": screen_id, "resolved_via": mapping["resolved_via"], "fields_source": "architecture_agent"})
    return out


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    started = time.monotonic()  # real generation time, stored with the version
    domain_value = request.domain
    subdomain_value = request.subdomain or None
    # Raw, as-typed geography text, resolved to a real place by the first
    # screen's generation call and reused by every screen after that in
    # this same request (see Agents/data_generator_agent.py).
    raw_geography = request.geography

    resolved = await _resolve_feature_screens(request.features)

    # Phase 1: schema extraction (cached, deterministic) + APM schema
    # resolution (cached) for every successfully resolved feature/screen.
    screen_contexts: list[dict] = []
    for r in resolved:
        feat = r["feature"]
        if not r.get("ok"):
            screen_contexts.append({
                "ok": False, "feature": feat, "data_template": r["data_template"], "screen_name": r["screen_name"], "error": r["error"],
            })
            continue

        if r["fields_source"] == "kms":
            fields = r["fields"]  # already classified in _resolve_feature_screens
        else:
            fields = schema_extraction.extract_fields(r["path"], r["component_html"])

        if not fields:
            screen_contexts.append({"ok": False, "feature": feat, "data_template": r["data_template"], "screen_name": r["screen_name"], "error": "No form fields found on this screen."})
            continue

        if MAX_FIELDS_PER_SCREEN > 0 and len(fields) > MAX_FIELDS_PER_SCREEN:
            logger.info(
                "generate: %s has %d fields -- using the first %d (MAX_FIELDS_PER_SCREEN)",
                r["screen_name"], len(fields), MAX_FIELDS_PER_SCREEN,
            )
            fields = fields[:MAX_FIELDS_PER_SCREEN]

        # APM gets the APM-side module name and, as alternates for matching the Save endpoint,
        # the KMS screen id's tail (e.g. "ACCOUNTS_MACCOUNT" -> "MACCOUNT") besides the display name.
        sid = r.get("screen_id") or ""
        aliases = [sid.split("_", 1)[1]] if "_" in sid else ([sid] if sid else [])
        if APM_ENABLED:
            apm_resolution = await resolve_for_generation(r["apm_module"], r["screen_name"], aliases)
        else:
            apm_resolution = {"ok": False, "error": "APM is switched off (APM_ENABLED=false) -- preview only."}

        screen_contexts.append({
            "ok": True,
            "feature": feat,
            "data_template": r["data_template"],
            "module": r["module"],
            "apm_module": r["apm_module"],
            "screen_id": r.get("screen_id"),
            "screen_name": r["screen_name"],
            "resolved_path": r.get("path"),
            "resolved_via": r["resolved_via"],
            "fields_source": r["fields_source"],
            "fields": fields,
            "apm_resolution": apm_resolution,
        })

    if not any(c["ok"] for c in screen_contexts):
        first_error = next((c["error"] for c in screen_contexts if not c["ok"]), "Generation failed.")
        raise HTTPException(status_code=422, detail=first_error)

    # Phase 2: entity-assignment consistency, grouped per resolved module
    # (same picklist-consistency goal as before -- a feature's picklists
    # should agree with other features resolved into the same module).
    entity_assignment_maps: dict[str, dict] = {}
    modules_present = sorted({c["module"] for c in screen_contexts if c["ok"]})
    for module in modules_present:
        picklist_field_names = sorted({
            f["field_name"]
            for c in screen_contexts if c["ok"] and c["module"] == module
            for f in c["fields"] if f.get("kind") == "picklist"
        })
        entity_assignment_maps[module] = await build_entity_assignment_map(
            module=module,
            domain=domain_value,
            subdomain=subdomain_value,
            geography=raw_geography,
            picklist_field_names=picklist_field_names,
        )

    # Phase 3: per-screen row generation. geography starts as the raw
    # typed text; the first screen to run resolves it to a real place and
    # every screen after that (across every feature in this run, not just
    # within one module) reuses that resolved value.
    geography_for_next_screen = raw_geography
    geography_resolved_yet = False

    results: list[ScreenResult] = []
    for ctx in screen_contexts:
        feat = ctx["feature"]
        if not ctx["ok"]:
            results.append(ScreenResult(
                status="error", message=ctx["error"],
                feature_id=feat.id, feature_name=feat.name, data_template=ctx["data_template"],
                screen=ctx["screen_name"],
            ))
            continue

        apm_resolution = ctx["apm_resolution"]
        apm_ready = bool(apm_resolution.get("ok"))

        initial_state = {
            "module": ctx["module"],
            "screen": ctx["screen_name"],
            "domain": domain_value,
            "subdomain": subdomain_value,
            "geography": geography_for_next_screen,
            "geography_already_resolved": geography_resolved_yet,
            "row_count": feat.row_count or request.row_count,
            "generated_fields": ctx["fields"],
            "apm_type_hints": apm_resolution.get("scalar_fields", {}) if apm_ready else {},
            "entity_assignment_map": entity_assignment_maps.get(ctx["module"], {}),
        }

        try:
            final_state = await generator_graph.ainvoke(initial_state)
        except Exception as e:
            logger.exception("Generation pipeline crashed for feature=%r (%s/%s)", feat.id, ctx["module"], ctx["screen_name"])
            results.append(ScreenResult(
                status="error", message=str(e),
                feature_id=feat.id, feature_name=feat.name, data_template=ctx["data_template"],
                screen=ctx["screen_name"],
            ))
            continue

        if final_state.get("generation_error"):
            results.append(ScreenResult(
                status="error", message=final_state["generation_error"],
                feature_id=feat.id, feature_name=feat.name, data_template=ctx["data_template"],
                screen=ctx["screen_name"],
            ))
            continue

        if final_state.get("resolved_geography"):
            geography_for_next_screen = final_state["resolved_geography"]
            geography_resolved_yet = True

        rows = final_state.get("generated_rows") or []
        results.append(ScreenResult(
            status="ok",
            feature_id=feat.id, feature_name=feat.name, data_template=ctx["data_template"],
            resolved_via=ctx["resolved_via"],
            fields_source=ctx["fields_source"],
            screen=ctx["screen_name"],
            module=ctx["apm_module"],
            kms_module=ctx["module"],
            screen_id=ctx.get("screen_id"),
            resolved_path=ctx["resolved_path"],
            fields=ctx["fields"],
            rows=rows,
            row_count=len(rows),
            apm_ready=apm_ready,
            apm_disabled_reason=None if apm_ready else apm_resolution.get("error"),
            resolved_geography=final_state.get("resolved_geography"),
        ))

    # Store this run as the next version in the profile's own database. Best effort: never fails the request.
    meta = {
        "top_up": request.profile.top_up,
        "domain": domain_value, "subdomain": subdomain_value, "geography": raw_geography,
        "resolved_geography": next((r.resolved_geography for r in results if r.resolved_geography), None),
        "catalog_version": request.profile.catalog_version, "mode": request.profile.mode, "seed": request.profile.seed,
        "target_id": request.profile.target_id, "target_name": request.profile.target_name,
        "row_count": request.row_count,
        "duration_seconds": round(time.monotonic() - started, 1),
    }
    storage = await run_store.save_run(request.profile.id, meta, results)

    return GenerateResponse(profile_id=request.profile.id, results=results, storage=StorageInfo(**storage))


# ---------------------------------------------------------------------
# APM save — explicit, per-row, opt-in
# ---------------------------------------------------------------------

@app.post("/save-row", response_model=SaveRowResponse)
async def save_row(request: SaveRowRequest, raw_request: Request):
    login_header = raw_request.headers.get("Login", "")
    if not login_header.strip():
        raise HTTPException(status_code=400, detail="A Login header is required to save to APM.")

    # The header itself is forwarded to APM unchanged; here we only READ it to fill system fields
    # and to catch a malformed paste early with a clear message.
    login, login_problem = parse_login(login_header)
    if login is None:
        return SaveRowResponse(ok=False, error=login_problem)

    resolution = await resolve_apm_schema(request.module, request.screen)
    if not resolution.get("ok"):
        return SaveRowResponse(ok=False, error=resolution.get("error"), login_warnings=login_warnings(login))

    mapping = await map_row_to_schema(request.row, resolution["scalar_fields"], request.screen, login)
    result = await execute_save(resolution["path_prefix"], resolution["endpoint_path"], login_header, mapping["body"])

    logger.info(
        "save-row: screen=%r db=%r user=%r -> %s ok=%s",
        request.screen, login.get("DatabaseName"), login.get("UserCode"), result.get("url"), result["ok"],
    )

    return SaveRowResponse(
        ok=result["ok"], status_code=result["status_code"],
        error=result["error"], detail=result.get("detail"), url=result.get("url"),
        response=result["response"],
        missing_required_fields=mapping["missing_required_fields"],
        unmatched_apm_fields=mapping["unmatched_apm_fields"],
        unmatched_frontend_fields=mapping["unmatched_frontend_fields"],
        filled_from_login=mapping.get("filled_from_login", []),
        login_warnings=login_warnings(login),
    )