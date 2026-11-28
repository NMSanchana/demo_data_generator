"""CRUD for dimension values and for custom dimensions (the built-in dimensions stay in the frontend seed)."""
from service.postgres_client import get_pool


async def list_dimension_values(dimension_id: str | None = None) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        if dimension_id:
            rows = await conn.fetch(
                "SELECT * FROM ddpa_dimension_values WHERE dimension_id = $1 ORDER BY name",
                dimension_id
            )
        else:
            rows = await conn.fetch("SELECT * FROM ddpa_dimension_values ORDER BY dimension_id, name")
        return [dict(r) for r in rows]


async def upsert_dimension_value(v: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ddpa_dimension_values (id, dimension_id, name)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE SET
                dimension_id = EXCLUDED.dimension_id,
                name         = EXCLUDED.name
            RETURNING *
        """,
        v["id"], v["dimensionId"], v["name"],
        )
        return dict(row)


async def delete_dimension_value(value_id: str) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM ddpa_dimension_values WHERE id = $1", value_id)
        return result == "DELETE 1"



async def list_dimensions() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM ddpa_dimensions ORDER BY precedence DESC, name")
        return [dict(r) for r in rows]


async def upsert_dimension(d: dict) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ddpa_dimensions (id, name, description, precedence)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (id) DO UPDATE SET
                name        = EXCLUDED.name,
                description = EXCLUDED.description,
                precedence  = EXCLUDED.precedence
            RETURNING *
        """,
        d["id"], d["name"], d.get("description") or "", float(d["precedence"]),
        )
        return dict(row)


async def delete_dimension(dimension_id: str) -> bool:
    """Deletes a custom dimension together with its values and the rules that use it."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM ddpa_rules WHERE dimension_id = $1", dimension_id)
            await conn.execute("DELETE FROM ddpa_dimension_values WHERE dimension_id = $1", dimension_id)
            result = await conn.execute("DELETE FROM ddpa_dimensions WHERE id = $1", dimension_id)
        return result == "DELETE 1"


async def list_dimension_order() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, precedence FROM ddpa_dimension_order")
        return [dict(r) for r in rows]


async def save_dimension_order(items: list[dict]) -> list[dict]:
    """Stores the whole priority order in one go. items: [{"id": ..., "precedence": ...}]"""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for it in items:
                await conn.execute("""
                    INSERT INTO ddpa_dimension_order (id, precedence)
                    VALUES ($1, $2)
                    ON CONFLICT (id) DO UPDATE SET precedence = EXCLUDED.precedence, updated_at = NOW()
                """, it["id"], float(it["precedence"]))
    return await list_dimension_order()