import logging

from Agents.apm_resolver_agent import resolve_apm_schema

logger = logging.getLogger(__name__)


async def resolve_for_generation(module: str, screen: str) -> dict:
    result = await resolve_apm_schema(module, screen)
    if not result.get("ok"):
        logger.info("apm_schema_resolution_step: %s/%s not APM-ready: %s", module, screen, result.get("error"))
    return result
