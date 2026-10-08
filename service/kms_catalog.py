"""
KMS catalog reader -- builds the Features tree and the Relationships graph
from the Qdrant collection `pie_knowledge_v2` (PIE's knowledge base).

Nothing here is hardcoded business data. Everything comes from KMS points:

  Features       <- UsageModule / UsageCapability / UsageFeature  (deterministic, v0.19.0)
  Relationships  <- FieldKnowledge points: each field's
                    field.database.{table, column, foreignKey, primaryKey, childTable}
                    and field.relatedMasters

Design notes
------------
* Plain REST via httpx (already in requirements.txt) -- same JSON you test in
  the Qdrant console, so it is easy to debug. No vectors are ever fetched.
* Every scroll asks for ONLY the payload keys it needs. DepartmentManifest /
  DepartmentKnowledge points are huge and are never touched.
* Env vars are read lazily (at call time), so it does not matter that
  main.py calls load_dotenv() after its imports.
* Result is cached for KMS_CACHE_SECONDS (default 300). If Qdrant is down, the
  last good snapshot is served instead of failing.
* KMS does NOT store min/max children per parent, so those two numbers are
  defaults and every relationship is flagged `defaultsApplied: true`.
"""

import asyncio
import logging
import os
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PAGE_SIZE = 1000

# KMS has no row-count data. Sensible defaults by category, clearly flagged.
ROWS_BY_CATEGORY = {
    "master data": 500,
    "transaction": 3000,
    "configuration": 50,
    "setup": 50,
    "report": 0,
}
DEFAULT_ROWS = 300

DEFAULT_MIN_PER_PARENT_REQUIRED = 1
DEFAULT_MIN_PER_PARENT_OPTIONAL = 0
DEFAULT_MAX_PER_PARENT = 5


# ----------------------------------------------------------------------
# Config / HTTP
# ----------------------------------------------------------------------

def _cfg() -> tuple[str, str, dict]:
    url = os.getenv("QDRANT_URL")
    if not url:
        host = os.getenv("QDRANT_HOST", "217.217.249.121")
        port = os.getenv("QDRANT_PORT", "6333")
        url = f"http://{host}:{port}"
    collection = os.getenv("QDRANT_COLLECTION", "pie_knowledge_v2")
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("QDRANT_API_KEY")
    if api_key:
        headers["api-key"] = api_key
    return url.rstrip("/"), collection, headers


def _cache_seconds() -> int:
    try:
        return int(os.getenv("KMS_CACHE_SECONDS", "300"))
    except ValueError:
        return 300


async def _scroll(client: httpx.AsyncClient, flt: dict, include: list[str]) -> list[dict]:
    """Scrolls every point matching `flt`, returning only the requested payload keys."""
    base, collection, headers = _cfg()
    out: list[dict] = []
    offset = None
    while True:
        body: dict[str, Any] = {
            "limit": PAGE_SIZE,
            "with_payload": {"include": include},
            "with_vector": False,
            "filter": flt,
        }
        if offset is not None:
            body["offset"] = offset
        resp = await client.post(f"{base}/collections/{collection}/points/scroll", json=body, headers=headers)
        resp.raise_for_status()
        result = resp.json()["result"]
        out.extend((p.get("payload") or {}) for p in result.get("points", []))
        offset = result.get("next_page_offset")
        if offset is None:
            break
    return out


def _type_filter(knowledge_type: str) -> dict:
    return {"must": [{"key": "knowledge_type", "match": {"value": knowledge_type}}]}


# Only the FieldKnowledge points that can tell us something about relationships:
# a declared FK, a related master, or a primary key (to learn which screen owns a table).
FIELD_FILTER = {
    "must": [{"key": "knowledge_type", "match": {"value": "FieldKnowledge"}}],
    "should": [
        {"must_not": [{"is_empty": {"key": "field.database.foreignKey"}}]},
        {"must_not": [{"is_empty": {"key": "field.relatedMasters"}}]},
        {"key": "field.database.primaryKey", "match": {"value": True}},
    ],
}
FIELD_INCLUDE = [
    "knowledge_id", "screen_id", "module", "field_name", "confidence",
    "field.database", "field.mandatory", "field.relatedMasters", "field.evidence",
]

