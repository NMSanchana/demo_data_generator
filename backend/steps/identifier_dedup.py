"""
identifier_dedup_pass — code, NOT generation.

This is a lightweight safety net, not a generation method: if the LLM
happens to produce the same identifier value twice within one screen's row
batch, this appends a short disambiguating suffix to the later occurrence.
It never invents new content and never runs for any field kind other than
"identifier". Flagged clearly here (and in Section 5.4 of the remediation
spec) so it's never mistaken for "the code is generating demo data" — the
one narrow exception point.1 of the spec carves out alongside deterministic
field discovery.
"""

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
