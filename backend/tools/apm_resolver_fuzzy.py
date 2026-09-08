import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from localedata.apm_modules import APM_MODULES

MODULE_MIN_CONFIDENCE = 0.5
ENDPOINT_MIN_CONFIDENCE = 0.45
FIELD_MIN_CONFIDENCE = 0.75


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class ResolvedModule:
    module: str
    path: str
    cluster: str
    confidence: float


@dataclass
class ResolvedEndpoint:
    entity: str
    path: str
    operation_id: str
    schema_name: str
    confidence: float


def resolve_module(query: str) -> ResolvedModule | None:
    q = _normalize(query)
    scored = []
    for entry in APM_MODULES:
        score = max(
            _similarity(_normalize(entry["module"]), q),
            _similarity(_normalize(entry["path"]), q),
        )
        scored.append((score, entry))
    scored.sort(key=lambda t: t[0], reverse=True)

    best_score, best = scored[0]
    if best_score < MODULE_MIN_CONFIDENCE:
        return None
    return ResolvedModule(module=best["module"], path=best["path"], cluster=best["cluster"], confidence=round(best_score, 3))


def iter_save_operations(spec: dict):
    for path, methods in spec.get("paths", {}).items():
        post_op = methods.get("post")
        if not post_op:
            continue
        operation_id = post_op.get("operationId", "")
        if "Save" not in operation_id and "save" not in path.lower():
            continue
        entity = path.strip("/").split("/")[0]
        request_body = post_op.get("requestBody", {})
        content = request_body.get("content", {}).get("application/json", {})
        schema = content.get("schema", {})
        schema_ref = schema.get("$ref")
        schema_name = schema_ref.rsplit("/", 1)[-1] if schema_ref else None
        yield path, operation_id, entity, schema_name


def resolve_save_endpoint(spec: dict, screen_query: str) -> ResolvedEndpoint | None:
    q = _normalize(screen_query)
    scored = []
    for path, operation_id, entity, schema_name in iter_save_operations(spec):
        if not schema_name:
            continue
        score = _similarity(_normalize(entity), q)
        scored.append((score, path, operation_id, entity, schema_name))

    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, path, operation_id, entity, schema_name = scored[0]
    if best_score < ENDPOINT_MIN_CONFIDENCE:
        return None

    return ResolvedEndpoint(
        entity=entity, path=path, operation_id=operation_id,
        schema_name=schema_name, confidence=round(best_score, 3),
    )


# Format/range constraint keys carried straight through from the swagger
# field schema, when present, onto the returned field_meta dict. These are
# used both as generation hints for the LLM and as a save-time safety net
# (see row_mapper._validate_constraints).
_CONSTRAINT_KEYS = ("maxLength", "minLength", "pattern", "minimum", "maximum")


def extract_schema_fields(spec: dict, schema_name: str) -> dict:
    """Top-level scalar fields only (name -> {type, format, is_required
    [, maxLength, minLength, pattern, minimum, maximum][, enum, enum_labels]})
    — enough to know which generated row keys map directly onto the save
    body, AND (critically, see Section 6.3) enough to tell the Data
    Generator Agent the real target type of each field before generation
    happens. Nested object/array fields (sub-DTOs) are listed separately
    so the save step can knowingly skip them rather than silently drop
    data.

    Fixed-value byte/int fields (C# enums) are frequently exported by
    Swashbuckle/NSwag as an "enum" list of numeric codes, with the
    human-readable names alongside under one of a few vendor extension
    keys. Capture both when present — without this, a field like
    DomainType (0-4: Manufacturing/Quality/Maintenance/General/Support)
    looks like a plain byte and gets treated as free-text domain
    vocabulary, which is wrong: it's a fixed system enum, not something
    that varies by domain.

    Two more things the same swagger schema already carries but which
    used to be ignored:

    - "required": [...] at the schema's top level -- which fields MUST be
      filled in for APM to accept the save. Tagged per-field as
      is_required so a missing mandatory field can be flagged before the
      save is even attempted, instead of only discovered from a failed
      response.
    - Per-field maxLength/minLength/pattern/minimum/maximum -- value-shape
      constraints APM actually enforces, so generated values can be
      steered (and, as a safety net, checked) against them.
    """
    schemas = spec.get("components", {}).get("schemas", {})
    schema = schemas.get(schema_name, {})
    props = schema.get("properties", {})
    required_fields = set(schema.get("required") or [])

    scalar_fields = {}
    nested_fields = []
    for name, prop in props.items():
        if "$ref" in prop or prop.get("type") in ("object", "array"):
            nested_fields.append(name)
            continue

        field_meta = {
            "type": prop.get("type"),
            "format": prop.get("format"),
            "is_required": name in required_fields,
        }

        for constraint_key in _CONSTRAINT_KEYS:
            if constraint_key in prop:
                field_meta[constraint_key] = prop[constraint_key]

        enum_values = prop.get("enum")
        if enum_values:
            enum_labels = (
                prop.get("x-enumNames")
                or prop.get("x-enum-varnames")
                or prop.get("x-ms-enum", {}).get("values")
            )
            field_meta["enum"] = enum_values
            if enum_labels:
                field_meta["enum_labels"] = enum_labels

        scalar_fields[name] = field_meta

    return {"scalar_fields": scalar_fields, "nested_fields": nested_fields}


def resolve_field_name(target: str, candidates: list[str]) -> tuple[str, float] | None:
    """Fuzzy-match a single APM field name (target) against a list of
    candidate frontend row keys, using the same normalize + SequenceMatcher
    technique as resolve_module/resolve_save_endpoint above.

    Used by row_mapper.map_row_to_schema as a fallback for fields that
    don't have an exact-name match in the generated row (e.g. APM field
    "CustomerName" vs frontend field "CustName") -- without this, such
    fields were silently dropped from the save payload.

    Returns (best_candidate, confidence) or None if nothing among the
    candidates clears FIELD_MIN_CONFIDENCE, or if there are no candidates
    left to match against.
    """
    if not candidates:
        return None

    q = _normalize(target)
    scored = [(_similarity(_normalize(c), q), c) for c in candidates]
    scored.sort(key=lambda t: t[0], reverse=True)

    best_score, best_candidate = scored[0]
    if best_score < FIELD_MIN_CONFIDENCE:
        return None
    return best_candidate, round(best_score, 3)