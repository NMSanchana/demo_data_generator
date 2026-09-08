import hashlib
import logging
import re
from difflib import SequenceMatcher

from tools import apm_resolver_fuzzy as fuzzy

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


def _is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _validate_constraints(field_name: str, value, field_schema: dict) -> None:
    """Safety-net check (log only, never mutates/truncates/blocks) against
    the maxLength/minLength/pattern/minimum/maximum constraints swagger
    already carries per field — same "log honestly, don't pretend" style
    as the enum-guessing branch in _coerce_value above."""
    if value is None:
        return

    max_length = field_schema.get("maxLength")
    if max_length is not None and isinstance(value, str) and len(value) > max_length:
        logger.warning(
            "row_mapper: field %r value %r is %d chars, exceeds maxLength=%d",
            field_name, value, len(value), max_length,
        )

    min_length = field_schema.get("minLength")
    if min_length is not None and isinstance(value, str) and len(value) < min_length:
        logger.warning(
            "row_mapper: field %r value %r is %d chars, below minLength=%d",
            field_name, value, len(value), min_length,
        )

    pattern = field_schema.get("pattern")
    if pattern and isinstance(value, str):
        try:
            if not re.search(pattern, value):
                logger.warning(
                    "row_mapper: field %r value %r does not match pattern %r",
                    field_name, value, pattern,
                )
        except re.error:
            # A pattern straight from third-party swagger.json may not be a
            # valid Python regex (e.g. .NET-specific syntax) — don't crash
            # the save over an unparseable constraint, just skip the check.
            pass

    minimum = field_schema.get("minimum")
    if minimum is not None and isinstance(value, (int, float)) and value < minimum:
        logger.warning(
            "row_mapper: field %r value %r is below minimum=%s",
            field_name, value, minimum,
        )

    maximum = field_schema.get("maximum")
    if maximum is not None and isinstance(value, (int, float)) and value > maximum:
        logger.warning(
            "row_mapper: field %r value %r exceeds maximum=%s",
            field_name, value, maximum,
        )


async def map_row_to_schema(row: dict, scalar_fields: dict, screen_name: str) -> dict:
    """
    Maps a generated/edited frontend row onto the real APM save schema.

    Returns:
      {
        "body": dict,                        # the actual save payload
        "missing_required_fields": list[str],# APM-required fields left empty/absent
        "unmatched_apm_fields": list[str],    # APM fields with no row match at all
        "unmatched_frontend_fields": list[str], # row keys never matched to an APM field
      }

    Matching order per APM field: exact name match against `row` first;
    if that misses, fuzzy-match against whatever `row` keys are still
    unclaimed (see tools.apm_resolver_fuzzy.resolve_field_name, same
    string-similarity technique already used for module/endpoint
    resolution, ~75%+ confidence). Anything still unmatched on either side
    is reported rather than silently dropped.
    """
    body: dict = {}
    missing_required_fields: list[str] = []
    unmatched_apm_fields: list[str] = []

    # "needs_review" is a frontend-only bookkeeping key, never a real APM
    # field -- exclude it up front so it never counts as an unmatched
    # frontend field or gets fuzzy-matched against.
    remaining_row_keys = {k for k in row.keys() if k != "needs_review"}

    for field_name, field_schema in scalar_fields.items():
        field_schema = field_schema or {}
        matched_key = None

        if field_name in row and field_name in remaining_row_keys:
            matched_key = field_name
        else:
            fuzzy_match = fuzzy.resolve_field_name(field_name, sorted(remaining_row_keys))
            if fuzzy_match is not None:
                matched_key, confidence = fuzzy_match
                logger.info(
                    "row_mapper: fuzzy-matched APM field %r to frontend field %r (confidence=%.2f)",
                    field_name, matched_key, confidence,
                )

        if matched_key is None:
            unmatched_apm_fields.append(field_name)
            if field_schema.get("is_required"):
                missing_required_fields.append(field_name)
            continue

        remaining_row_keys.discard(matched_key)
        value = _coerce_value(field_name, row[matched_key], field_schema)
        _validate_constraints(field_name, value, field_schema)

        if field_schema.get("is_required") and _is_empty(value):
            missing_required_fields.append(field_name)

        body[field_name] = value

    unmatched_frontend_fields = sorted(remaining_row_keys)

    if missing_required_fields:
        logger.warning(
            "row_mapper: screen %r save is missing required APM fields: %s",
            screen_name, missing_required_fields,
        )
    if unmatched_apm_fields:
        logger.warning(
            "row_mapper: screen %r has APM fields with no matching row data: %s",
            screen_name, unmatched_apm_fields,
        )
    if unmatched_frontend_fields:
        logger.warning(
            "row_mapper: screen %r has row fields that didn't match any APM field: %s",
            screen_name, unmatched_frontend_fields,
        )

    return {
        "body": body,
        "missing_required_fields": missing_required_fields,
        "unmatched_apm_fields": unmatched_apm_fields,
        "unmatched_frontend_fields": unmatched_frontend_fields,
    }