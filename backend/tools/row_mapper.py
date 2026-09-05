import hashlib
import logging
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

_BYTE_MAX = 255
_INT32_MAX = 2_147_483_647
_DEFAULT_INT_MAX = 32767

# Fields confirmed (via a real APM 400-error response) to be fixed system
# enums rather than domain-flavored free text, for cases where the swagger
# spec itself doesn't expose "enum"/"x-enumNames" for us to read
# generically. This is a documented, temporary bridge — each entry should
# be deleted once the real spec is confirmed to carry the enum metadata
# (tools/apm_resolver_fuzzy.extract_schema_fields) and this fallback is no
# longer needed for that field. Source of truth for DomainType: an APM
# error body -> "DomainType must be 0-4 (Manufacturing/Quality/
# Maintenance/General/Support)".
_KNOWN_ENUM_FALLBACKS = {
    "DomainType": ["Manufacturing", "Quality", "Maintenance", "General", "Support"],
}


def _best_label_match(value: str, labels: list[str]) -> int | None:
    """Fuzzy-match generated text against a field's real enum labels and
    return the matching index, or None if nothing is a plausible match."""
    best_index, best_score = None, 0.0
    lower_value = value.lower()
    for i, label in enumerate(labels):
        score = SequenceMatcher(None, lower_value, str(label).lower()).ratio()
        if score > best_score:
            best_index, best_score = i, score
    return best_index if best_score >= 0.3 else None


def _stable_code_for_text(value: str, modulus: int) -> int:
    """Deterministic pseudo-numeric code derived from picklist text.

    STOPGAP ONLY: there is currently no real picklist-value -> APM domain-ID
    lookup table anywhere in this codebase. Until that lookup is built,
    this hash-based code is the best we can do: it guarantees the same
    text always maps to the same number and stays within the numeric
    field's valid bounds, so saves stop failing validation.

    IMPORTANT: this code will NOT match the real APM domain ID for that
    picklist option. It unblocks testing/demoing the save flow, but the
    stored value is not semantically correct. The real fix is upstream:
    either the swagger spec exposes enum/enum_labels (preferred — see
    tools/apm_resolver_fuzzy.extract_schema_fields) or a known-fallback
    entry above is added for the specific field.
    """
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest, 16) % modulus


def _coerce_value(field_name: str, value, field_schema: dict):
    """Coerce a generated value to match the field's real schema type
    before it goes into the save body. Handles variants of the same
    underlying bug (front-end component's assumed kind != real APM schema
    type landing a JS-typed value on a numeric field):
      1. bool -> integer/byte           (e.g. IsActive)  — exact
      2. str  -> integer/byte, w/ enum  (e.g. DomainType) — real label match
      3. str  -> integer/byte, no enum info available     — stopgap hash
    Values that already match the expected shape pass through untouched."""
    field_schema = field_schema or {}
    field_type = field_schema.get("type")
    field_format = field_schema.get("format")

    if field_type == "integer" and isinstance(value, bool):
        # Same bug class as the picklist-text case below, different pairing:
        # a checkbox-kind field got classified/generated as a real Python
        # bool, but the field's actual APM schema type is numeric (e.g.
        # IsActive is a byte, not bool). A JSON boolean lands on a byte/int
        # field and the API rejects it with something like "Unexpected
        # token False when parsing System.Byte." Cast straight to 0/1 —
        # this mapping IS exact (no lookup/hash needed) since true/false
        # have an unambiguous 1/0 equivalent.
        return int(value)

    if field_type == "integer" and isinstance(value, str):
        # A numeric string ("42") is a legitimate integer value — just cast it.
        try:
            return int(value)
        except ValueError:
            pass

        # Non-numeric string (e.g. "Organic Farming") hitting an integer
        # field: this is the picklist-text-vs-enum-code mismatch. Prefer a
        # real fix over a guess, in priority order:
        #   a) spec-provided enum labels (from apm_resolver_fuzzy)
        #   b) a documented known-fallback for this specific field
        #   c) last resort: stopgap hash, bounded to the field's format
        enum_labels = field_schema.get("enum_labels") or _KNOWN_ENUM_FALLBACKS.get(field_name)
        enum_values = field_schema.get("enum")
        if enum_labels:
            match_index = _best_label_match(value, enum_labels)
            if match_index is None:
                # Generated text (domain vocab) doesn't resemble any real
                # label closely enough to trust — this happens because the
                # generator is filling a fixed system enum from
                # domain-flavored vocab that was never meant to describe
                # it. We still know the valid *range* though, so hash into
                # that range rather than the field's full byte/int32 range —
                # this guarantees we never resend an out-of-bounds value,
                # even though the resulting code is still a guess, not a
                # real answer. The actual fix is upstream: don't generate
                # domain vocab for fields that are fixed system enums (see
                # the enum_labels passthrough into the data generator
                # prompt in Agents/data_generator_agent.py).
                match_index = _stable_code_for_text(value, len(enum_labels))
            if enum_values and match_index < len(enum_values):
                return enum_values[match_index]
            return match_index

        if field_format == "byte":
            modulus = _BYTE_MAX + 1
        elif field_format == "int32":
            modulus = _INT32_MAX
        else:
            modulus = _DEFAULT_INT_MAX
        logger.warning(
            "row_mapper: field %r has no enum metadata to match %r against — "
            "using stopgap hash, value will NOT be semantically correct",
            field_name, value,
        )
        return _stable_code_for_text(value, modulus)

    if field_type == "number" and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            logger.warning("row_mapper: field %r expected number, got %r — dropping", field_name, value)
            return None

    if field_type == "boolean" and isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")

    return value


def map_row_to_schema(row: dict, scalar_fields: dict) -> dict:
    """Only fills fields our generator actually produced (matched by exact
    field name against the schema's scalar fields). Everything else in the
    schema (audit fields, tenant fields, nested sub-DTOs) is left out of
    the body entirely — the server derives that from the Login context, or
    it's covered by the schema's own defaults.

    Each included value is passed through _coerce_value() against that
    field's real schema type/format, so a picklist field that was
    generated as text but is actually backed by a numeric enum (e.g.
    DomainType, type=integer/format=byte) doesn't get sent to APM as a
    raw string.
    """
    body = {}
    for field_name, field_schema in scalar_fields.items():
        if field_name in row and field_name != "needs_review":
            body[field_name] = _coerce_value(field_name, row[field_name], field_schema)
    return body
