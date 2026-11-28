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
    LogEntry,
    PlanNotice,
    PlanRequest,
    PlanResponse,
    PlannedScreen,
    ResolvedFeature,
    ScreenResult,
    SaveRowRequest,
    SaveRowResponse,
    StorageInfo,
)
from workflow.graph import generator_graph
from service.apm_client import execute_save
from service.apm_session import parse_login, login_warnings
from service import feature_resolver, relationship_planner
from service.postgres_client import init_pool, close_pool
from service import profile_db, run_store, kms_catalog
from service.run_log import RunLog, plain_error
from Agents import architecture_agent
from Agents.apm_resolver_agent import resolve_apm_schema
from Agents.data_generator_agent import build_entity_assignment_map
from steps import schema_extraction
from tools.schema_parser import classify_fields
from steps.apm_schema_resolution import resolve_for_generation
from tools.row_mapper import map_row_to_schema
from localedata.domain import DOMAIN_LIST
from localedata import apm_modules
from service.table_setup import run_migrations
from routers import profiles, targets, dimensions, dashboard, kms

load_dotenv()

# --- Generation-only controls (all optional, set in .env) -------------------
# APM_ENABLED=false  -> skip every APM / swagger / foreign-key lookup. Generation
#                       then needs only KMS (or GitLab) + OpenAI. Push-to-APM is off.
# MAX_FIELDS_PER_SCREEN -> some KMS screens hold hundreds of fields. Only the first N
#                       (mandatory fields first) are generated. 0 = no cap. When a screen is
#                       capped, the response says so (fields_total / fields_capped) and the
#                       Generator page shows a notice.
APM_ENABLED = os.getenv("APM_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
try:
    MAX_FIELDS_PER_SCREEN = int(os.getenv("MAX_FIELDS_PER_SCREEN", "0"))
except ValueError:
    MAX_FIELDS_PER_SCREEN = 0

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

# Only the frontend's own address may call this API. Set CORS_ORIGINS in .env (comma separated) for any other.
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:8080,http://127.0.0.1:8080,http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Register store routers
app.include_router(profiles.router)
app.include_router(targets.router)
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


@app.get("/meta/apm", summary="Which APM host rows are pushed to (read from .env) and whether pushing is switched on")
def get_apm_info() -> dict:
    try:
        host = apm_modules._apm_api_host()
    except RuntimeError:
        host = None  # APM_DOC_HOST / APM_API_HOST not set in .env
    return {"enabled": APM_ENABLED, "host": host}


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


async def _build_contexts(resolved: list[dict]) -> list[dict]:
    # Schema extraction (cached, deterministic) + APM schema resolution (cached) for every
    # successfully resolved feature/screen.
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

        fields_total = len(fields)
        fields_capped = MAX_FIELDS_PER_SCREEN > 0 and fields_total > MAX_FIELDS_PER_SCREEN
        if fields_capped:
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
            "fields_total": fields_total,
            "fields_capped": fields_capped,
            "apm_resolution": apm_resolution,
        })
    return screen_contexts


def _up(v) -> str:
    return str(v or "").strip().upper()


