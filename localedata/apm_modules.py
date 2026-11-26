import os

APM_MODULES = [
    {"module": "Accounts", "path": "as", "cluster": "HRFinance"},
    {"module": "Admin", "path": "ads", "cluster": "Business"},
    {"module": "AMP", "path": "amp", "cluster": "Engagement"},
    {"module": "Analytics", "path": "ans", "cluster": "Business"},
    {"module": "Automation", "path": "auto", "cluster": "Platform"},
    {"module": "BizProcess", "path": "bp", "cluster": "Platform"},
    {"module": "CMGM", "path": "cmgm", "cluster": "Engagement"},
    {"module": "CMS", "path": "cms", "cluster": "Engagement"},
    {"module": "Collab", "path": "cbs", "cluster": "Engagement"},
    {"module": "Collaboration", "path": "collab", "cluster": "Engagement"},
    {"module": "Commercial", "path": "cml", "cluster": "Platform"},
    {"module": "Communication", "path": "comm", "cluster": "Engagement"},
    {"module": "Compliance", "path": "comp", "cluster": "Platform"},
    {"module": "Correspondence", "path": "corr", "cluster": "Engagement"},
    {"module": "Costing", "path": "cts", "cluster": "HRFinance"},
    {"module": "CRM", "path": "crm", "cluster": "Business"},
    {"module": "CRMSales", "path": "crms", "cluster": "Business"},
    {"module": "Devadmin", "path": "ds", "cluster": "Platform"},
    {"module": "DMS", "path": "dms", "cluster": "Engagement"},
    {"module": "DXP", "path": "dxp", "cluster": "Platform"},
    {"module": "EAI", "path": "eai", "cluster": "Platform"},
    {"module": "ECP", "path": "ecp", "cluster": "Engagement"},
    {"module": "Enablement", "path": "ena", "cluster": "Engagement"},
    {"module": "Entitlement", "path": "ent", "cluster": "Platform"},
    {"module": "FAM", "path": "fam", "cluster": "HRFinance"},
    {"module": "FBCK", "path": "fbck", "cluster": "Engagement"},
    {"module": "FLS", "path": "fls", "cluster": "Platform"},
    {"module": "FM", "path": "fms", "cluster": "Business"},
    {"module": "Framework", "path": "fws", "cluster": "Framework"},
    {"module": "Hubs", "path": "hubs", "cluster": "Framework"},
    {"module": "IceImport", "path": "ice", "cluster": "Platform"},
    {"module": "IceImportHub", "path": "hubs/iceimport-run", "cluster": "Platform"},
    {"module": "IDMS", "path": "idms", "cluster": "Platform"},
    {"module": "JobEngine", "path": "je", "cluster": "Platform"},
    {"module": "KMS", "path": "kms", "cluster": "Platform"},
    {"module": "Logistics", "path": "ls", "cluster": "Business"},
    {"module": "Maintenance", "path": "ms", "cluster": "Business"},
    {"module": "Marketing", "path": "mkts", "cluster": "Business"},
    {"module": "Meeting", "path": "meet", "cluster": "Engagement"},
    {"module": "MM", "path": "mms", "cluster": "Business"},
    {"module": "NFA", "path": "nfa", "cluster": "HRFinance"},
    {"module": "OKR", "path": "okr", "cluster": "Engagement"},
    {"module": "Partner", "path": "pms", "cluster": "Platform"},
    {"module": "PAY", "path": "pay", "cluster": "HRFinance"},
    {"module": "PayRoll", "path": "prs", "cluster": "HRFinance"},
    {"module": "PERM", "path": "perm", "cluster": "Engagement"},
    {"module": "PIE", "path": "pie", "cluster": "Platform"},
    {"module": "PlatformAdmin", "path": "pfa", "cluster": "Platform"},
    {"module": "QMS", "path": "qms", "cluster": "Business"},
    {"module": "Recruitment", "path": "rec", "cluster": "HRFinance"},
    {"module": "SkillManagement", "path": "sms", "cluster": "HRFinance"},
    {"module": "SQLWorkBench", "path": "sws", "cluster": "Platform"},
    {"module": "TCMS", "path": "tcms", "cluster": "Platform"},
    {"module": "TMS", "path": "tms", "cluster": "HRFinance"},
    {"module": "Vehicle", "path": "vs", "cluster": "Business"},
    {"module": "WorkInstruction", "path": "wis", "cluster": "Engagement"},
]


def _clean_host(host: str) -> str:
    """Accept a pasted docs-page address too: keep only scheme://host (drop /GB5Documentation/..., /index.html)."""
    host = host.strip().rstrip("/")
    for marker in ("/GB5Documentation", "/index.html", "/swagger"):
        i = host.find(marker)
        if i > 0:
            host = host[:i]
    return host.rstrip("/")


def _apm_doc_host() -> str:
    host = os.getenv("APM_DOC_HOST")
    if not host:
        raise RuntimeError("APM_DOC_HOST must be set in .env (see .env.example).")
    return _clean_host(host)


def _apm_api_host() -> str:
    """Host used to POST rows. Defaults to the docs host; set APM_API_HOST if saving goes elsewhere."""
    host = os.getenv("APM_API_HOST")
    return _clean_host(host) if host else _apm_doc_host()


def swagger_url(path_prefix: str, module_name: str) -> str:
    return f"{_apm_doc_host()}/{path_prefix}/swagger/{module_name}/swagger.json"


def call_url(path_prefix: str, endpoint_path: str) -> str:
    return f"{_apm_api_host()}/{path_prefix}{endpoint_path}"