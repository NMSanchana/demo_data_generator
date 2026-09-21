"""
Hand-curated fallback mapping: frontend feature dataTemplate name -> real
{module, screen} pair.

This is a TEMPORARY stand-in for service/feature_resolver.py's KMS lookup,
which is stubbed until the PIE/KMS team's Qdrant instance actually has data
in it. Once KMS can resolve this itself, entries here become redundant one
by one (KMS is tried first) -- you don't need to remove them, unused
entries just stop being reached.

Add an entry here for every feature you want to actually run through
/generate today. Key = the CatalogFeature's dataTemplate string exactly as
the frontend sends it; value = the real module + screen name that
Agents.architecture_agent.resolve_screen() can resolve against the source
repo (same names you'd type into the old module/screen fields).

Example (uncomment and adjust once you have real names to confirm):

    FEATURE_SCREEN_MAP: dict[str, dict[str, str]] = {
        "CustomerMaster": {"module": "Accounts", "screen": "Customer Master"},
        "VendorMaster":   {"module": "Accounts", "screen": "Vendor Master"},
    }
"""

FEATURE_SCREEN_MAP: dict[str, dict[str, str]] = {}
