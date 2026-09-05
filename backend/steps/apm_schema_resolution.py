"""
apm_schema_resolution_step — runs BEFORE data generation (not only at save
time), specifically so the Data Generator Agent can be told the REAL
target type of every field (string vs integer vs boolean) and generate
save-compatible values from the start. See Section 6.3 of the remediation
spec for why this ordering is the actual fix for the byte/integer 400
error observed previously.

If resolution fails, generation still proceeds (preview always works even
if APM's schema can't be found) — the caller just marks the screen's
result as apm_ready=False with a reason, so Save is disabled with a clear
explanation instead of silently failing later.
"""

import logging

from Agents.apm_resolver_agent import resolve_apm_schema

logger = logging.getLogger(__name__)


async def resolve_for_generation(module: str, screen: str) -> dict:
    result = await resolve_apm_schema(module, screen)
    if not result.get("ok"):
        logger.info("apm_schema_resolution_step: %s/%s not APM-ready: %s", module, screen, result.get("error"))
    return result
