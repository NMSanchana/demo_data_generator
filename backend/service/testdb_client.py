import asyncio
import logging
import os
import re
from difflib import SequenceMatcher

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TESTDB_HOST     = os.getenv("TESTDB_HOST")
TESTDB_PORT     = os.getenv("TESTDB_PORT")  # optional -- engine default used if unset
TESTDB_NAME     = os.getenv("TESTDB_NAME")
TESTDB_USER     = os.getenv("TESTDB_USER")
TESTDB_PASSWORD = os.getenv("TESTDB_PASSWORD")
TESTDB_ENGINE   = os.getenv("TESTDB_ENGINE")  # optional override: postgres / mysql / mssql / oracle

TABLE_MIN_CONFIDENCE = 0.42
COLUMN_MIN_CONFIDENCE = 0.4
CONNECT_TIMEOUT_SECONDS = 6
DEFAULT_VALUE_LIMIT = 200

# Cache of {"engine": name, "conn": <driver connection>} once one engine has
# been proven reachable, so subsequent calls in this process skip probing.
# `None` conn means "checked and TESTDB is not usable" (also cached, so a
# misconfigured/unreachable TESTDB doesn't get retried on every field of
# every row of every request).
_connection_cache: dict | None = None
_connection_lock = asyncio.Lock()


def _is_configured() -> bool:
    return bool(TESTDB_HOST and TESTDB_NAME and TESTDB_USER and TESTDB_PASSWORD)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _best_match(query: str, candidates: list[str], min_confidence: float) -> str | None:
    if not candidates:
        return None
    q = _normalize(query)
    scored = [(_similarity(q, _normalize(c)), c) for c in candidates]
    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best_candidate = scored[0]
    if best_score < min_confidence:
        return None
    return best_candidate


# ---------------------------------------------------------------------
# Per-engine adapters. Each returns None (never raises) if that engine's
# driver isn't installed or the connection attempt fails.
# ---------------------------------------------------------------------

async def _try_postgres():
    try:
        import asyncpg
    except ImportError:
        return None
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(
                host=TESTDB_HOST, port=int(TESTDB_PORT) if TESTDB_PORT else 5432,
                database=TESTDB_NAME, user=TESTDB_USER, password=TESTDB_PASSWORD,
            ),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        return {"engine": "postgres", "conn": conn}
    except Exception as e:
        logger.info("testdb_client: postgres probe failed: %s", e)
        return None


