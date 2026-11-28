"""
Stores each Generate as a VERSION in the profile's own database (service/profile_db.py).

  - a normal Generate creates the next version (v1, v2, ...) and marks it current;
  - "Top up" appends to the current version instead (same screen -> more rows, new screen -> new entry);
  - screens that failed are stored too (status 'error' + message), so history shows what happened.

Storing is best effort: generation must never fail because storage did. save_run() never raises; it returns a
small dict describing what happened, which /generate passes back to the UI.
"""

import json
import logging
from datetime import datetime, timezone

import asyncpg

from service import profile_db, profiles_service
from service.postgres_client import get_pool

logger = logging.getLogger(__name__)

_ADVISORY_LOCK_KEY = 7_411_001  # serialises version numbering within one profile database


async def _save(conn: asyncpg.Connection, meta: dict, results: list) -> dict:
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _ADVISORY_LOCK_KEY)

        current = None
        if meta.get("top_up"):
            current = await conn.fetchrow(
                "SELECT id, version_no FROM dd_runs WHERE is_current ORDER BY version_no DESC LIMIT 1"
            )

        appended = current is not None
        if appended:
            run_id, version_no = current["id"], current["version_no"]
            # A top-up adds its own generation time to the version's total (only when that version has one).
            await conn.execute(
                """
                UPDATE dd_runs
                SET finished_at = NOW(),
                    params = CASE WHEN params ? 'duration_seconds'
                                  THEN jsonb_set(params, '{duration_seconds}',
                                                 to_jsonb((params->>'duration_seconds')::float + $2::float))
                                  ELSE params END
                WHERE id = $1
                """,
                run_id, float(meta.get("duration_seconds") or 0),
            )
            if meta.get("logs"):  # a top-up keeps the earlier logs and adds its own
                await conn.execute(
                    "UPDATE dd_runs SET params = jsonb_set(params, '{logs}', COALESCE(params->'logs', '[]'::jsonb) || $2::jsonb) WHERE id = $1",
                    run_id, meta["logs"],
                )
        else:
            version_no = (await conn.fetchval("SELECT COALESCE(MAX(version_no), 0) + 1 FROM dd_runs")) or 1
            await conn.execute("UPDATE dd_runs SET is_current = FALSE WHERE is_current")
            run_id = await conn.fetchval(
                """
                INSERT INTO dd_runs (version_no, status, is_current, top_up, domain, subdomain, geography,
                                     resolved_geography, catalog_version, mode, seed, target_id, target_name,
                                     row_count_requested, params, finished_at)
                VALUES ($1, 'completed', TRUE, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, NOW())
                RETURNING id
                """,
                version_no, bool(meta.get("top_up")), meta.get("domain"), meta.get("subdomain"), meta.get("geography"),
                meta.get("resolved_geography"), meta.get("catalog_version"), meta.get("mode"), meta.get("seed"),
                meta.get("target_id"), meta.get("target_name"), meta.get("row_count"),
                {
                    "features": [r.feature_id for r in results if r.feature_id],
                    # Real wall-clock time of the request. started_at/finished_at cannot show this: both come from
                    # NOW(), which Postgres freezes at the start of the transaction.
                    "duration_seconds": meta.get("duration_seconds"),
                    "logs": meta.get("logs") or [],
                },
            )

        rows_saved = 0
        for r in results:
            rows = r.rows or []
            screen_row = None
            if appended and r.status == "ok":
                screen_row = await conn.fetchrow(
                    "SELECT id, row_count FROM dd_run_screens WHERE run_id = $1 AND screen = $2 AND status = 'ok' LIMIT 1",
                    run_id, r.screen,
                )

            if screen_row is not None:
                run_screen_id = screen_row["id"]
                start_index = (await conn.fetchval(
                    "SELECT COALESCE(MAX(row_index), -1) + 1 FROM dd_rows WHERE run_screen_id = $1", run_screen_id
                )) or 0
                await conn.execute(
                    "UPDATE dd_run_screens SET row_count = row_count + $2, fields = $3::jsonb WHERE id = $1",
                    run_screen_id, len(rows), r.fields or [],
                )
            else:
                start_index = 0
                run_screen_id = await conn.fetchval(
                    """
                    INSERT INTO dd_run_screens (run_id, feature_id, feature_name, data_template, screen, screen_id, module,
                                                kms_module, resolved_via, fields_source, fields, status, message, apm_ready, row_count)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,$15)
                    RETURNING id
                    """,
                    run_id, r.feature_id, r.feature_name, r.data_template, r.screen, r.screen_id, r.module,
                    r.kms_module, r.resolved_via, r.fields_source, r.fields or [], r.status, r.message,
                    bool(r.apm_ready), len(rows),
                )

            if rows:
                await conn.executemany(
                    "INSERT INTO dd_rows (run_screen_id, run_id, screen, row_index, data) VALUES ($1, $2, $3, $4, $5::jsonb)",
                    [(run_screen_id, run_id, r.screen, start_index + i, row) for i, row in enumerate(rows)],
                )
                rows_saved += len(rows)

    return {"run_id": run_id, "version_no": version_no, "appended": appended, "rows_saved": rows_saved}


