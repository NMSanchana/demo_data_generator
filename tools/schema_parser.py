import re

# Component tags that are NOT form-data fields (actions, layout, grids-of-fields
# handled separately, containers) — excluded from extraction entirely.
_NON_FIELD_TAGS = {
    "gb-formaction",
    "gb-card",
    "gb-filter",
    "gb-command-palette",
    "gb-checklisttour",
    "gb-banner",
    "gb-dashboard",
    "gb-dashboardaction",
    "gb-action-panel",
    "gb-chart",
    "gb-comment",
    "gb-appmenu",
    "gb-applogo",
}

# Base tag -> kind, before field-name suffix tie-breaking is applied.
_TAG_KIND_MAP = {
    "gb-newpicklist": "picklist",
    "gb-combobox": "picklist",
    "gb-dynamiccombobox": "picklist",
    "gb-picklist": "picklist",
    "gb-infinitescroll": "picklist",
    "gb-checkbox": "checkbox",
    "gb-radiobutton": "checkbox",
    "gb-date": "date",
    "gb-time": "date",
    "gb-textarea": "textarea",
    "gb-richtexteditor": "textarea",
    "gb-input": "input",
    "gb-address": "input",
    "gb-formgrid": "unclassified",
    "gb-attachment": "unclassified",
    "gb-metaform": "unclassified",
    "gb-contact": "unclassified",
}

# gb-newpicklist / gb-input are reused across identifier/name/generic-input
# fields in the real repo (e.g. ItemCode, ItemName, SKUName all use
# gb-newpicklist). Field-name suffix breaks the tie.
_IDENTIFIER_SUFFIXES = ("code", "id", "no", "num", "number", "slno")
_NAME_SUFFIXES = ("name",)

_TAG_PATTERN = re.compile(
    r"<(?P<tag>gb-[a-z0-9-]+)(?P<attrs>[^>]*?)/?>",
    re.IGNORECASE | re.DOTALL,
)
_FORM_CONTROL_PATTERN = re.compile(
    r"formControlName\s*=\s*[\"']([A-Za-z0-9_]+)[\"']", re.IGNORECASE
)
_SELECTOR_PATTERN = re.compile(
    r"(?:data-testid|test-id)\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE
)

# Domain-sensitive kinds: content whose values would plausibly differ by
# domain/vertical. Identifiers/dates/checkboxes are structural, not
# domain-flavored.
_DOMAIN_SENSITIVE_KINDS = {"picklist", "textarea", "input", "name"}


def _classify(tag: str, field_name: str) -> str:
    base_kind = _TAG_KIND_MAP.get(tag.lower(), "unclassified")

    if tag.lower() in ("gb-newpicklist", "gb-input"):
        lower_name = field_name.lower()
        if lower_name.endswith(_IDENTIFIER_SUFFIXES):
            return "identifier"
        if lower_name.endswith(_NAME_SUFFIXES):
            return "name"
        return base_kind

    return base_kind


def parse_component_html(html: str) -> list[dict]:
    fields: list[dict] = []
    seen_names: set[str] = set()

    for match in _TAG_PATTERN.finditer(html):
        tag = match.group("tag").lower()
        attrs = match.group("attrs") or ""

        if tag in _NON_FIELD_TAGS:
            continue

        control_match = _FORM_CONTROL_PATTERN.search(attrs)
        if not control_match:
            continue

        field_name = control_match.group(1)
        if field_name in seen_names:
            continue
        seen_names.add(field_name)

        selector_match = _SELECTOR_PATTERN.search(attrs)
        selector = selector_match.group(1) if selector_match else f"formControlName={field_name}"

        kind = _classify(tag, field_name)

        fields.append(
            {
                "field_name": field_name,
                "kind": kind,
                "selector": selector,
                "domain_sensitive": kind in _DOMAIN_SENSITIVE_KINDS,
                "source_tag": tag,
            }
        )

    return fields


