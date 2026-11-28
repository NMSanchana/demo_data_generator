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

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# How a run is meant to work:
#   full    -> every screen that applies to the profile; creates a new version
#   partial -> only the screens the user picked; creates a new version
#   add     -> adds rows to the profile's current version (same screen -> more rows, new screen -> new entry)
GenerationMode = Literal["full", "partial", "add"]


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
    top_up:  bool = Field(
        False, description="'Top up an existing dataset instead of regenerating'. "
                            "The run is appended to the profile's current stored version "
                            "instead of creating a new one (see service/run_store.py). "
                            "Kept for older clients; it is always in step with mode ('add')."
    )
    mode: GenerationMode | None = Field(
        None, description="'full', 'partial' or 'add'. Saved with the run and shown in History."
    )

    @model_validator(mode="after")
    def _sync_mode_and_top_up(self):
        # Older clients only send top_up; newer ones send mode. Keep the two consistent.
        if self.mode is None:
            self.mode = "add" if self.top_up else "partial"
        self.top_up = self.mode == "add"
        return self


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
    auto_add_parents: bool = Field(
        True, description="When a selected screen has a foreign key to a screen that is not selected (and has no stored data), "
                           "generate that parent screen first and use its values. False = leave it out; the child then gets invented values."
    )


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
    module:                 str | None = Field(
        None, description="Module name to send back to /save-row (the APM-side module this screen resolved to)"
    )
    kms_module:             str | None = Field(None, description="The KMS module code this screen belongs to")
    screen_id:              str | None = Field(None, description="KMS screen id")
    resolved_path:          str | None = None
    fields:                 list[dict] = []
    rows:                   list[dict] = []
    row_count:              int = 0
    fields_total:           int | None = Field(
        None, description="How many fields the screen has in total (before any MAX_FIELDS_PER_SCREEN limit)"
    )
    fields_capped:          bool = Field(
        False, description="True when only the first MAX_FIELDS_PER_SCREEN fields were generated"
    )
    apm_ready:              bool = False
    apm_disabled_reason:    str | None = None
    resolved_geography:     str | None = Field(
        None, description="What the agent resolved the typed geography text to, e.g. 'Tamil Nadu, India'"
    )
    auto_added:             bool = Field(False, description="True when this screen was not selected: it was added because another screen needs it")
    auto_added_for:         list[str] = Field(default_factory=list, description="Screens that needed this auto-added screen")
    relationship_notes:     list[str] = Field(
        default_factory=list, description="Plain-language notes on how this screen used (or could not use) its parent screens' data"
    )


class StorageInfo(BaseModel):
    """What happened when this run was stored in the profile's own database."""
    saved:        bool = False
    version_no:   int | None = None
    run_id:       int | None = None
    appended:     bool = Field(False, description="True when 'top up' added to the current version instead of creating a new one")
    rows_saved:   int = 0
    db_name:      str | None = None
    error:        str | None = None


class LogEntry(BaseModel):
    t:       float = Field(0, description="Seconds since the run started")
    level:   Literal["info", "warning", "error"] = "info"
    screen:  str | None = Field(None, description="The screen this line is about; None for run-wide lines")
    message: str


class GenerateResponse(BaseModel):
    profile_id: str
    results:    list[ScreenResult] = []
    storage:    StorageInfo | None = None
    logs:       list[LogEntry] = Field(default_factory=list, description="The readable log of this run, grouped by screen in the UI")


class SaveRowRequest(BaseModel):
    module: str = Field(..., description="Module name as submitted to /generate")
    screen: str = Field(..., description="Screen name whose row this is")
    row:    dict[str, Any] = Field(..., description="A single generated/edited preview row")


class SaveRowResponse(BaseModel):
    ok:          bool
    status_code: int | None = None
    error:       str | None = None
    detail:      str | None = Field(
        None, description="APM's own explanation of a failure (ErrorBody / ErrorInnerException), verbatim."
    )
    url:         str | None = Field(None, description="The exact URL the row was posted to (shows which APM host was used).")
    filled_from_login: list[str] = Field(
        default_factory=list,
        description="System/audit fields (CreatedById, TenantId, ...) filled from the Login header.",
    )
    login_warnings: list[str] = Field(default_factory=list, description="Problems spotted in the Login header.")
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

# ---------------------------------------------------------------------
# /plan -- what Generate will do about related screens, before anything is generated
# ---------------------------------------------------------------------

class PlanRequest(BaseModel):
    profile_id: str | None = Field(None, description="Used to find parent data already stored in the profile's current version")
    features: list[ResolvedFeature] = Field(..., min_length=1)
    auto_add_parents: bool = True


class PlanNotice(BaseModel):
    level:   Literal["info", "warning"]
    kind:    str = Field(..., description="auto_added | in_run | reused | not_generated | unavailable | limit")
    screen:  str | None = Field(None, description="KMS id of the parent screen the notice is about")
    parent:  str | None = Field(None, description="Display name of that parent screen")
    message: str


class PlannedScreen(BaseModel):
    screen_id: str
    name:      str
    auto_added: bool = False


class PlanResponse(BaseModel):
    notices:  list[PlanNotice] = []
    order:    list[PlannedScreen] = Field(default_factory=list, description="Every screen of the run, parents first")
    auto_added: list[PlannedScreen] = []
    unresolved: list[str] = Field(default_factory=list, description="Requested screens KMS does not know (they cannot be planned)")