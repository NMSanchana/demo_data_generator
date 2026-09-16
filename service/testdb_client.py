import logging
import os
import time

import pyodbc

logger = logging.getLogger(__name__)

CACHE_SECONDS = 300
CONNECT_TIMEOUT = 10

_CACHE: dict[tuple, list] = {}
_CACHE_AT: dict[tuple, float] = {}


def _get_connection() -> pyodbc.Connection:
    host = os.getenv("TESTDB_HOST")
    name = os.getenv("TESTDB_NAME")
    user = os.getenv("TESTDB_USER")
    password = os.getenv("TESTDB_PASSWORD")

    if not all([host, name, user, password]):
        raise RuntimeError(
            "TESTDB_HOST, TESTDB_NAME, TESTDB_USER, and TESTDB_PASSWORD must all be set in .env"
        )

    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={host};"
        f"DATABASE={name};"
        f"UID={user};"
        f"PWD={password};"
        "TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str, timeout=CONNECT_TIMEOUT)


def _cache_get(key: tuple) -> list | None:
    now = time.time()
    if key in _CACHE and (now - _CACHE_AT.get(key, 0)) < CACHE_SECONDS:
        return _CACHE[key]
    return None


def _cache_set(key: tuple, value: list) -> None:
    _CACHE[key] = value
    _CACHE_AT[key] = time.time()


async def _resolve_tenant_id(table: str) -> int | None:
    cache_key = ("resolved_tenant_id", table)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached[0] if cached else None

    query = (
        f"SELECT TOP 5 TENANTID, COUNT(*) AS row_count FROM {table} "
        f"WHERE STATUS = 1 GROUP BY TENANTID ORDER BY row_count DESC"
    )
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
    except Exception as e:
        logger.warning("_resolve_tenant_id failed for table=%r: %s", table, e)
        return None

    if not rows:
        logger.warning("_resolve_tenant_id: table=%r has no active rows", table)
        _cache_set(cache_key, [])
        return None

    if len(rows) > 1:
        breakdown = ", ".join(f"{r[0]}={r[1]}" for r in rows)
        logger.warning(
            "_resolve_tenant_id: table=%r has multiple tenants (%s), using dominant %r",
            table, breakdown, rows[0][0],
        )

    tenant_id = rows[0][0]
    _cache_set(cache_key, [tenant_id])
    return tenant_id


async def get_fk_options(table: str, id_column: str, label_column: str) -> list[dict]:
    tenant_id = await _resolve_tenant_id(table)
    if tenant_id is None:
        return []

    cache_key = ("fk_options", table, id_column, label_column, tenant_id)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    query = f"SELECT {id_column}, {label_column} FROM {table} WHERE STATUS = 1 AND TENANTID = ?"

    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (tenant_id,))
            rows = cursor.fetchall()
    except Exception as e:
        logger.warning(
            "get_fk_options failed for table=%r id_column=%r label_column=%r: %s",
            table, id_column, label_column, e,
        )
        return []

    options = [{"id": row[0], "label": row[1]} for row in rows]
    _cache_set(cache_key, options)
    return options


async def get_table_columns(table: str) -> list[dict]:
    cache_key = ("table_columns", table)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    query = "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ?"
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (table,))
            rows = cursor.fetchall()
    except Exception as e:
        logger.warning("get_table_columns failed for table=%r: %s", table, e)
        return []

    columns = [{"name": row[0], "type": row[1], "nullable": row[2] == "YES"} for row in rows]
    _cache_set(cache_key, columns)
    return columns


async def get_declared_foreign_keys() -> list[dict]:
    cache_key = ("declared_fks",)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    query = """
        SELECT
            tp.name AS parent_table,
            cp.name AS parent_column,
            tr.name AS referenced_table,
            cr.name AS referenced_column
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id
        JOIN sys.tables tp ON fkc.parent_object_id = tp.object_id
        JOIN sys.columns cp ON fkc.parent_object_id = cp.object_id AND fkc.parent_column_id = cp.column_id
        JOIN sys.tables tr ON fkc.referenced_object_id = tr.object_id
        JOIN sys.columns cr ON fkc.referenced_object_id = cr.object_id AND fkc.referenced_column_id = cr.column_id
    """
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
    except Exception as e:
        logger.warning("get_declared_foreign_keys failed: %s", e)
        return []

    fks = [
        {
            "parent_table": row[0],
            "parent_column": row[1],
            "referenced_table": row[2],
            "referenced_column": row[3],
        }
        for row in rows
    ]
    _cache_set(cache_key, fks)
    return fks