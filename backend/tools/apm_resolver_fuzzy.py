import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from localedata.apm_modules import APM_MODULES

MODULE_MIN_CONFIDENCE = 0.5
ENDPOINT_MIN_CONFIDENCE = 0.45


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


def extract_schema_fields(spec: dict, schema_name: str) -> dict:
    """Top-level scalar fields only (name -> {type, format[, enum, enum_labels]})
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
    that varies by domain."""
    schemas = spec.get("components", {}).get("schemas", {})
    schema = schemas.get(schema_name, {})
    props = schema.get("properties", {})

    scalar_fields = {}
    nested_fields = []
    for name, prop in props.items():
        if "$ref" in prop or prop.get("type") in ("object", "array"):
            nested_fields.append(name)
            continue

        field_meta = {"type": prop.get("type"), "format": prop.get("format")}

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
