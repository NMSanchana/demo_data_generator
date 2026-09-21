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


# KMS records only give a plain list of field NAME strings -- no source
# HTML tag to classify from. This guesses a kind purely from the name,
# using the same identifier/name suffix heuristics as the real parser
# above, extended to guess date/checkbox/textarea/picklist too. It's a
# heuristic, not a fact -- unlike parse_component_html, which reads the
# real component tag.
_BOOLEAN_PREFIXES = ("is", "has")
_BOOLEAN_SUFFIXES = ("flag", "enabled", "active")
_DATE_MARKERS = ("date", "time")
_TEXTAREA_MARKERS = ("description", "notes", "remarks", "comment", "comments")
_PICKLIST_MARKERS = ("category", "type", "status", "reason", "uom", "currency", "priority")


def _classify_by_name(field_name: str) -> str:
    lower_name = field_name.lower()

    if lower_name.endswith(_IDENTIFIER_SUFFIXES):
        return "identifier"
    if lower_name.endswith(_NAME_SUFFIXES):
        return "name"
    if lower_name.startswith(_BOOLEAN_PREFIXES) or lower_name.endswith(_BOOLEAN_SUFFIXES):
        return "checkbox"
    if any(m in lower_name for m in _DATE_MARKERS):
        return "date"
    if any(m in lower_name for m in _TEXTAREA_MARKERS):
        return "textarea"
    if any(m in lower_name for m in _PICKLIST_MARKERS):
        return "picklist"
    return "input"


def classify_fields_from_names(field_names: list[str]) -> list[dict]:
    """Builds schema_extraction-shaped field records from a plain list of
    field name strings (what KMS provides), instead of parsing real HTML
    (what parse_component_html does). Downstream code
    (Agents/data_generator_agent.py) only actually reads field_name and
    kind=='picklist' from these dicts -- selector/source_tag are filled in
    for shape-compatibility/display only."""
    fields: list[dict] = []
    for name in field_names:
        kind = _classify_by_name(name)
        fields.append({
            "field_name": name,
            "kind": kind,
            "selector": f"formControlName={name}",
            "domain_sensitive": kind in _DOMAIN_SENSITIVE_KINDS,
            "source_tag": "kms",
        })
    return fields