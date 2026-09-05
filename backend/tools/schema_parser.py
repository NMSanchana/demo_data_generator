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
