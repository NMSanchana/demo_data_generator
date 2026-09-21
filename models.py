"""
Request and response models for the demo data generator API.

Geography and Domain are both plain free-text strings now, not enums:

- Geography used to be a strict pycountry-backed Continent/Country/State
  struct requiring three dropdowns. It's now whatever the user types --
  "Singanallur", "Tamil Nadu", "near Coimbatore", anything. The LLM itself
  resolves that text to a real place (locality -> district/state ->
  country) as part of the existing data generation call in
  Agents/data_generator_agent.py -- no separate resolution agent, no extra
  LLM call. See that file's resolve-then-generate flow.
- Domain still has a dropdown in the frontend for quick-pick, but the
  backend never enforced a closed enum in the prompt anyway -- the enum
  was only a Pydantic-layer constraint. Dropping it just means someone
  typing a domain that isn't in the dropdown's list flows straight through
  instead of being rejected.
- Subdomain was already free text end-to-end -- unchanged.
"""

from typing import Any

from pydantic import BaseModel, Field


class ProfileMeta(BaseModel):
    """
    Provenance only -- the frontend has already resolved which features are
    critical/optional/N-A for this profile (via its own applicability rules)
    and which of them are selected for this run. The backend does not
    re-derive applicability from this; it's carried through purely so runs
    can be traced back to the profile/target/run configuration that
    produced them (logging, history, debugging a bad run later).
    """
    id:              str
    name:            str
    catalog_version: str | None = None
    target_id:       str | None = None
    target_name:     str | None = None
    mode:            str | None = Field(
        None, description="'table' | 'api' | 'hybrid' as chosen in the frontend. "
                           "'table' has no effect on the backend today -- there is "
                           "no direct-DB-write path, only the APM save flow -- so "
                           "'table' and 'api'/'hybrid' currently behave identically "
                           "at generation time; the distinction only matters once a "
                           "future save/push step is mode-aware."
    )
    seed:    str | None = None
    top_up:  bool = Field(
        False, description="'Top up an existing dataset instead of regenerating', "
                            "as chosen in the frontend. Carried through for "
                            "provenance/logging only -- the backend has no "
                            "existing-key-reuse logic yet, so this currently has "
                            "no effect on generation. Needs its own design when "
                            "that capability is built."
    )


class ResolvedFeature(BaseModel):
    """One feature the frontend has already decided to include in this run
    (applicability resolved, any manual override applied)."""
    id:             str = Field(..., description="Feature id from the frontend catalog")
    name:           str = Field(..., description="Feature display name, e.g. 'Customer Master'")
    data_templates: list[str] = Field(
        ..., min_length=1,
        description="The feature's dataTemplates -- a feature can span multiple "
                     "screens/entities (e.g. 'approval_requests' + "
                     "'approval_steps'). Each one is resolved to a real "
                     "module+screen independently. See "
                     "service/feature_resolver.py for how that resolution "
                     "happens (KMS first, then a manual mapping, until KMS "
                     "has real data)."
    )
    row_count: int | None = Field(None, ge=1, le=100, description="Overrides the request-level row_count for every screen under this feature")


class GenerateRequest(BaseModel):
    profile: ProfileMeta
    domain:    str | None = Field(None, description="Resolved from the profile's Industry dimension selection, e.g. 'Manufacturing'")
    subdomain: str | None = Field(None, description="Optional further specialisation, if the frontend's dimensions ever add one")
    geography: str | None = Field(None, description="Resolved from the profile's Geography dimension selection, e.g. 'India'")
    features:  list[ResolvedFeature] = Field(..., min_length=1)
    row_count: int | None = Field(None, ge=1, le=100, description="Default rows per screen (default 20); a feature's own row_count overrides this")


class ScreenResult(BaseModel):
    status:               str
    message:               str | None = None
    feature_id:             str | None = None
    feature_name:           str | None = None
    data_template:          str | None = None
    resolved_via:           str | None = Field(
        None, description="How data_template was resolved to a module/screen: 'kms', 'manual_mapping', or None if unresolved"
    )
    fields_source:          str | None = Field(
        None, description="Where this screen's fields came from: 'kms' (KMS record's own field list, used as primary "
                           "when available) or 'architecture_agent' (parsed from the real component source)"
    )
    screen:                str
    resolved_path:          str | None = None
    fields:                 list[dict] = []
    rows:                   list[dict] = []
    row_count:              int = 0
    apm_ready:              bool = False
    apm_disabled_reason:    str | None = None
    resolved_geography:     str | None = Field(
        None, description="What the agent resolved the typed geography text to, e.g. 'Tamil Nadu, India'"
    )


class GenerateResponse(BaseModel):
    profile_id: str
    results:    list[ScreenResult] = []


class SaveRowRequest(BaseModel):
    module: str = Field(..., description="Module name as submitted to /generate")
    screen: str = Field(..., description="Screen name whose row this is")
    row:    dict[str, Any] = Field(..., description="A single generated/edited preview row")


class SaveRowResponse(BaseModel):
    ok:          bool
    status_code: int | None = None
    error:       str | None = None
    response:    Any = None
    missing_required_fields: list[str] = Field(
        default_factory=list,
        description="APM-required fields (per swagger.json 'required') that were "
                     "absent or empty in this row. Save is still attempted -- this "
                     "is a warning, not a hard block -- but APM itself is likely "
                     "to reject the save if this list is non-empty.",
    )
    unmatched_apm_fields: list[str] = Field(
        default_factory=list,
        description="APM schema fields that had no exact or fuzzy-matched "
                     "counterpart anywhere in the submitted row, so nothing was "
                     "sent for them.",
    )
    unmatched_frontend_fields: list[str] = Field(
        default_factory=list,
        description="Keys present in the submitted row that didn't match any "
                     "APM schema field (exactly or fuzzily), so they were dropped "
                     "from the save body.",
    )