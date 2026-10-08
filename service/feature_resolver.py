"""
Resolves a frontend feature's dataTemplate to a real {module, screen} pair.

Everything comes from the KMS collection (pie_knowledge_v2, see service/kms_catalog.py):

  1. The dataTemplate is a KMS screen_id (or alias) -> exact match.
     (The Features page sends each feature's screen_id as its dataTemplate.)
  2. Otherwise it is matched by display name, but only if that name points at exactly
     one screen.
  3. Otherwise it is reported as unresolved -- never guessed.

The old fuzzy matcher over the previous collection (PIE_collection) and the hand-written
localedata/feature_screen_map.py were removed: KMS is now the single source.

kms_fields: the field names KMS holds for the screen (FieldKnowledge points). When present,
main.py uses them directly; when KMS has none, main.py falls back to parsing the real source.
"""

import logging

from service import kms_catalog

logger = logging.getLogger(__name__)


async def resolve_feature_screen(data_template: str) -> dict:
    """
    Returns:
      {"ok": True, "resolved_via": "kms", "module": str, "screen": str, "kms_fields": list[str] | None}
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

    kms_fields: list[str] | None = None
    try:
        names = await kms_catalog.get_screen_field_names(hit["screen_id"])
        kms_fields = names or None
    except Exception as e:
        logger.warning("feature_resolver: could not read KMS fields for %r (%s) -- falling back to source parsing", hit["screen_id"], e)

    logger.info("feature_resolver: %r -> module=%r screen=%r (%d KMS fields)",
                data_template, hit["module"], hit["screen_name"], len(kms_fields or []))
    return {"ok": True, "resolved_via": "kms", "module": hit["module"], "screen": hit["screen_name"], "kms_fields": kms_fields}