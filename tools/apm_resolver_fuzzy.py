import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from localedata.apm_modules import APM_MODULES
from service import testdb_client
from tools.fk_resolver import resolve_fk_field

logger = logging.getLogger(__name__)

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


_CONSTRAINT_KEYS = ("maxLength", "minLength", "pattern", "minimum", "maximum")


def _parse_enum_from_description(description: str) -> tuple[list[int], list[str]] | None:
    if not description:
        return None

    pairs = re.findall(r"(\d+)\s*=\s*([A-Za-z][A-Za-z0-9 _-]*)", description)
    if len(pairs) >= 2:
        values = [int(v) for v, _ in pairs]
        labels = [label.strip() for _, label in pairs]
        return values, labels

    range_match = re.search(r"(\d+)\s*-\s*(\d+)", description)
    labels_match = re.search(r"\(([A-Za-z][A-Za-z0-9 _/-]*)\)", description)
    if range_match and labels_match:
        start = int(range_match.group(1))
        end = int(range_match.group(2))
        labels = [part.strip() for part in labels_match.group(1).split("/") if part.strip()]
        if labels and len(labels) == (end - start + 1):
            values = list(range(start, end + 1))
            return values, labels

    return None


async def extract_schema_fields(spec: dict, schema_name: str) -> dict:
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

        description = prop.get("description")
        field_meta = {
            "type": prop.get("type"),
            "format": prop.get("format"),
            "is_required": name in required_fields,
        }
        if description:
            field_meta["description"] = description

        for constraint_key in _CONSTRAINT_KEYS:
            if constraint_key in prop:
                field_meta[constraint_key] = prop[constraint_key]

        enum_values = prop.get("enum")
        enum_labels = None
        if enum_values:
            enum_labels = (
                prop.get("x-enumNames")
                or prop.get("x-enum-varnames")
                or prop.get("x-ms-enum", {}).get("values")
            )
            field_meta["enum_source"] = "schema"
        elif description:
            recovered = _parse_enum_from_description(description)
            if recovered:
                enum_values, enum_labels = recovered
                field_meta["enum_source"] = "description"

        if enum_values:
            field_meta["enum"] = enum_values
        if enum_labels:
            field_meta["enum_labels"] = enum_labels

        fk_ref = await resolve_fk_field(schema_name, name)
        if fk_ref:
            fk_options = await testdb_client.get_fk_options(
                fk_ref["table"], fk_ref["id_column"], fk_ref["label_column"]
            )
            if fk_options:
                field_meta["is_fk"] = True
                field_meta["fk_options"] = fk_options
            else:
                logger.warning(
                    "extract_schema_fields: declared FK resolved for (%r, %r) -> %s, "
                    "but the live lookup returned no rows, falling back to plain-integer generation",
                    schema_name, name, fk_ref["table"],
                )

        scalar_fields[name] = field_meta

    return {"scalar_fields": scalar_fields, "nested_fields": nested_fields}


def resolve_field_name(target: str, candidates: list[str]) -> tuple[str, float] | None:
    if not candidates:
        return None

    q = _normalize(target)
    scored = [(_similarity(_normalize(c), q), c) for c in candidates]
    scored.sort(key=lambda t: t[0], reverse=True)

    best_score, best_candidate = scored[0]
    if best_score < FIELD_MIN_CONFIDENCE:
        return None
    return best_candidate, round(best_score, 3)