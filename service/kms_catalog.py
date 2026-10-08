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
        host, port = os.getenv("QDRANT_HOST"), os.getenv("QDRANT_PORT", "6333")
        if not host:
            raise RuntimeError("QDRANT_URL must be set in .env (see .env.example).")
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


def _norm_name(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


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

def _resolve_hint(name: str, known: set[str]) -> str | None:
    """A 'related master' hint is a loose name (e.g. ACCOUNT). Accept it only if it
    matches a real table KMS knows about: exact, or with the usual 'M' master prefix."""
    n = _norm_table(name)
    if not n:
        return None
    for cand in (n, "M" + n):
        if cand in known:
            return cand
    return None


def build_relationships(field_points: list[dict], features_by_screen: dict[str, dict]) -> dict:
    """Derive entities + relationships from FieldKnowledge payloads.

    One relationship per (parent table, child table) pair. Several columns that point at the
    same parent are merged into that single relationship (`childColumns`).

    status:
      confirmed -> backed by a declared database foreign key (required = child column is NOT NULL)
      suggested -> only a 'related master' hint or a grid/child-table hint. Never marked required.
                   Hints that do not match a real table are dropped (counted in unresolvedHints).
    """
    pk_screens: dict[str, set[tuple[str, str]]] = {}
    real_tables: set[str] = set()
    fk_rows: list[dict] = []
    hint_rows: list[dict] = []
    child_table_pairs: dict[tuple[str, str], dict] = {}

    for p in field_points:
        screen_id = p.get("screen_id") or ""
        module = (p.get("module") or "").lower()
        field = p.get("field") or {}
        db = field.get("database") or {}
        mand = field.get("mandatory") or {}
        table = _norm_table(db.get("table"))
        if not table:
            continue
        real_tables.add(table)
        if db.get("primaryKey") is True and screen_id:
            pk_screens.setdefault(table, set()).add((screen_id, module))

        point_conf = p.get("confidence")
        point_conf = float(point_conf) if isinstance(point_conf, (int, float)) else 1.0
        col = str(db.get("column") or p.get("field_name") or "")
        required = bool(db.get("nullable") is False or mand.get("required"))
        evidence = [
            {"source": e.get("source"), "line": e.get("line"), "fact": e.get("fact")}
            for e in (field.get("evidence") or [])[:2] if isinstance(e, dict)
        ]
        base = {"child": table, "childColumn": col, "required": required, "screen": screen_id,
                "module": module, "evidence": evidence}

        fk = db.get("foreignKey")
        if isinstance(fk, dict) and fk.get("table"):
            parent = _norm_table(fk["table"])
            if parent:
                real_tables.add(parent)
                fk_rows.append({**base, "parent": parent, "parentColumn": str(fk.get("column") or ""),
                                "confidence": min(1.0, point_conf), "oneToOne": db.get("primaryKey") is True})
        else:
            for rm in field.get("relatedMasters") or []:
                hint_rows.append({**base, "hint": rm})

        ct = db.get("childTable")
        if isinstance(ct, dict):
            ct = ct.get("table") or ct.get("name")
        ct = _norm_table(ct) if isinstance(ct, str) else ""
        if ct and ct != table:
            d = child_table_pairs.setdefault((table, ct), {"screens": set(), "modules": set()})
            if screen_id:
                d["screens"].add(screen_id)
            if module:
                d["modules"].add(module)

    # ---- merge into one record per (parent, child) pair ----
    pairs: dict[tuple[str, str], dict] = {}
    self_refs = 0

    def pair_for(parent: str, child: str, source: str, conf: float) -> dict:
        return pairs.setdefault((parent, child), {
            "parent": parent, "child": child, "source": source, "confidence": conf,
            "required": False, "oneToOne": False, "columns": {},
            "screens": set(), "modules": set(), "evidence": [],
        })

    def absorb(rec: dict, row: dict):
        if row["screen"]:
            rec["screens"].add(row["screen"])
        if row["module"]:
            rec["modules"].add(row["module"])
        for ev in row["evidence"]:
            if ev not in rec["evidence"] and len(rec["evidence"]) < 3:
                rec["evidence"].append(ev)

    for r in fk_rows:
        if r["parent"] == r["child"]:
            self_refs += 1
            continue
        rec = pair_for(r["parent"], r["child"], "kms_foreign_key", r["confidence"])
        rec["source"] = "kms_foreign_key"
        rec["confidence"] = max(rec["confidence"], r["confidence"])
        rec["required"] = rec["required"] or r["required"]
        rec["oneToOne"] = rec["oneToOne"] or r["oneToOne"]
        rec["columns"][r["childColumn"]] = r["parentColumn"]
        absorb(rec, r)

    unresolved: set[str] = set()
    for r in hint_rows:
        parent = _resolve_hint(r["hint"], real_tables)
        if not parent:
            unresolved.add(_norm_table(r["hint"]))
            continue
        if parent == r["child"]:
            continue
        rec = pairs.get((parent, r["child"]))
        if rec is None:
            rec = pair_for(parent, r["child"], "kms_related_master", 0.6)
        if rec["source"] == "kms_related_master":
            rec["columns"].setdefault(r["childColumn"], "")
        absorb(rec, r)

    for (parent, child), d in child_table_pairs.items():
        if parent == child or (parent, child) in pairs or parent not in real_tables and child not in real_tables:
            continue
        rec = pair_for(parent, child, "kms_child_table", 0.5)
        rec["screens"] |= d["screens"]
        rec["modules"] |= d["modules"]

    # ---- owner screen per table ----
    def owner_of(table: str) -> tuple[str | None, str]:
        options = sorted(pk_screens.get(table, set()))
        if not options:
            return None, ""
        options.sort(key=lambda o: (-_owner_score(table, o[0], o[1]), len(o[0]), o[0]))
        return options[0]

    all_tables = set(real_tables)
    for (parent, child) in pairs:
        all_tables.add(parent)
        all_tables.add(child)

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

    # ---- relationships ----
    relationships: list[dict] = []
    for (parent, child), c in sorted(pairs.items()):
        confirmed = c["source"] == "kms_foreign_key"
        required = bool(confirmed and c["required"])
        one = bool(confirmed and c["oneToOne"])
        cols = sorted(k for k in c["columns"] if k)
        if confirmed and cols:
            shown = ", ".join(f"{k}\u2192{c['columns'][k]}" if c["columns"][k] else k for k in cols[:3])
            if len(cols) > 3:
                shown += f" +{len(cols) - 3} more"
            note = f"{child}.{shown} (parent: {parent})"
        elif c["source"] == "kms_related_master":
            note = f"{child} refers to master {parent} (hint from KMS, not a declared foreign key)"
        else:
            note = f"{child} is a detail/grid table of {parent}"
        relationships.append({
            "id": f"rel-{child.lower()}-{parent.lower()}",
            "fromEntityId": entities[parent]["id"],
            "toEntityId": entities[child]["id"],
            "kind": "one-to-one" if one else "one-to-many",
            "minPerParent": 1 if one else (DEFAULT_MIN_PER_PARENT_REQUIRED if required else DEFAULT_MIN_PER_PARENT_OPTIONAL),
            "maxPerParent": 1 if one else DEFAULT_MAX_PER_PARENT,
            "required": required,
            "note": note,
            "source": c["source"],
            "status": "confirmed" if confirmed else "suggested",
            "confidence": c["confidence"],
            "childColumns": cols,
            "defaultsApplied": True,
            "screens": sorted(c["screens"])[:12],
            "modules": sorted(c["modules"]),
            "evidence": c["evidence"],
        })

    linked = {r["fromEntityId"] for r in relationships} | {r["toEntityId"] for r in relationships}
    entity_list = [e for e in entities.values() if e["id"] in linked or e["featureId"]]

    # screen -> screens it depends on. Only confirmed links count as real dependencies.
    deps: dict[str, set[str]] = {}
    ent_by_id = {e["id"]: e for e in entities.values()}
    for r in relationships:
        if r["status"] != "confirmed":
            continue
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
            "confirmed": sum(1 for r in relationships if r["status"] == "confirmed"),
            "suggested": sum(1 for r in relationships if r["status"] == "suggested"),
            "selfReferencesSkipped": self_refs,
            "unresolvedHints": len(unresolved),
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
    by_name: dict[str, list[dict]] = {}
    for f in catalog["features"]:
        entry = {"screen_id": f["id"], "module": f["moduleId"], "screen_name": f["screenName"] or f["name"]}
        screens[f["id"].upper()] = entry
        for alias in f["aliasScreenIds"]:
            screens.setdefault(str(alias).upper(), entry)
        for label in {f["screenName"], f["name"]}:
            k = _norm_name(label)
            if k and entry not in by_name.setdefault(k, []):
                by_name[k].append(entry)
    # a display name is only usable when it points at exactly one screen
    screens_by_name = {k: v[0] for k, v in by_name.items() if len(v) == 1}

    now = time.time()
    meta = {
        "source": "kms",
        "collection": collection,
        "qdrant": base,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "warnings": warnings,
        "notes": [
            "Features come from deterministic UsageFeature/UsageModule/UsageCapability points.",
            "Relationships: one per table pair. confirmed = declared database foreign key; suggested = related-master / grid hint (never required).",
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
        "screensByName": screens_by_name,
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
    """Screen id (or alias) -> {screen_id, module, screen_name}. Falls back to an
    unambiguous display-name match. No guessing beyond that."""
    raw = (data_template or "").strip()
    if not raw:
        return None
    snap = await get_snapshot()
    hit = snap["screens"].get(re.sub(r"\s+", "_", raw).upper())
    if hit:
        return hit
    return snap["screensByName"].get(_norm_name(raw))


_fields_cache: dict[str, tuple[float, list[str]]] = {}


async def get_screen_field_names(screen_id: str) -> list[str]:
    """Field names KMS knows for one screen (FieldKnowledge points), in stored order.
    Empty list when KMS has none -- the caller then falls back to parsing the source."""
    key = screen_id.upper()
    cached = _fields_cache.get(key)
    if cached and (time.time() - cached[0]) < _cache_seconds():
        return cached[1]
    flt = {"must": [
        {"key": "knowledge_type", "match": {"value": "FieldKnowledge"}},
        {"key": "screen_id", "match": {"value": screen_id}},
    ]}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        pts = await _scroll(client, flt, ["screen_id", "field_name"])
    names: list[str] = []
    for p in pts:
        n = str(p.get("field_name") or "").strip()
        if n and n not in names:
            names.append(n)
    _fields_cache[key] = (time.time(), names)
    return names


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