async def _try_mysql():
    try:
        import aiomysql
    except ImportError:
        return None
    try:
        conn = await asyncio.wait_for(
            aiomysql.connect(
                host=TESTDB_HOST, port=int(TESTDB_PORT) if TESTDB_PORT else 3306,
                db=TESTDB_NAME, user=TESTDB_USER, password=TESTDB_PASSWORD,
            ),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        return {"engine": "mysql", "conn": conn}
    except Exception as e:
        logger.info("testdb_client: mysql probe failed: %s", e)
        return None


async def _try_mssql():
    try:
        import pyodbc
    except ImportError:
        return None

    def _connect():
        port = TESTDB_PORT or "1433"
        conn_str = (
            "DRIVER={ODBC Driver 17 for SQL Server};"
            f"SERVER={TESTDB_HOST},{port};DATABASE={TESTDB_NAME};"
            f"UID={TESTDB_USER};PWD={TESTDB_PASSWORD};"
            "TrustServerCertificate=yes;"
        )
        return pyodbc.connect(conn_str, timeout=CONNECT_TIMEOUT_SECONDS)

    try:
        conn = await asyncio.wait_for(asyncio.to_thread(_connect), timeout=CONNECT_TIMEOUT_SECONDS + 2)
        return {"engine": "mssql", "conn": conn}
    except Exception as e:
        logger.info("testdb_client: mssql probe failed: %s", e)
        return None


async def _try_oracle():
    try:
        import oracledb
    except ImportError:
        return None

    def _connect():
        port = int(TESTDB_PORT) if TESTDB_PORT else 1521
        dsn = oracledb.makedsn(TESTDB_HOST, port, service_name=TESTDB_NAME)
        return oracledb.connect(user=TESTDB_USER, password=TESTDB_PASSWORD, dsn=dsn)

    try:
        conn = await asyncio.wait_for(asyncio.to_thread(_connect), timeout=CONNECT_TIMEOUT_SECONDS + 2)
        return {"engine": "oracle", "conn": conn}
    except Exception as e:
        logger.info("testdb_client: oracle probe failed: %s", e)
        return None


_ENGINE_PROBES = {
    "postgres": _try_postgres,
    "mysql":    _try_mysql,
    "mssql":    _try_mssql,
    "oracle":   _try_oracle,
}
_PROBE_ORDER = ["mssql", "postgres", "mysql", "oracle"]


async def _get_connection() -> dict | None:
    """Returns {"engine": str, "conn": <driver connection>} for the first
    engine that connects successfully, cached for the process lifetime.
    Returns None (cached) if TESTDB isn't configured or no engine works."""
    global _connection_cache

    if _connection_cache is not None:
        return _connection_cache if _connection_cache.get("conn") else None

    async with _connection_lock:
        if _connection_cache is not None:
            return _connection_cache if _connection_cache.get("conn") else None

        if not _is_configured():
            logger.info("testdb_client: TESTDB_HOST/NAME/USER/PASSWORD not fully set -- dependency lookups disabled")
            _connection_cache = {"engine": None, "conn": None}
            return None

        order = [TESTDB_ENGINE] if TESTDB_ENGINE in _ENGINE_PROBES else _PROBE_ORDER
        for engine_name in order:
            probe = _ENGINE_PROBES[engine_name]
            result = await probe()
            if result is not None:
                logger.info("testdb_client: connected to TESTDB via %s", engine_name)
                _connection_cache = result
                return result

        logger.warning("testdb_client: could not connect to TESTDB with any known engine -- dependency lookups will fall back to invented values")
        _connection_cache = {"engine": None, "conn": None}
        return None


# ---------------------------------------------------------------------
# Per-engine introspection + fetch. All synchronous drivers are pushed to
# a thread so this module stays fully async-friendly either way.
# ---------------------------------------------------------------------

async def _list_tables(engine: str, conn) -> list[str]:
    try:
        if engine == "postgres":
            rows = await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
            )
            return [r["table_name"] for r in rows]

        if engine == "mysql":
            async with conn.cursor() as cur:
                await cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (TESTDB_NAME,))
                rows = await cur.fetchall()
            return [r[0] for r in rows]

        if engine == "mssql":
            def _run():
                cur = conn.cursor()
                cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES")
                return [r[0] for r in cur.fetchall()]
            return await asyncio.to_thread(_run)

        if engine == "oracle":
            def _run():
                cur = conn.cursor()
                cur.execute("SELECT table_name FROM user_tables")
                return [r[0] for r in cur.fetchall()]
            return await asyncio.to_thread(_run)

    except Exception as e:
        logger.info("testdb_client: _list_tables failed for engine=%s: %s", engine, e)

    return []


async def _list_columns(engine: str, conn, table: str) -> list[str]:
    try:
        if engine == "postgres":
            rows = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = $1", table,
            )
            return [r["column_name"] for r in rows]

        if engine == "mysql":
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
                    (TESTDB_NAME, table),
                )
                rows = await cur.fetchall()
            return [r[0] for r in rows]

        if engine == "mssql":
            def _run():
                cur = conn.cursor()
                cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ?", table)
                return [r[0] for r in cur.fetchall()]
            return await asyncio.to_thread(_run)

        if engine == "oracle":
            def _run():
                cur = conn.cursor()
                cur.execute("SELECT column_name FROM user_tab_columns WHERE table_name = :1", [table.upper()])
                return [r[0] for r in cur.fetchall()]
            return await asyncio.to_thread(_run)

    except Exception as e:
        logger.info("testdb_client: _list_columns failed for engine=%s table=%s: %s", engine, table, e)

    return []


