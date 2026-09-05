import logging

logger = logging.getLogger(__name__)


def dedup_identifiers(rows: list[dict], fields: list[dict]) -> list[dict]:
    identifier_field_names = [f["field_name"] for f in fields if f.get("kind") == "identifier"]
    if not identifier_field_names:
        return rows

    for field_name in identifier_field_names:
        seen: set[str] = set()
        for row in rows:
            value = row.get(field_name)
            if value is None:
                continue
            original = str(value)
            candidate = original
            suffix = 2
            while candidate in seen:
                candidate = f"{original}-{suffix}"
                suffix += 1
            if candidate != original:
                logger.info("identifier_dedup_pass: %r duplicated in field %r -- disambiguated to %r", original, field_name, candidate)
                row[field_name] = candidate
            seen.add(candidate)

    return rows
