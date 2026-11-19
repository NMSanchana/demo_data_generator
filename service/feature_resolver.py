"""
Resolves a frontend feature's dataTemplate to a real {module, screen} pair.

Everything comes from the KMS collection (pie_knowledge_v2, see service/kms_catalog.py):

  1. The dataTemplate is a KMS screen_id (or alias) -> exact match.
     (The Features page sends each feature's screen_id as its dataTemplate.)
  2. Otherwise it is matched by display name, but only if that name points at exactly
     one screen.
  3. Otherwise it is reported as unresolved -- never guessed.

Returned module names (two different vocabularies, so two keys):
  module      the single KMS module code, cleaned (a combined "A, B" text is cut to its first part).
              Used for source-repository (component folder) resolution and for prompts.
  apm_module  the name to give the APM resolver: the canonical APM module when the KMS module maps
              to one (every part of a combined value is tried, not just the first), else `module`.
              See service/module_names.py.

kms_fields: the form fields KMS holds for the screen (FieldKnowledge points, audit columns and
grid/joined-table columns already filtered out, see kms_catalog.select_screen_fields).
When empty, main.py falls back to parsing the real source.
"""

import logging

from service import kms_catalog, module_names

logger = logging.getLogger(__name__)


async def resolve_feature_screen(data_template: str) -> dict:
    """
    Returns:
      {"ok": True, "resolved_via": "kms", "module": str, "apm_module": str, "screen": str,
       "screen_id": str, "generatable": bool, "kms_fields": list[dict] | None}
      or
      {"ok": False, "error": str}
    """
    try:
        hit = await kms_catalog.lookup_screen(data_template)
    except Exception as e:
        logger.warning("feature_resolver: KMS lookup failed for %r (%s)", data_template, e)
        return {"ok": False, "error": f"KMS is unreachable, so '{data_template}' could not be resolved: {e}"}

    if hit is None:
        logger.warning("feature_resolver: %r is not a screen in KMS", data_template)
        return {
            "ok": False,
            "error": (
                f"'{data_template}' is not a screen in the KMS collection. "
                "Pick the feature from the KMS catalog (Features page) so its screen id is used."
            ),
        }

    if hit.get("generatable") is False:
        return {
            "ok": False,
            "error": f"'{hit['screen_name']}' is a report screen ({hit.get('category') or 'Report'}) -- it holds no data to generate.",
        }

    kms_fields: list[dict] | None = None
    try:
        info = await kms_catalog.get_screen_fields(hit["screen_id"])
        kms_fields = info["fields"] or None
    except Exception as e:
        logger.warning("feature_resolver: could not read KMS fields for %r (%s) -- falling back to source parsing", hit["screen_id"], e)

    module = module_names.clean_module_code(hit["module"])
    apm_module = module_names.apm_module_name(hit["module"])
    logger.info("feature_resolver: %r -> kms module=%r apm module=%r screen=%r (%d KMS fields)",
                data_template, module, apm_module, hit["screen_name"], len(kms_fields or []))
    return {
        "ok": True, "resolved_via": "kms", "module": module, "apm_module": apm_module,
        "screen": hit["screen_name"], "screen_id": hit["screen_id"], "generatable": True,
        "kms_fields": kms_fields,
    }