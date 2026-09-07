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

    # Resolved by main.py per screen, BEFORE this graph runs, using
    # Agents/dependency_agent.py's LLM-based dependency detection plus
    # either in-run generated rows or service/testdb_client.py's real
    # TESTDB lookup (see main.py's Phase 1.5 / Phase 3 wiring). Maps a
    # field_name on THIS screen to a list of real, already-existing values
    # it must be drawn from -- e.g. {"SkillDomainCode": ["AGRI-DOM-001",
    # "AGRI-DOM-002"]}. Empty ({}) when this screen has no detected
    # dependencies, or when a dependency was detected but neither in-run
    # data nor TESTDB had anything usable for it -- in either case
    # generation proceeds exactly as before (the field is invented).
    dependency_field_values: dict

    # --- Data generator agent outputs ---
    generated_rows:      list[dict]
    resolved_geography:  str | None   # what geography text was resolved to, e.g.
                                      # "Tamil Nadu, India" -- None if geography was
                                      # disabled/not provided for this screen
    generation_error:    str | None