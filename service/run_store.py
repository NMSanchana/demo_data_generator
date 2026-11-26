"""
Stores each Generate as a VERSION in the profile's own database (service/profile_db.py).

  - a normal Generate creates the next version (v1, v2, ...) and marks it current;
  - "Top up" appends to the current version instead (same screen -> more rows, new screen -> new entry);
  - screens that failed are stored too (status 'error' + message), so history shows what happened.

Storing is best effort: generation must never fail because storage did. save_run() never raises; it returns a
small dict describing what happened, which /generate passes back to the UI.
"""

import logging

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
            await conn.execute("UPDATE dd_runs SET finished_at = NOW() WHERE id = $1", run_id)
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
                {"features": [r.feature_id for r in results if r.feature_id]},
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


async def list_runs(profile_id: str) -> list[dict]:
    """Versions of one profile, newest first, with screen and row totals."""
    profile = await profiles_service.get_profile(profile_id)
    if not profile or profile.get("db_status") != "ready":
        return []
    pool = await profile_db.get_profile_pool(profile.get("db_name") or profile_db.db_name_for(profile_id))
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.version_no, r.is_current, r.top_up, r.domain, r.geography, r.resolved_geography,
                   r.catalog_version, r.mode, r.seed, r.target_name, r.note, r.started_at, r.finished_at,
                   COUNT(s.id) AS screens, COALESCE(SUM(s.row_count), 0) AS row_count
            FROM dd_runs r LEFT JOIN dd_run_screens s ON s.run_id = r.id
            GROUP BY r.id ORDER BY r.version_no DESC
            """
        )
    return [dict(r) for r in rows]