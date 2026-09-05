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


class GenerateRequest(BaseModel):
    module:    str = Field(..., description="ERP module name, e.g. 'Skill Management'")
    screen:    str | None = Field(
        None,
        description="Screen name, e.g. 'Skill Domain'. Omit to generate for every real screen under this module.",
    )
    domain:    str = Field(..., min_length=1, description="Domain to flavor the generated vocabulary/content -- e.g. 'Agriculture & Farming', or anything typed")
    subdomain: str | None = Field(None, description="Optional further specialisation, e.g. 'Organic Farming'")
    geography: str = Field(..., min_length=1, description="Any location text -- a locality, city, state, or country. Resolved to a real place by the generation agent.")
    row_count: int | None = Field(None, ge=1, le=100, description="Rows to generate per screen (default 20)")


class ScreenResult(BaseModel):
    status:               str
    message:               str | None = None
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
    module:  str
    results: list[ScreenResult] = []


class SettingsSaveRequest(BaseModel):
    module:        str = Field(..., description="ERP module name, e.g. 'Skill Management'")
    screen:        str | None = Field(
        None,
        description="Screen name, e.g. 'Skill Domain'. Omit/None to save the module-level default "
                    "that applies to every screen under this module unless overridden.",
    )
    use_domain:    bool = Field(True, description="Whether the domain component is enabled")
    use_subdomain: bool = Field(True, description="Whether the subdomain component is enabled")
    use_geography: bool = Field(True, description="Whether the geography component is enabled")


class SettingsResponse(BaseModel):
    module:        str
    screen:        str | None = None
    use_domain:    bool
    use_subdomain: bool
    use_geography: bool
    is_default:    bool = Field(
        ..., description="True if this came from the all-enabled fallback (no row saved yet)"
    )


class SaveRowRequest(BaseModel):
    module: str = Field(..., description="Module name as submitted to /generate")
    screen: str = Field(..., description="Screen name whose row this is")
    row:    dict[str, Any] = Field(..., description="A single generated/edited preview row")


class SaveRowResponse(BaseModel):
    ok:          bool
    status_code: int | None = None
    error:       str | None = None
    response:    Any = None