from typing import TypedDict


class GeneratorState(TypedDict, total=False):
    # --- Inputs (filled in by main.py before invoking the graph) ---
    module:    str
    screen:    str
    domain:    str | None
    subdomain: str | None
    geography: str | None            # free text as typed by the user ("Singanallur"),
                                      # or -- for every screen after the first in a
                                      # full-module request -- the already-resolved
                                      # place from the first screen's generation call
                                      # (see geography_already_resolved below and
                                      # Agents/data_generator_agent.py's module docstring)
    geography_already_resolved: bool  # False for the first screen (geography is raw
                                      # user text, the agent must resolve it itself);
                                      # True for every later screen in the same
                                      # request (geography is already a resolved place,
                                      # reuse it as-is instead of re-resolving)
    row_count: int | None

    use_domain:    bool
    use_subdomain: bool
    use_geography: bool

    # Already-resolved by the pre-pass in main.py (architecture_agent +
    # schema_extraction step) -- this graph does not re-resolve or
    # re-extract.
    generated_fields: list[dict]

    # Already-resolved by steps/apm_schema_resolution.py -- {} if APM
    # resolution failed for this screen (generation still proceeds).
    apm_type_hints: dict

    # Built once per request (main.py) across every resolved screen's
    # picklist fields -- {} if there were no picklist fields anywhere.
    entity_assignment_map: dict

    # --- Data generator agent outputs ---
    generated_rows:      list[dict]
    resolved_geography:  str | None   # what geography text was resolved to, e.g.
                                      # "Tamil Nadu, India" -- None if geography was
                                      # disabled/not provided for this screen
    generation_error:    str | None