async def save_run(profile_id: str, meta: dict, results: list) -> dict:
    """Persist one /generate outcome. Never raises. Returns
    {"saved": bool, "version_no", "run_id", "appended", "rows_saved", "db_name", "error"}."""
    info: dict = {"saved": False, "version_no": None, "run_id": None, "appended": False, "rows_saved": 0, "db_name": None, "error": None}
    try:
        profile = await profiles_service.get_profile(profile_id)
        if profile is None:
            info["error"] = "This profile is not saved in the backend yet, so nothing was stored. Save the profile first."
            return info

        if profile.get("db_status") != "ready":
            ensured = await profile_db.ensure_profile_db(profile_id)
            if ensured["db_status"] != "ready":
                info["error"] = f"The profile database is not ready: {ensured.get('db_error')}"
                return info
            profile = await profiles_service.get_profile(profile_id) or profile

        db_name = profile.get("db_name") or profile_db.db_name_for(profile_id)
        info["db_name"] = db_name

        for attempt in (1, 2):
            try:
                pool = await profile_db.get_profile_pool(db_name)
                async with pool.acquire() as conn:
                    saved = await _save(conn, meta, results)
                break
            except asyncpg.InvalidCatalogNameError:
                # the database was removed behind our back: rebuild it once, then retry
                await profile_db.close_profile_pool(db_name)
                if attempt == 2:
                    raise
                await profile_db.ensure_profile_db(profile_id)

        info.update(saved)
        info["saved"] = True

        cp = get_pool()
        async with cp.acquire() as conn:
            await conn.execute("UPDATE ddpa_profiles SET current_version = $2 WHERE id = $1", profile_id, saved["version_no"])
        logger.info("run_store: profile %s -> v%s (%s rows, appended=%s) in %s",
                    profile_id, saved["version_no"], saved["rows_saved"], saved["appended"], db_name)
    except Exception as e:  # noqa: BLE001
        logger.exception("run_store: could not store the run for profile %s", profile_id)
        info["error"] = f"{type(e).__name__}: {e}"[:400]
    return info


def _as_json(v):
    return json.loads(v) if isinstance(v, (str, bytes)) else v


async def _profile_pool(profile_id: str):
    profile = await profiles_service.get_profile(profile_id)
    if not profile or profile.get("db_status") != "ready":
        return None
    return await profile_db.get_profile_pool(profile.get("db_name") or profile_db.db_name_for(profile_id))