MODULE_INCLUDE = ["module", "functionality", "content", "statistics", "capabilities", "product"]
CAPABILITY_INCLUDE = ["module", "capability", "content", "features", "ready_features"]
FEATURE_INCLUDE = [
    "knowledge_id", "product", "module", "functionality", "capability", "feature",
    "screen_id", "screen_name", "readiness", "category", "verified_claims",
    "review_claims", "ai_summary", "alias_screen_ids", "migration_gaps", "content",
]


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "unknown"


def _norm_table(name: Any) -> str:
    n = str(name or "").strip().upper()
    if n.startswith("DBO."):
        n = n[4:]
    return n


def _title(code: str) -> str:
    return (code or "Unassigned").replace("_", " ").title()


def _rows_for(category: str | None) -> int:
    return ROWS_BY_CATEGORY.get((category or "").strip().lower(), DEFAULT_ROWS)


def _owner_score(table: str, screen_id: str, module: str) -> int:
    """How well does a screen's name match a table's name?  MITEMGROUP <-> INVENTORY_ITEMGROUP."""
    t = re.sub(r"[^a-z0-9]", "", table.lower())
    if t[:1] in ("m", "t") and len(t) > 3:
        t_core = t[1:]
    else:
        t_core = t
    s = screen_id.lower()
    if module and s.startswith(module.lower() + "_"):
        s = s[len(module) + 1:]
    s = re.sub(r"[^a-z0-9]", "", s)
    if s == t_core or s == t:
        return 3
    if t_core and (t_core in s or s in t_core):
        return 1
    return 0


# ----------------------------------------------------------------------
# Relationships
# ----------------------------------------------------------------------

