"""CRUD for dimension values (dimension structure stays in seed; values are user-managed)."""
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