async def stored_screen_info(profile_id: str) -> dict[str, dict]:
    """{SCREEN_ID (upper-case): {"version_no", "row_count"}} for every screen that already has rows in the profile's
    CURRENT version. Used by relationship-based generation to reuse parent data instead of generating it again,
    and by the "Checks before you generate" list to say how many rows will be reused. Never raises."""
    try:
        pool = await _profile_pool(profile_id)
        if pool is None:
            return {}
        async with pool.acquire() as conn:
            found = await conn.fetch(
                """
                SELECT UPPER(s.screen_id) AS sid, r.version_no, s.row_count
                FROM dd_run_screens s JOIN dd_runs r ON r.id = s.run_id
                WHERE r.is_current AND s.status = 'ok' AND s.row_count > 0 AND s.screen_id IS NOT NULL
                """
            )
        return {r["sid"]: {"version_no": r["version_no"], "row_count": int(r["row_count"] or 0)} for r in found}
    except Exception:  # noqa: BLE001
        logger.exception("run_store: could not read stored screens for profile %s", profile_id)
        return {}


async def stored_screen_versions(profile_id: str) -> dict[str, int]:
    """{SCREEN_ID (upper-case): version_no} -- the version part of stored_screen_info(). Never raises."""
    return {sid: info["version_no"] for sid, info in (await stored_screen_info(profile_id)).items()}


async def stored_screen_data(profile_id: str, screen_ids: list[str], max_rows: int = 200) -> dict[str, dict]:
    """{SCREEN_ID (upper-case): {"name", "fields", "rows", "version_no", plus the screen's stored details}} from the
    profile's CURRENT version, for the given screens (at most max_rows rows each). Never raises."""
    out: dict[str, dict] = {}
    ids = sorted({str(i).upper() for i in screen_ids if i})
    if not ids:
        return out
    try:
        pool = await _profile_pool(profile_id)
        if pool is None:
            return out
        async with pool.acquire() as conn:
            screens = await conn.fetch(
                """
                SELECT s.id, UPPER(s.screen_id) AS sid, s.screen_id, s.screen, s.fields, s.feature_id, s.feature_name,
                       s.data_template, s.module, s.kms_module, s.resolved_via, s.fields_source, s.apm_ready, r.version_no
                FROM dd_run_screens s JOIN dd_runs r ON r.id = s.run_id
                WHERE r.is_current AND s.status = 'ok' AND s.row_count > 0 AND UPPER(s.screen_id) = ANY($1::text[])
                """,
                ids,
            )
            for s in screens:
                rows = await conn.fetch(
                    "SELECT data FROM dd_rows WHERE run_screen_id = $1 ORDER BY row_index LIMIT $2", s["id"], max_rows
                )
                out[s["sid"]] = {
                    "name": s["screen"],
                    "fields": _as_json(s["fields"]) or [],
                    "rows": [_as_json(r["data"]) for r in rows],
                    "version_no": s["version_no"],
                    "screen_id": s["screen_id"], "feature_id": s["feature_id"], "feature_name": s["feature_name"],
                    "data_template": s["data_template"], "module": s["module"], "kms_module": s["kms_module"],
                    "resolved_via": s["resolved_via"], "fields_source": s["fields_source"], "apm_ready": bool(s["apm_ready"]),
                }
    except Exception:  # noqa: BLE001
        logger.exception("run_store: could not read stored rows for profile %s", profile_id)
    return out


_RUNS_SQL = """
SELECT r.id, r.version_no, r.is_current, r.top_up, r.domain, r.subdomain, r.geography, r.resolved_geography,
       r.catalog_version, r.mode, r.seed, r.target_name, r.note, r.started_at, r.finished_at,
       (r.params->>'duration_seconds')::float AS duration_seconds,
       COUNT(s.id) AS screens,
       COUNT(s.id) FILTER (WHERE s.status = 'ok') AS ok_screens,
       COUNT(s.id) FILTER (WHERE s.status <> 'ok') AS error_screens,
       COALESCE(SUM(s.row_count), 0) AS row_count
FROM dd_runs r LEFT JOIN dd_run_screens s ON s.run_id = r.id
GROUP BY r.id ORDER BY r.version_no DESC
"""