# KMS records give field NAMES (plus a little metadata) -- no source HTML tag to classify from.
# This guesses a kind from the name's WORDS (camelCase / snake_case split into tokens), using the
# same identifier/name idea as the real parser above. Token matching matters: a plain substring
# test called "UpdatedBy" a date field (it contains "date") and "Paid" an identifier ("id").
# It is a heuristic, not a fact -- unlike parse_component_html, which reads the real tag.
_BOOLEAN_FIRST = {"is", "has", "can", "allow", "enable"}
_BOOLEAN_LAST = {"flag", "enabled", "active", "required", "applicable"}
_DATE_TOKENS = {"date", "time", "datetime", "dob", "timestamp"}
_TEXTAREA_TOKENS = {"description", "notes", "note", "remarks", "remark", "comment", "comments", "narration"}
_PICKLIST_TOKENS = {"category", "type", "status", "reason", "uom", "currency", "priority", "group", "class", "mode", "method", "level"}
_ID_LAST = {"code", "id", "no", "num", "number", "slno", "sno"}


def _tokens(name: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", name or "")]


def _classify_by_name(field_name: str, data_type: str = "") -> str:
    toks = _tokens(field_name)
    dt = (data_type or "").lower()
    if toks:
        last, first = toks[-1], toks[0]
        if last in _ID_LAST:
            return "identifier"
        if last == "name":
            return "name"
        if first in _BOOLEAN_FIRST or last in _BOOLEAN_LAST or any(k in dt for k in ("bool", "bit")):
            return "checkbox"
        if last in _DATE_TOKENS or any(t in _DATE_TOKENS for t in toks) or "date" in dt:
            return "date"
        if any(t in _TEXTAREA_TOKENS for t in toks):
            return "textarea"
        if any(t in _PICKLIST_TOKENS for t in toks):
            return "picklist"
    return "input"


_NUMERIC_TOKENS = {
    "number", "percentage", "decimal", "numeric", "int", "integer", "smallint", "tinyint", "bigint",
    "byte", "double", "float", "money", "currency", "amount",
}


def _classify_with_meta(name: str, info: dict) -> str:
    """Kind from the field's real KMS type metadata first, then the name heuristic.

    The name alone misleads: "ReservationPercentage" contains "category" in its screen prefix
    (CasteCategory...) and would become a picklist, but KMS says it is a percentage."""
    base = _classify_by_name(name, info.get("dataType", ""))
    types = " ".join(str(info.get(k) or "") for k in ("typeForm", "typeBackend", "typeDb")).lower()
    toks = set(re.findall(r"[a-z]+", types))
    ui = str(info.get("ui") or "").lower()

    if info.get("allowedValues") and base not in ("identifier", "name"):
        return "picklist"
    if toks & {"bool", "boolean", "bit"}:
        return "checkbox"
    if base in ("identifier", "name"):
        return base
    if "date" in types:
        return "date"
    if "lookup" in ui:
        return "picklist"
    if toks & _NUMERIC_TOKENS:
        return "input"
    return base


def _record(name: str, kind: str, required: bool = False, extra: dict | None = None) -> dict:
    rec = {
        "field_name": name,
        "kind": kind,
        "selector": f"formControlName={name}",
        "domain_sensitive": kind in _DOMAIN_SENSITIVE_KINDS,
        "source_tag": "kms",
        "required": required,
    }
    if extra:
        rec.update({k: v for k, v in extra.items() if v not in (None, "", [], {})})
    return rec


def _meta_extra(it: dict) -> dict:
    """Context for the generator (it reads the whole field record): on-screen label, a readable
    type, the length limit and the closed list of allowed values, when KMS has them."""
    form, db = str(it.get("typeForm") or ""), str(it.get("typeDb") or "")
    data_type = form if not db or db.lower() == form.lower() else (f"{form} ({db})" if form else db)
    label = str(it.get("label") or "")
    return {
        "label": label,
        "data_type": data_type,
        "max_length": it.get("maxLength"),
        "allowed_values": it.get("allowedValues"),
    }


def classify_fields(infos: list) -> list[dict]:
    """Builds schema_extraction-shaped field records from KMS field info.
    Each item is either a plain name string or a dict {name, required, dataType, ...}
    (see kms_catalog.select_screen_fields). Downstream code (Agents/data_generator_agent.py)
    reads field_name and kind; `required`, `label`, `data_type`, `max_length` and
    `allowed_values` are extra context for the generator."""
    fields: list[dict] = []
    for it in infos:
        if isinstance(it, str):
            fields.append(_record(it, _classify_by_name(it, ""), False))
            continue
        name = it["name"]
        fields.append(_record(name, _classify_with_meta(name, it), bool(it.get("required")), _meta_extra(it)))
    return fields


def classify_fields_from_names(field_names: list[str]) -> list[dict]:
    """Backward-compatible wrapper: a plain list of field name strings."""
    return classify_fields(list(field_names))