def build_relationships(field_points: list[dict], features_by_screen: dict[str, dict]) -> dict:
    """Derive entities + relationships from FieldKnowledge payloads."""
    # 1) Which screens declare a table's primary key? -> owner screen for the entity.
    pk_screens: dict[str, set[tuple[str, str]]] = {}
    all_tables: set[str] = set()
    # candidate relationships keyed by (parent_table, child_table, column)
    cands: dict[tuple[str, str, str], dict] = {}
    child_table_pairs: dict[tuple[str, str], dict] = {}

    for p in field_points:
        screen_id = p.get("screen_id") or ""
        module = (p.get("module") or "").lower()
        field = p.get("field") or {}
        db = field.get("database") or {}
        mand = field.get("mandatory") or {}
        table = _norm_table(db.get("table"))
        point_conf = p.get("confidence")
        point_conf = float(point_conf) if isinstance(point_conf, (int, float)) else 1.0

        if table:
            all_tables.add(table)
            if db.get("primaryKey") is True and screen_id:
                pk_screens.setdefault(table, set()).add((screen_id, module))

        if not table:
            continue

        required = bool(db.get("nullable") is False or mand.get("required"))
        evidence = [
            {"source": e.get("source"), "line": e.get("line"), "fact": e.get("fact")}
            for e in (field.get("evidence") or [])[:2] if isinstance(e, dict)
        ]

        def add(parent: str, parent_col: str, source: str, conf: float, one_to_one: bool):
            parent = _norm_table(parent)
            if not parent:
                return
            all_tables.add(parent)
            key = (parent, table, str(db.get("column") or p.get("field_name") or ""))
            c = cands.get(key)
            if c is None:
                c = cands[key] = {
                    "parent": parent, "child": table,
                    "childColumn": db.get("column") or p.get("field_name") or "",
                    "parentColumn": parent_col or "",
                    "source": source, "confidence": conf,
                    "required": required, "oneToOne": one_to_one,
                    "screens": set(), "modules": set(), "evidence": [],
                }
            else:
                # an actual DB foreign key always beats a "related master" hint
                if source == "kms_foreign_key" and c["source"] != "kms_foreign_key":
                    c.update(source=source, confidence=conf, parentColumn=parent_col or c["parentColumn"])
                c["required"] = c["required"] or required
                c["oneToOne"] = c["oneToOne"] or one_to_one
            if screen_id:
                c["screens"].add(screen_id)
            if module:
                c["modules"].add(module)
            for ev in evidence:
                if ev not in c["evidence"] and len(c["evidence"]) < 3:
                    c["evidence"].append(ev)

        fk = db.get("foreignKey")
        if isinstance(fk, dict) and fk.get("table"):
            add(fk["table"], str(fk.get("column") or ""), "kms_foreign_key", min(1.0, point_conf), db.get("primaryKey") is True)
        else:
            for rm in field.get("relatedMasters") or []:
                add(rm, "", "kms_related_master", 0.6, False)

        # grid / child-table hint: only trusted if it is a plain table name
        ct = db.get("childTable")
        if isinstance(ct, dict):
            ct = ct.get("table") or ct.get("name")
        ct = _norm_table(ct) if isinstance(ct, str) else ""
        if ct and ct != table:
            all_tables.add(ct)
            k2 = (table, ct)
            d = child_table_pairs.setdefault(k2, {"screens": set(), "modules": set()})
            if screen_id:
                d["screens"].add(screen_id)
            if module:
                d["modules"].add(module)

    # 2) Pick one owner screen per table.
    def owner_of(table: str) -> tuple[str | None, str]:
        options = sorted(pk_screens.get(table, set()))
        if not options:
            return None, ""
        options.sort(key=lambda o: (-_owner_score(table, o[0], o[1]), len(o[0]), o[0]))
        return options[0]

    # 3) Entities
    entities: dict[str, dict] = {}
    for t in sorted(all_tables):
        sid, mod = owner_of(t)
        feat = features_by_screen.get(sid) if sid else None
        entities[t] = {
            "id": f"ent-{t.lower()}",
            "name": t,
            "label": feat["name"] if feat else None,
            "featureId": sid if feat else None,
            "module": (feat or {}).get("moduleId") or mod or None,
            "estimatedRows": feat["estimatedRows"] if feat else DEFAULT_ROWS,
            "estimatedRowsBasis": "default_by_category" if feat else "default",
        }

    # 4) Relationships
    relationships: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    self_refs = 0
    for (parent, child, _col), c in sorted(cands.items()):
        if parent == child:
            self_refs += 1
            continue
        seen_pairs.add((parent, child))
        one = c["oneToOne"]
        required = c["required"]
        relationships.append({
            "id": f"rel-{child.lower()}-{_slug(c['childColumn'])}-{parent.lower()}",
            "fromEntityId": entities[parent]["id"],
            "toEntityId": entities[child]["id"],
            "kind": "one-to-one" if one else "one-to-many",
            "minPerParent": (1 if one else (DEFAULT_MIN_PER_PARENT_REQUIRED if required else DEFAULT_MIN_PER_PARENT_OPTIONAL)),
            "maxPerParent": 1 if one else DEFAULT_MAX_PER_PARENT,
            "required": required,
            "note": f"{child}.{c['childColumn']} \u2192 {parent}" + (f".{c['parentColumn']}" if c["parentColumn"] else ""),
            "source": c["source"],
            "confidence": c["confidence"],
            "defaultsApplied": True,
            "screens": sorted(c["screens"])[:12],
            "modules": sorted(c["modules"]),
            "evidence": c["evidence"],
        })

    for (parent, child), d in sorted(child_table_pairs.items()):
        if (parent, child) in seen_pairs or parent == child:
            continue
        relationships.append({
            "id": f"rel-{child.lower()}-grid-{parent.lower()}",
            "fromEntityId": entities[parent]["id"],
            "toEntityId": entities[child]["id"],
            "kind": "one-to-many",
            "minPerParent": 0,
            "maxPerParent": DEFAULT_MAX_PER_PARENT,
            "required": False,
            "note": f"{child} is a detail/grid table of {parent}",
            "source": "kms_child_table",
            "confidence": 0.5,
            "defaultsApplied": True,
            "screens": sorted(d["screens"])[:12],
            "modules": sorted(d["modules"]),
            "evidence": [],
        })

    # Only keep entities that take part in a relationship OR own a screen.
    linked = {r["fromEntityId"] for r in relationships} | {r["toEntityId"] for r in relationships}
    entity_list = [e for e in entities.values() if e["id"] in linked or e["featureId"]]

    # screen -> screens it depends on (parent table's owner screen)
    deps: dict[str, set[str]] = {}
    ent_by_id = {e["id"]: e for e in entities.values()}
    for r in relationships:
        parent_owner = ent_by_id[r["fromEntityId"]]["featureId"]
        if not parent_owner:
            continue
        for s in r["screens"]:
            if s != parent_owner and s in features_by_screen:
                deps.setdefault(s, set()).add(parent_owner)

    return {
        "entities": entity_list,
        "relationships": relationships,
        "dependencies": {k: sorted(v) for k, v in deps.items()},
        "stats": {
            "tables": len(all_tables),
            "entities": len(entity_list),
            "relationships": len(relationships),
            "selfReferencesSkipped": self_refs,
            "fieldPointsRead": len(field_points),
        },
    }


