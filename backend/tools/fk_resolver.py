import logging
import re

from service import testdb_client

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _strip_id_suffix(name: str) -> str:
    return re.sub(r"id$", "", name, flags=re.IGNORECASE)


def _guess_label_column(columns: list[dict]) -> str | None:
    names = [c["name"] for c in columns]
    for n in names:
        if n.upper().endswith("NAME"):
            return n
    for n in names:
        if n.upper().endswith("CODE"):
            return n
    return None


async def resolve_fk_field(schema_name: str, field_name: str) -> dict | None:
    declared_fks = await testdb_client.get_declared_foreign_keys()
    if not declared_fks:
        return None

    field_norm = _normalize(field_name)
    field_stem = _normalize(_strip_id_suffix(field_name))

    matches = []
    for fk in declared_fks:
        column_norm = _normalize(fk["parent_column"])
        column_stem = _normalize(_strip_id_suffix(fk["parent_column"]))
        if field_norm == column_norm or field_stem == column_stem:
            matches.append(fk)

    if not matches:
        return None

    distinct_tables = {fk["referenced_table"] for fk in matches}
    if len(distinct_tables) > 1:
        logger.warning(
            "resolve_fk_field: (%r, %r) matches declared FK columns pointing at multiple tables (%s), skipping",
            schema_name, field_name, ", ".join(sorted(distinct_tables)),
        )
        return None

    fk = matches[0]
    table = fk["referenced_table"]
    id_column = fk["referenced_column"]

    columns = await testdb_client.get_table_columns(table)
    column_names_upper = {c["name"].upper() for c in columns}
    if "STATUS" not in column_names_upper or "TENANTID" not in column_names_upper:
        logger.warning(
            "resolve_fk_field: (%r, %r) -> %r missing STATUS/TENANTID, skipping",
            schema_name, field_name, table,
        )
        return None

    label_column = _guess_label_column(columns)
    if not label_column:
        logger.warning(
            "resolve_fk_field: (%r, %r) -> %r has no NAME/CODE-like column, skipping",
            schema_name, field_name, table,
        )
        return None

    return {"table": table, "id_column": id_column, "label_column": label_column}