async def _plan_relationships(request: GenerateRequest, screen_contexts: list[dict]) -> dict:
    """
    Phase 1b. Works out how the selected screens relate (service/relationship_planner.py):
      - parents that are missing from the run are resolved and added (unless auto_add_parents is off);
      - parents that already have data in the profile's current version are reused instead;
      - the screens are ordered parents first.
    Never fails the request: on any problem the screens simply run in the order given, as before.
    """
    info = {"contexts": screen_contexts, "plan": None, "stored": {}}
    try:
        ok_ids = [c["screen_id"] for c in screen_contexts if c["ok"] and c.get("screen_id")]
        if not ok_ids:
            return info
        stored = await run_store.stored_screen_versions(request.profile.id)
        plan = await relationship_planner.build_plan(ok_ids, auto_add=request.auto_add_parents, stored=stored)

        # resolve and add the missing parent screens
        if plan["auto_add"]:
            needed_by: dict[str, list[str]] = {}
            for l in plan["links"]:
                if l["status"] in ("auto_added",) and l["parent"] in plan["auto_add"]:
                    needed_by.setdefault(l["parent"], []).append(plan["names"].get(l["child"], l["child"]))
            parent_rows = min(request.row_count or 10, 20)
            extra = [
                ResolvedFeature(id=sid, name=plan["names"].get(sid, sid), data_templates=[sid], row_count=parent_rows)
                for sid in plan["auto_add"]
            ]
            new_contexts = await _build_contexts(await _resolve_feature_screens(extra))
            for c in new_contexts:
                c["auto_added"] = True
                c["auto_added_for"] = sorted(set(needed_by.get(_up(c.get("screen_id")), [])))
            screen_contexts = screen_contexts + new_contexts
            logger.info("generate: added %d parent screen(s) first: %s", len(new_contexts),
                        ", ".join(plan["names"].get(s, s) for s in plan["auto_add"]))

        # parents first (screens without a screen id, i.e. failed ones, keep their place at the end)
        position = {sid: i for i, sid in enumerate(plan["order"])}
        screen_contexts = sorted(screen_contexts, key=lambda c: position.get(_up(c.get("screen_id")), len(position)))
        info.update({"contexts": screen_contexts, "plan": plan, "stored": stored})
    except Exception:  # noqa: BLE001
        logger.exception("generate: relationship planning failed -- generating screens independently")
    return info


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    started = time.monotonic()  # real generation time, stored with the version
    domain_value = request.domain
    subdomain_value = request.subdomain or None
    # Raw, as-typed geography text, resolved to a real place by the first
    # screen's generation call and reused by every screen after that in
    # this same request (see Agents/data_generator_agent.py).
    raw_geography = request.geography

    logger.info(
        "generate: profile=%s mode=%s screens requested=%d",
        request.profile.id, request.profile.mode, sum(len(f.data_templates) for f in request.features),
    )

    log = RunLog()
    mode_label = {"full": "Full", "partial": "Partial", "add": "Add to existing"}.get(request.profile.mode or "", "Partial")
    n_requested = sum(len(f.data_templates) for f in request.features)
    log.info(
        f"{mode_label} run for profile '{request.profile.name}': {n_requested} screen(s) requested"
        + (f", target {request.profile.target_name}" if request.profile.target_name else "")
        + (f", geography '{raw_geography}'" if raw_geography else "")
        + (f", domain '{domain_value}'" if domain_value else "")
        + (". New rows will be added to the current version." if request.profile.mode == "add" else ".")
    )
    if not APM_ENABLED:
        log.info("APM is switched off (APM_ENABLED=false): rows are previews and cannot be pushed.")

    resolved = await _resolve_feature_screens(request.features)

    screen_contexts = await _build_contexts(resolved)

    # Phase 1b: relationships -- order parents first, add missing parents, find parent data to reuse
    rel = await _plan_relationships(request, screen_contexts)
    screen_contexts = rel["contexts"]

    for c in screen_contexts:
        name = c["screen_name"]
        if not c["ok"]:
            log.error(f"Could not prepare this screen: {plain_error(c['error'])}", name)
            continue
        src = {"kms": "KMS", "architecture_agent": "the screen's source code (GitLab)"}.get(c["fields_source"], c["fields_source"])
        log.info(f"Resolved to module {c['module']} (APM module '{c['apm_module']}'); {c['fields_total']} field(s) read from {src}.", name)
        if c.get("fields_capped"):
            log.warning(f"The screen has {c['fields_total']} fields; only the first {len(c['fields'])} are generated (MAX_FIELDS_PER_SCREEN).", name)
        res = c["apm_resolution"]
        if res.get("ok"):
            log.info("APM is ready for this screen: rows can be pushed.", name)
        else:
            log.warning(f"APM is not ready for this screen, so rows cannot be pushed: {res.get('error') or 'reason not given'}", name)
        if c.get("auto_added"):
            who = ", ".join(c.get("auto_added_for") or []) or "another screen"
            log.info(f"Added automatically because {who} needs it; generated before it.", name)
    plan_info = rel["plan"]
    if plan_info:
        reused = [l for l in plan_info["links"] if l["status"] == "reused"]
        for parent in sorted({l["parent"] for l in reused}):
            log.info(f"Reusing {plan_info['names'].get(parent, parent)} from v{rel['stored'].get(parent, '?')} (already stored); not generated again.")
        log.info("Generation order (parents first): " + " \u2192 ".join(plan_info["names"].get(s, s) for s in plan_info["order"]) + ".")
    elif any(c["ok"] for c in screen_contexts):
        log.warning("Related screens could not be worked out; screens are generated independently, in the order given.")

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
        if picklist_field_names:
            n_pools = len(entity_assignment_maps[module])
            if n_pools:
                log.info(f"Module {module}: fixed value lists agreed for {n_pools} dropdown field(s) so screens use the same names.")
            else:
                log.warning(f"Module {module}: could not agree shared values for its dropdown fields; each screen picks its own.")

    # Phase 3: per-screen row generation. geography starts as the raw
    # typed text; the first screen to run resolves it to a real place and
    # every screen after that (across every feature in this run, not just
    # within one module) reuses that resolved value.
    geography_for_next_screen = raw_geography
    geography_resolved_yet = False

    # Relationship-based generation: rows of screens finished so far (and parent data reused from the stored
    # version) are the only allowed values for the child fields that point at them.
    plan = rel["plan"]
    sources: dict[str, dict] = {}
    owners: dict[str, str] = {}
    if plan:
        try:
            owners = await relationship_planner.table_owners()
            reused_ids = [l["parent"] for l in plan["links"] if l["status"] == "reused"]
            sources.update(await run_store.stored_screen_data(request.profile.id, reused_ids))
        except Exception:  # noqa: BLE001
            logger.exception("generate: could not load parent data -- screens will not be linked")

    results: list[ScreenResult] = []
    for ctx in screen_contexts:
        feat = ctx["feature"]
        if not ctx["ok"]:
            results.append(ScreenResult(
                status="error", message=plain_error(ctx["error"]),
                feature_id=feat.id, feature_name=feat.name, data_template=ctx["data_template"],
                screen=ctx["screen_name"],
            ))
            continue

        apm_resolution = ctx["apm_resolution"]
        apm_ready = bool(apm_resolution.get("ok"))

        parent_values: dict[str, list] = {}
        notes: list[str] = []
        if plan:
            parent_values, field_notes = relationship_planner.parent_values_for(ctx["fields"], ctx.get("screen_id") or "", sources, owners)
            notes = relationship_planner.child_notes(plan, ctx.get("screen_id") or "", rel["stored"]) + field_notes

        sname = ctx["screen_name"]
        want_rows = feat.row_count or request.row_count or 20
        for n in notes:
            log.info(n, sname)
        log.info(f"Generating {want_rows} row(s)" + (f" using {len(parent_values)} linked field(s)" if parent_values else "") + ".", sname)
        screen_started = time.monotonic()

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
            "parent_values": parent_values,
        }

        try:
            final_state = await generator_graph.ainvoke(initial_state)
        except Exception as e:
            logger.exception("Generation pipeline crashed for feature=%r (%s/%s)", feat.id, ctx["module"], ctx["screen_name"])
            log.error(f"Generation failed: {plain_error(e)}", sname)
            results.append(ScreenResult(
                status="error", message=plain_error(e),
                feature_id=feat.id, feature_name=feat.name, data_template=ctx["data_template"],
                screen=ctx["screen_name"],
            ))
            continue

        if final_state.get("generation_error"):
            log.error(f"Generation failed: {plain_error(final_state['generation_error'])}", sname)
            results.append(ScreenResult(
                status="error", message=plain_error(final_state["generation_error"]),
                feature_id=feat.id, feature_name=feat.name, data_template=ctx["data_template"],
                screen=ctx["screen_name"],
            ))
            continue

        if final_state.get("resolved_geography"):
            if not geography_resolved_yet:
                log.info(f"Geography '{raw_geography}' resolved to '{final_state['resolved_geography']}'; every later screen uses it.", sname)
            geography_for_next_screen = final_state["resolved_geography"]
            geography_resolved_yet = True

        rows = final_state.get("generated_rows") or []
        if parent_values:
            replaced = relationship_planner.enforce_parent_values(rows, parent_values)
            if replaced:
                msg = f"{replaced} value(s) that did not come from a parent screen were replaced with parent values."
                notes.append(msg)
                log.warning(msg, sname)
        batches = final_state.get("generation_batches") or []
        took = round(time.monotonic() - screen_started, 1)
        if len(rows) < want_rows:
            log.warning(f"Only {len(rows)} of {want_rows} requested row(s) were produced (batches: {', '.join(map(str, batches)) or 'none'}).", sname)
        else:
            log.info(f"Generated {len(rows)} row(s) in {len(batches)} batch(es) ({', '.join(map(str, batches))}) in {took}s.", sname)
        if final_state.get("dedup_renamed"):
            log.info(f"{final_state['dedup_renamed']} duplicate identifier value(s) were renamed to keep them unique.", sname)
        if plan and ctx.get("screen_id"):
            sources[_up(ctx["screen_id"])] = {"name": ctx["screen_name"], "fields": ctx["fields"], "rows": rows}
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
            fields_total=ctx.get("fields_total"),
            fields_capped=bool(ctx.get("fields_capped")),
            apm_ready=apm_ready,
            apm_disabled_reason=None if apm_ready else apm_resolution.get("error"),
            resolved_geography=final_state.get("resolved_geography"),
            auto_added=bool(ctx.get("auto_added")),
            auto_added_for=ctx.get("auto_added_for") or [],
            relationship_notes=notes,
        ))

    # Store this run as the next version in the profile's own database. Best effort: never fails the request.
    meta = {
        "top_up": request.profile.top_up,
        "mode": request.profile.mode,
        "domain": domain_value, "subdomain": subdomain_value, "geography": raw_geography,
        "resolved_geography": next((r.resolved_geography for r in results if r.resolved_geography), None),
        "catalog_version": request.profile.catalog_version,
        "target_id": request.profile.target_id, "target_name": request.profile.target_name,
        "row_count": request.row_count,
        "duration_seconds": round(time.monotonic() - started, 1),
    }
    ok_n = sum(1 for r in results if r.status == "ok")
    total_rows = sum(r.row_count for r in results)
    log.info(f"Finished: {ok_n} of {len(results)} screen(s) generated, {total_rows} row(s), in {meta['duration_seconds']}s. Storing in the profile database.")
    meta["logs"] = list(log.entries)
    storage = await run_store.save_run(request.profile.id, meta, results)
    if storage.get("saved"):
        log.info(
            f"Stored as v{storage.get('version_no')}" + (" (rows added to the existing version)" if storage.get("appended") else "")
            + f" in database {storage.get('db_name')}: {storage.get('rows_saved')} row(s)."
        )
    else:
        log.error(f"The rows were generated but NOT stored in the profile database: {storage.get('error') or 'no reason given'}")

    return GenerateResponse(
        profile_id=request.profile.id, results=results, storage=StorageInfo(**storage),
        logs=[LogEntry(**e) for e in log.entries],
    )