# ----------------------------------------------------------------------
# Features
# ----------------------------------------------------------------------

def build_catalog(module_pts: list[dict], cap_pts: list[dict], feat_pts: list[dict]) -> dict:
    module_info = {(m.get("module") or "").lower(): m for m in module_pts}
    cap_info = {((c.get("module") or "").lower(), (c.get("capability") or "")): c for c in cap_pts}

    products: dict[str, dict] = {}
    features: list[dict] = []
    seen: set[str] = set()

    for p in feat_pts:
        screen_id = p.get("screen_id") or p.get("knowledge_id")
        if not screen_id or screen_id in seen:
            continue
        seen.add(screen_id)

        product_name = p.get("product") or "GoodBooks ERP"
        pid = _slug(product_name)
        mod_code = (p.get("module") or "").lower() or "unassigned"
        mod_id = f"{pid}::{mod_code}"
        cap_name = p.get("capability") or "General"
        fn_id = f"{mod_id}::{_slug(cap_name)}"

        prod = products.setdefault(pid, {"id": pid, "name": product_name, "description": "Knowledge-managed product catalog (PIE / KMS)", "modules": {}})
        minfo = module_info.get(mod_code, {})
        mod = prod["modules"].setdefault(mod_id, {
            "id": mod_id, "name": minfo.get("functionality") or _title(mod_code),
            "description": minfo.get("content") or "", "productId": pid, "functionalities": {},
        })
        cinfo = cap_info.get((mod_code, cap_name), {})
        mod["functionalities"].setdefault(fn_id, {
            "id": fn_id, "name": cap_name, "description": cinfo.get("content") or "",
            "moduleId": mod_id, "features": [],
        })

        verified = p.get("verified_claims") or []
        review = p.get("review_claims") or []
        category = p.get("category") or ""
        description = " ".join(verified[:2]) or p.get("ai_summary") or p.get("content") or ""

        features.append({
            "id": screen_id,
            "name": p.get("feature") or p.get("screen_name") or screen_id,
            "description": description,
            "functionalityId": fn_id,
            "moduleId": mod_code,
            "isCommon": category.strip().lower() == "master data",
            "estimatedRows": _rows_for(category),
            "estimatedRowsBasis": "default_by_category",
            "dependencies": [],
            "dataTemplates": [screen_id],
            "screenId": screen_id,
            "screenName": p.get("screen_name") or "",
            "readiness": p.get("readiness") or "UNKNOWN",
            "category": category,
            "verifiedClaims": verified,
            "reviewClaims": review,
            "aiSummary": p.get("ai_summary") or "",
            "aliasScreenIds": p.get("alias_screen_ids") or [],
            "migrationGaps": p.get("migration_gaps") or 0,
            "source": "kms_usage_feature",
        })

    product_list = []
    for prod in products.values():
        mods = []
        for m in sorted(prod["modules"].values(), key=lambda x: x["name"].lower()):
            m["functionalities"] = sorted(m["functionalities"].values(), key=lambda x: x["name"].lower())
            mods.append(m)
        prod["modules"] = mods
        product_list.append(prod)

    features.sort(key=lambda f: f["name"].lower())
    readiness: dict[str, int] = {}
    for f in features:
        readiness[f["readiness"]] = readiness.get(f["readiness"], 0) + 1

    return {
        "products": product_list,
        "features": features,
        "stats": {
            "modules": sum(len(p["modules"]) for p in product_list),
            "features": len(features),
            "readiness": readiness,
        },
    }


# ----------------------------------------------------------------------
# Snapshot (cached)
# ----------------------------------------------------------------------

_snapshot: dict | None = None
_snapshot_at: float = 0.0
_lock = asyncio.Lock()


