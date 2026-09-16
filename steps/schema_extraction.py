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