# ---------------------------------------------------------------------
# Plan -- what Generate will do about related screens (no generation, no LLM)
# ---------------------------------------------------------------------

@app.post("/plan", response_model=PlanResponse)
async def plan_generation(request: PlanRequest):
    """Fast, in-memory check run before generating: which selected screens need which parent screens, which parents
    will be generated first or reused from stored data, and which cannot be provided."""
    unresolved: list[str] = []
    screen_ids: list[str] = []
    for feat in request.features:
        for template in feat.data_templates:
            hit = await kms_catalog.lookup_screen(template)
            if hit is None or hit.get("generatable") is False:
                unresolved.append(template)
            elif hit["screen_id"] not in screen_ids:
                screen_ids.append(hit["screen_id"])
    if not screen_ids:
        return PlanResponse(unresolved=unresolved)

    stored = await run_store.stored_screen_versions(request.profile_id) if request.profile_id else {}
    plan = await relationship_planner.build_plan(screen_ids, auto_add=request.auto_add_parents, stored=stored)
    added = set(plan["auto_add"])
    order = [PlannedScreen(screen_id=s, name=plan["names"].get(s, s), auto_added=s in added) for s in plan["order"]]
    return PlanResponse(
        notices=[PlanNotice(**n) for n in relationship_planner.describe_plan(plan, stored)],
        order=order,
        auto_added=[p for p in order if p.auto_added],
        unresolved=unresolved,
    )


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