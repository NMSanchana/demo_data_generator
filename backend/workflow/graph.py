"""
LangGraph pipeline for the demo data generator — the per-screen generation
stage.

Why resolution isn't a graph node here: architecture resolution, schema
extraction, and APM schema resolution (Section 3 of the remediation spec)
all need to run across EVERY resolved screen in a request before the
shared cross-screen entity-assignment call can happen (a full-module run
needs to know every screen's picklist fields before it can decide on one
consistent named-entity pool). That's an inherent fan-out-then-join across
screens that a single linear StateGraph doesn't model cleanly, so that
phase runs as plain async calls into Agents/architecture_agent.py,
steps/schema_extraction.py and steps/apm_schema_resolution.py, orchestrated
directly by main.py's /generate handler (each of those pieces still does
its own caching, so repeated screens/requests stay cheap).

What IS a clean, genuinely sequential per-screen pipeline is what happens
once a screen's fields (and APM type hints, and the shared entity map) are
already known: generate rows, then run the identifier-dedup safety net.
That's what this graph models:

  data_generator_agent
      |-- error --> [END with error]
      |-- dedup  --> identifier_dedup
                         |
                      [END with result]
"""

import logging

from langgraph.graph import StateGraph, END

from workflow.state import GeneratorState
from workflow.edges import after_generation
from Agents.data_generator_agent import data_generator_agent_node
from steps.identifier_dedup import dedup_identifiers

logger = logging.getLogger(__name__)


async def identifier_dedup_node(state: dict) -> dict:
    rows = state.get("generated_rows") or []
    fields = state.get("generated_fields") or []
    deduped = dedup_identifiers(rows, fields)
    return {"generated_rows": deduped}


async def error_node(state: dict) -> dict:
    error = state.get("generation_error") or "Unknown error in generation pipeline"
    logger.error("error_node: %s", error)
    return {"generation_error": error, "generated_rows": []}


def build_graph() -> StateGraph:
    graph = StateGraph(GeneratorState)

    graph.add_node("data_generator_agent", data_generator_agent_node)
    graph.add_node("identifier_dedup", identifier_dedup_node)
    graph.add_node("error", error_node)

    graph.set_entry_point("data_generator_agent")

    graph.add_conditional_edges(
        "data_generator_agent",
        after_generation,
        {"dedup": "identifier_dedup", "error": "error"},
    )

    graph.add_edge("identifier_dedup", END)
    graph.add_edge("error", END)

    return graph.compile()


# Single compiled graph instance reused across requests
generator_graph = build_graph()
