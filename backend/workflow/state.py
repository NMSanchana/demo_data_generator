"""
LangGraph state definition for the demo data generator's per-screen
generation pipeline.

Resolution (architecture + schema extraction + APM schema resolution) and
the cross-screen shared entity assignment happen ONCE per request, across
every resolved screen, in main.py before this graph is invoked -- see the
module docstring in workflow/graph.py for why that fan-out lives outside
the graph. This state only covers what happens per screen from that point
on: row generation, then the code-only identifier dedup safety net.
"""

from typing import TypedDict


class GeneratorState(TypedDict, total=False):
    # --- Inputs (filled in by main.py before invoking the graph) ---
    module:    str
    screen:    str
    domain:    str | None
    subdomain: str | None
    geography: str | None            # human-readable display string, e.g. "Tamil Nadu, India"
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
    generated_rows:   list[dict]
    generation_error: str | None