async def _build_snapshot() -> dict:
    base, collection, _ = _cfg()
    warnings: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        module_pts = await _scroll(client, _type_filter("UsageModule"), MODULE_INCLUDE)
        cap_pts = await _scroll(client, _type_filter("UsageCapability"), CAPABILITY_INCLUDE)
        feat_pts = await _scroll(client, _type_filter("UsageFeature"), FEATURE_INCLUDE)

        catalog = build_catalog(module_pts, cap_pts, feat_pts)
        by_screen = {f["id"]: f for f in catalog["features"]}

        try:
            try:
                field_pts = await _scroll(client, FIELD_FILTER, FIELD_INCLUDE)
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 400:
                    raise
                # Older Qdrant that rejects the nested filter: read all FieldKnowledge
                # (still payload-only, still only the keys we need) and filter in Python.
                logger.warning("kms_catalog: nested field filter rejected (400); falling back to full FieldKnowledge scan")
                warnings.append("Used full FieldKnowledge scan (slower first load).")
                field_pts = await _scroll(client, _type_filter("FieldKnowledge"), FIELD_INCLUDE)
            rels = build_relationships(field_pts, by_screen)
        except Exception as e:  # features must still work if relationships fail
            logger.exception("kms_catalog: relationship scan failed")
            warnings.append(f"Relationships unavailable: {e}")
            rels = {"entities": [], "relationships": [], "dependencies": {}, "stats": {}}

    # feature dependencies come from relationships
    for f in catalog["features"]:
        f["dependencies"] = rels["dependencies"].get(f["id"], [])

    # fast lookup for the /generate resolver
    screens: dict[str, dict] = {}
    for f in catalog["features"]:
        entry = {"screen_id": f["id"], "module": f["moduleId"], "screen_name": f["screenName"] or f["name"]}
        screens[f["id"].upper()] = entry
        for alias in f["aliasScreenIds"]:
            screens.setdefault(str(alias).upper(), entry)

    now = time.time()
    meta = {
        "source": "kms",
        "collection": collection,
        "qdrant": base,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "warnings": warnings,
        "notes": [
            "Features come from deterministic UsageFeature/UsageModule/UsageCapability points.",
            "Relationships are derived from database foreign keys and related masters in FieldKnowledge. KMS marks inferred links as unconfirmed.",
            "KMS has no min/max children per parent or row counts: those numbers are defaults (defaultsApplied=true).",
        ],
    }
    return {
        "catalog": {**catalog, "meta": {**meta, **catalog["stats"]}},
        "relationships": {
            "entities": rels["entities"],
            "relationships": rels["relationships"],
            "meta": {**meta, **rels["stats"]},
        },
        "screens": screens,
    }


async def get_snapshot(force: bool = False) -> dict:
    global _snapshot, _snapshot_at
    fresh = _snapshot is not None and (time.time() - _snapshot_at) < _cache_seconds()
    if fresh and not force:
        return _snapshot  # type: ignore[return-value]
    async with _lock:
        fresh = _snapshot is not None and (time.time() - _snapshot_at) < _cache_seconds()
        if fresh and not force:
            return _snapshot  # type: ignore[return-value]
        try:
            _snapshot = await _build_snapshot()
            _snapshot_at = time.time()
            logger.info(
                "kms_catalog: snapshot built -- %s features, %s relationships",
                len(_snapshot["catalog"]["features"]), len(_snapshot["relationships"]["relationships"]),
            )
        except Exception:
            if _snapshot is not None:
                logger.exception("kms_catalog: refresh failed, serving stale snapshot")
                return _snapshot
            raise
        return _snapshot


def cache_age_seconds() -> float | None:
    return None if _snapshot is None else round(time.time() - _snapshot_at, 1)


def filter_relationships(snapshot_rels: dict, module: str | None, min_confidence: float) -> dict:
    mod = (module or "").lower().strip()
    rels = [
        r for r in snapshot_rels["relationships"]
        if r["confidence"] >= min_confidence and (not mod or mod in r["modules"])
    ]
    if not mod and min_confidence <= 0:
        return snapshot_rels
    ids = {r["fromEntityId"] for r in rels} | {r["toEntityId"] for r in rels}
    ents = [e for e in snapshot_rels["entities"] if e["id"] in ids]
    return {"entities": ents, "relationships": rels, "meta": snapshot_rels["meta"]}


async def lookup_screen(data_template: str) -> dict | None:
    """Exact screen_id (or alias) match -> {screen_id, module, screen_name}. Used by feature_resolver."""
    key = re.sub(r"\s+", "_", (data_template or "").strip()).upper()
    if not key:
        return None
    snap = await get_snapshot()
    return snap["screens"].get(key)


async def health() -> dict:
    base, collection, headers = _cfg()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            r = await client.get(f"{base}/collections/{collection}", headers=headers)
            r.raise_for_status()
            info = r.json().get("result", {})
        return {"ok": True, "collection": collection, "qdrant": base,
                "points": info.get("points_count"), "cacheAgeSeconds": cache_age_seconds()}
    except Exception as e:
        return {"ok": False, "collection": collection, "qdrant": base, "error": str(e), "cacheAgeSeconds": cache_age_seconds()}