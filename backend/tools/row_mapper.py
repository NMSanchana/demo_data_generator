import hashlib
import logging
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

_LAST_RESORT_CEILING = 8

def _best_label_match(value: str, labels: list[str]) -> int | None:
    """Fuzzy-match generated text against a list of real labels and return
    the matching index, or None if nothing is a plausible match."""
    best_index, best_score = None, 0.0
    lower_value = value.lower()
    for i, label in enumerate(labels):
        score = SequenceMatcher(None, lower_value, str(label).lower()).ratio()
        if score > best_score:
            best_index, best_score = i, score
    return best_index if best_score >= 0.3 else None


def _stable_index_for_text(value: str, modulus: int) -> int:
    """Deterministic pseudo-random index derived from text. Same text
    always maps to the same index, within [0, modulus)."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest, 16) % modulus


def _coerce_value(field_name: str, value, field_schema: dict):
    field_schema = field_schema or {}
    field_type = field_schema.get("type")
    field_format = field_schema.get("format")

    if field_type == "integer" and isinstance(value, bool):
        return int(value)

    if field_type == "integer" and isinstance(value, str):
        # A numeric string ("42") is a legitimate integer value — just cast it.
        try:
            return int(value)
        except ValueError:
            pass

        # Tier 2 — real enum labels straight from the swagger spec, when present.
        enum_labels = field_schema.get("enum_labels")
        enum_values = field_schema.get("enum")
        if enum_labels:
            match_index = _best_label_match(value, enum_labels)
            if match_index is not None:
                if enum_values and match_index < len(enum_values):
                    return enum_values[match_index]
                return match_index

        # Tier 3 — genuinely no information available anywhere. Small,
        # biased guess, clearly logged as such.
        logger.warning(
            "row_mapper: field %r has no enum metadata to match %r against — "
            "guessing a small integer, value will NOT be semantically correct",
            field_name, value,
        )
        return _stable_index_for_text(value, _LAST_RESORT_CEILING)

    if field_type == "number" and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            logger.warning("row_mapper: field %r expected number, got %r — dropping", field_name, value)
            return None

    if field_type == "boolean" and isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")

    return value


async def map_row_to_schema(row: dict, scalar_fields: dict, screen_name: str) -> dict:
    body = {}
    for field_name, field_schema in scalar_fields.items():
        if field_name in row and field_name != "needs_review":
            body[field_name] = _coerce_value(field_name, row[field_name], field_schema)
    return body