def _outcome(ok_screens: int, error_screens: int) -> str:
    """completed = every screen worked, partial = some failed, failed = none worked."""
    if ok_screens == 0 and error_screens == 0:
        return "empty"
    if error_screens == 0:
        return "completed"
    return "failed" if ok_screens == 0 else "partial"


def _shape(row: asyncpg.Record, profile: dict) -> dict:
    d = dict(row)
    d["row_count"] = int(d["row_count"] or 0)
    d["status"] = _outcome(d["ok_screens"], d["error_screens"])
    d["last_activity"] = d["finished_at"] or d["started_at"]
    d["profile_id"] = profile["id"]
    d["profile_name"] = profile.get("name") or profile["id"]
    return d


async def _runs_for_profile(profile: dict) -> list[dict]:
    db_name = profile.get("db_name") or profile_db.db_name_for(profile["id"])
    pool = await profile_db.get_profile_pool(db_name)
    async with pool.acquire() as conn:
        rows = await conn.fetch(_RUNS_SQL)
    return [_shape(r, profile) for r in rows]


async def list_runs(profile_id: str) -> list[dict]:
    """Versions of one profile, newest first, with screen and row totals."""
    profile = await profiles_service.get_profile(profile_id)
    if not profile or profile.get("db_status") != "ready":
        return []
    return await _runs_for_profile(profile)


async def list_all_runs(limit: int | None = None) -> list[dict]:
    """Versions of every profile, most recently active first. A profile whose database cannot be read is skipped
    (and logged) so one broken profile never hides the others. Profiles are read one after another because only a
    few profile pools are kept open at once."""
    out: list[dict] = []
    for profile in await profiles_service.list_profiles():
        if profile.get("db_status") != "ready":
            continue
        try:
            out.extend(await _runs_for_profile(profile))
        except Exception:  # noqa: BLE001
            logger.exception("run_store: could not read the versions of profile %s", profile.get("id"))
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    out.sort(key=lambda r: r["last_activity"] or oldest, reverse=True)
    return out[:limit] if limit else out


async def get_bundle(profile_id: str, run_id: int) -> dict | None:
    """Everything stored for one version (details, screens, fields and rows) as one JSON-ready dict, or None."""
    profile = await profiles_service.get_profile(profile_id)
    if not profile or profile.get("db_status") != "ready":
        return None
    pool = await profile_db.get_profile_pool(profile.get("db_name") or profile_db.db_name_for(profile_id))
    async with pool.acquire() as conn:
        run = await conn.fetchrow("SELECT * FROM dd_runs WHERE id = $1", run_id)
        if run is None:
            return None
        screens = await conn.fetch("SELECT * FROM dd_run_screens WHERE run_id = $1 ORDER BY id", run_id)
        rows = await conn.fetch(
            "SELECT run_screen_id, data FROM dd_rows WHERE run_id = $1 ORDER BY run_screen_id, row_index", run_id
        )

    rows_by_screen: dict[int, list] = {}
    for r in rows:
        rows_by_screen.setdefault(r["run_screen_id"], []).append(r["data"])

    version = {k: run[k] for k in (
        "version_no", "is_current", "top_up", "domain", "subdomain", "geography", "resolved_geography",
        "catalog_version", "mode", "seed", "target_name", "note", "started_at", "finished_at",
    )}
    version["duration_seconds"] = (run["params"] or {}).get("duration_seconds")
    version["logs"] = (run["params"] or {}).get("logs") or []
    return {
        "format": "demo-data-bundle",
        "format_version": 1,
        "exported_at": datetime.now(timezone.utc),
        "profile": {"id": profile["id"], "name": profile.get("name")},
        "version": version,
        "screens": [
            {
                "screen": s["screen"], "screen_id": s["screen_id"], "module": s["module"], "kms_module": s["kms_module"],
                "feature_id": s["feature_id"], "feature_name": s["feature_name"],
                "status": s["status"], "message": s["message"], "apm_ready": s["apm_ready"],
                "fields": s["fields"], "row_count": s["row_count"],
                "rows": rows_by_screen.get(s["id"], []),
            }
            for s in screens
        ],
    }