def _quote_identifier(engine: str, identifier: str) -> str:
    if engine == "mysql":
        return f"`{identifier}`"
    if engine == "postgres":
        return f'"{identifier}"'
    if engine == "mssql":
        return f"[{identifier}]"
    return identifier


async def _fetch_distinct_values(engine: str, conn, table: str, column: str, limit: int) -> list[str]:
    quoted_table = _quote_identifier(engine, table)
    quoted_column = _quote_identifier(engine, column)

    try:
        if engine == "postgres":
            rows = await conn.fetch(
                f"SELECT DISTINCT {quoted_column} FROM {quoted_table} WHERE {quoted_column} IS NOT NULL LIMIT {int(limit)}"
            )
            return [str(r[0]) for r in rows if r[0] is not None]

        if engine == "mysql":
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT DISTINCT {quoted_column} FROM {quoted_table} WHERE {quoted_column} IS NOT NULL LIMIT {int(limit)}"
                )
                rows = await cur.fetchall()
            return [str(r[0]) for r in rows if r[0] is not None]

        if engine == "mssql":
            def _run():
                cur = conn.cursor()
                cur.execute(
                    f"SELECT DISTINCT TOP {int(limit)} {quoted_column} FROM {quoted_table} WHERE {quoted_column} IS NOT NULL"
                )
                return [str(r[0]) for r in cur.fetchall() if r[0] is not None]
            return await asyncio.to_thread(_run)

        if engine == "oracle":
            def _run():
                cur = conn.cursor()
                cur.execute(
                    f"SELECT DISTINCT {quoted_column} FROM {quoted_table} "
                    f"WHERE {quoted_column} IS NOT NULL FETCH FIRST {int(limit)} ROWS ONLY"
                )
                return [str(r[0]) for r in cur.fetchall() if r[0] is not None]
            return await asyncio.to_thread(_run)

    except Exception as e:
        logger.info("testdb_client: _fetch_distinct_values failed for %s.%s (%s): %s", table, column, engine, e)

    return []


# ---------------------------------------------------------------------
# Public entry point used by main.py
# ---------------------------------------------------------------------

async def fetch_master_values(screen_name: str, field_name: str, limit: int = DEFAULT_VALUE_LIMIT) -> list[str] | None:
    """
    Best-effort lookup of real, currently-existing values for a column
    that plausibly corresponds to `field_name`, on a table that plausibly
    corresponds to `screen_name`, in the real TESTDB.

    Returns a non-empty list[str] on success, or None if TESTDB isn't
    configured/reachable, or no plausible table/column match was found, or
    the match had no data — callers must treat None (or an empty list) as
    "fall through to the next tier", never as an error condition.
    """
    connection = await _get_connection()
    if connection is None:
        return None

    engine = connection["engine"]
    conn = connection["conn"]

    tables = await _list_tables(engine, conn)
    table = _best_match(screen_name, tables, TABLE_MIN_CONFIDENCE)
    if table is None:
        logger.info("testdb_client: no plausible table match for screen %r among %d table(s)", screen_name, len(tables))
        return None

    columns = await _list_columns(engine, conn, table)
    column = _best_match(field_name, columns, COLUMN_MIN_CONFIDENCE)
    if column is None:
        logger.info("testdb_client: no plausible column match for field %r on table %r", field_name, table)
        return None

    values = await _fetch_distinct_values(engine, conn, table, column, limit)
    if not values:
        return None

    logger.info(
        "testdb_client: resolved %r/%r -> %s.%s (%d real value(s))",
        screen_name, field_name, table, column, len(values),
    )
    return values