"""
Static ground truth for the APM Service Resolver — the module manifest
(module name -> path prefix -> cluster). This plays the same "Level 1
ground truth" role that the source repo tree plays for the Architecture
Resolver, except here it's a fixed manifest instead of a fetched tree,
since APM's module directory isn't itself a JSON endpoint.

Ported from the old build's equivalent module manifest file — content unchanged,
symbols renamed per the "never name the real ERP system" product decision.
The document host is read from the APM_DOC_HOST env var rather than
hardcoded, so no real hostname ever needs to live in source.
"""

import os

APM_MODULES = [
    {"module": "Accounts", "path": "as", "cluster": "HRFinance"},
    {"module": "Admin", "path": "ads", "cluster": "Business"},
    {"module": "AMP", "path": "amp", "cluster": "Engagement"},
    {"module": "Analytics", "path": "ans", "cluster": "Business"},
    {"module": "Automation", "path": "auto", "cluster": "Platform"},
    {"module": "CMGM", "path": "cmgm", "cluster": "Engagement"},
    {"module": "CMS", "path": "cms", "cluster": "Engagement"},
    {"module": "Collab", "path": "cbs", "cluster": "Engagement"},
    {"module": "Collaboration", "path": "collab", "cluster": "Engagement"},
    {"module": "Communication", "path": "comm", "cluster": "Engagement"},
    {"module": "Compliance", "path": "comp", "cluster": "Platform"},
    {"module": "Correspondence", "path": "corr", "cluster": "Engagement"},
    {"module": "Costing", "path": "cts", "cluster": "HRFinance"},
    {"module": "CRM", "path": "crm", "cluster": "Business"},
    {"module": "Devadmin", "path": "ds", "cluster": "Platform"},
    {"module": "DMS", "path": "dms", "cluster": "Engagement"},
    {"module": "DXP", "path": "dxp", "cluster": "Platform"},
    {"module": "ECP", "path": "ecp", "cluster": "Engagement"},
    {"module": "Enablement", "path": "ena", "cluster": "Engagement"},
    {"module": "Entitlement", "path": "ent", "cluster": "Platform"},
    {"module": "FAM", "path": "fam", "cluster": "HRFinance"},
    {"module": "FBCK", "path": "fbck", "cluster": "Engagement"},
    {"module": "FLS", "path": "fls", "cluster": "Platform"},
    {"module": "FM", "path": "fms", "cluster": "Business"},
    {"module": "Framework", "path": "fws", "cluster": "Framework"},
    {"module": "IceImport", "path": "ice", "cluster": "Platform"},
    {"module": "IDMS", "path": "idms", "cluster": "Platform"},
    {"module": "JobEngine", "path": "je", "cluster": "Platform"},
    {"module": "KMS", "path": "kms", "cluster": "Platform"},
    {"module": "Logistics", "path": "ls", "cluster": "Business"},
    {"module": "Maintenance", "path": "ms", "cluster": "Business"},
    {"module": "Marketing", "path": "mkts", "cluster": "Business"},
    {"module": "Meeting", "path": "meet", "cluster": "Engagement"},
    {"module": "MM", "path": "mms", "cluster": "Business"},
    {"module": "OKR", "path": "okr", "cluster": "Engagement"},
    {"module": "Partner", "path": "pms", "cluster": "Platform"},
    {"module": "PAY", "path": "pay", "cluster": "HRFinance"},
    {"module": "PayRoll", "path": "prs", "cluster": "HRFinance"},
    {"module": "PERM", "path": "perm", "cluster": "Engagement"},
    {"module": "PIE", "path": "pie", "cluster": "Platform"},
    {"module": "QMS", "path": "qms", "cluster": "Business"},
    {"module": "Recruitment", "path": "rec", "cluster": "HRFinance"},
    {"module": "SkillManagement", "path": "sms", "cluster": "HRFinance"},
    {"module": "SQLWorkBench", "path": "sws", "cluster": "Platform"},
    {"module": "TCMS", "path": "tcms", "cluster": "Platform"},
    {"module": "TMS", "path": "tms", "cluster": "HRFinance"},
    {"module": "Vehicle", "path": "vs", "cluster": "Business"},
    {"module": "WorkInstruction", "path": "wis", "cluster": "Engagement"},
]


def _apm_doc_host() -> str:
    host = os.getenv("APM_DOC_HOST")
    if not host:
        raise RuntimeError("APM_DOC_HOST must be set in .env (see .env.example).")
    return host.rstrip("/")


def swagger_url(path_prefix: str, module_name: str) -> str:
    return f"{_apm_doc_host()}/{path_prefix}/swagger/{module_name}/swagger.json"


def call_url(path_prefix: str, endpoint_path: str) -> str:
    return f"{_apm_doc_host()}/{path_prefix}{endpoint_path}"
