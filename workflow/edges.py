"""
LangGraph edge conditions for the per-screen generation pipeline.

Pipeline: data_generator_agent -> identifier_dedup -> END
"""

import logging

logger = logging.getLogger(__name__)


def after_generation(state: dict) -> str:
    """
    After the data generator agent: proceed to the dedup safety net only
    if we have rows. Otherwise route to the error terminal.
    """
    if state.get("generation_error") or not state.get("generated_rows"):
        logger.warning(
            "after_generation: routing to error — generation_error=%r, rows=%d",
            state.get("generation_error"),
            len(state.get("generated_rows") or []),
        )
        return "error"
    return "dedup"
