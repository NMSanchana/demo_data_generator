"""
Schema extraction step — deterministic (no LLM call), cached.

This replaces the old codebase's Agents/analysis_agent.py, which used an
LLM to re-extract fields from the same HTML on every single request. Field
discovery is structural parsing of real source code, not "demo data"
generation, so doing it deterministically is strictly better: faster,
cheaper, and reads ground truth off the real repo instead of asking a
model to summarize HTML it just saw once (see Section 4 of the
remediation spec).

Named a "step" rather than an "agent" throughout the codebase so the
naming doesn't imply an LLM call that no longer happens.
"""

import logging

from tools import schema_cache
from tools.schema_parser import parse_component_html

logger = logging.getLogger(__name__)


def extract_fields(path: str, component_html: str) -> list[dict]:
    """
    Extract fields for one resolved screen, using the disk-backed cache
    keyed on the resolved path so the same screen is never re-parsed on a
    later request.
    """
    if schema_cache.enabled():
        cached = schema_cache.get(path)
        if cached is not None:
            logger.info("schema_extraction: cache HIT for %s (%d fields)", path, len(cached))
            return cached

    fields = parse_component_html(component_html)

    unclassified = [f["field_name"] for f in fields if f["kind"] == "unclassified"]
    if unclassified:
        logger.info("schema_extraction: unclassified fields in %r: %s", path, unclassified)

    if schema_cache.enabled():
        schema_cache.set(path, fields)
        logger.info("schema_extraction: cache MISS for %s -- parsed and cached (%d fields)", path, len(fields))

    return fields
