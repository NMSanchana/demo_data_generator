"""
Relationship-based generation: which screens must exist before others, and which values a child screen may use.

Two levels, both read from what KMS already knows (service/kms_catalog.py):

  1. SCREEN level (build_plan) -- "Sales Order needs Customer Master".
     Uses each screen's confirmed dependencies (declared database foreign keys). It is in-memory only, so the
     /plan endpoint can answer instantly before anything is generated. It decides:
       - the order (parents first),
       - which missing parents are added automatically (and which cannot be),
       - which parents already have data stored in the profile's current version (reused, not regenerated).

  2. FIELD level (parent_values_for / enforce_parent_values) -- "SalesOrder.CustomerId must be a Customer Master value".
     Uses the foreign key stored on each field (fk_table / fk_column). After a parent screen is generated, its values
     become the ONLY allowed values for the child's field.

Nothing here calls the LLM or APM. Every function is safe to call with partial data: missing information
means "no relationship found", never an error.
"""

import logging
import os
import re
from typing import Any

from service import kms_catalog

logger = logging.getLogger(__name__)

try:
    MAX_AUTO_PARENTS = int(os.getenv("MAX_AUTO_PARENTS", "25"))
except ValueError:
    MAX_AUTO_PARENTS = 25

MAX_PARENT_VALUES = 100  # values handed to a child field (keeps the LLM prompt small)


def _up(screen_id: Any) -> str:
    return str(screen_id or "").strip().upper()


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


# ----------------------------------------------------------------------
# Snapshot lookups
# ----------------------------------------------------------------------

async def _features() -> dict[str, dict]:
    snap = await kms_catalog.get_snapshot()
    return {_up(f["id"]): f for f in snap["catalog"]["features"]}


async def _relationship_columns() -> dict[tuple[str, str], list[str]]:
    """(child screen, parent screen) -> the child's columns that point at the parent (for readable messages)."""
    snap = await kms_catalog.get_snapshot()
    rels = snap["relationships"]
    owner_by_ent = {e["id"]: _up(e.get("featureId")) for e in rels["entities"]}
    out: dict[tuple[str, str], list[str]] = {}
    for r in rels["relationships"]:
        if r.get("status") != "confirmed":
            continue
        parent = owner_by_ent.get(r["fromEntityId"])
        if not parent:
            continue
        for s in r.get("screens", []):
            out[(_up(s), parent)] = list(r.get("childColumns") or [])
    return out


async def table_owners() -> dict[str, str]:
    """TABLE NAME (upper) -> the screen (upper) that owns that table."""
    snap = await kms_catalog.get_snapshot()
    return {
        str(e["name"]).upper(): _up(e["featureId"])
        for e in snap["relationships"]["entities"] if e.get("featureId")
    }


def screen_name(feats: dict[str, dict], sid: str) -> str:
    f = feats.get(sid)
    return (f.get("screenName") or f.get("name")) if f else sid


# ----------------------------------------------------------------------
# Screen-level plan
# ----------------------------------------------------------------------

async def build_plan(
    screen_ids: list[str],
    *,
    auto_add: bool = True,
    stored: dict[str, int] | None = None,
) -> dict:
    """
    screen_ids : the screens selected for this run (KMS screen ids).
    auto_add   : add missing parent screens to the run (default) or leave them out.
    stored     : {SCREEN_ID(upper): version_no} for screens that already have data in the profile's current version.

    Returns {
      "order":     [SCREEN_ID, ...]  every screen of the run (selected + auto-added), parents first,
      "auto_add":  [SCREEN_ID, ...]  parents added to the run,
      "links":     [{child, parent, columns, status}]   status = in_run | auto_added | reused | not_generated | unavailable | limit
      "names":     {SCREEN_ID: display name},
    }
    """
    stored = {_up(k): v for k, v in (stored or {}).items()}
    feats = await _features()
    try:
        with_data = {k for k, n in (await kms_catalog.screens_with_fields()).items() if n > 0}
    except Exception:  # noqa: BLE001 - the field index is only a helper; without it assume parents have data
        logger.warning("relationship_planner: field index unavailable, assuming parent screens have field data")
        with_data = set(feats)
    cols = await _relationship_columns()

    in_run: list[str] = []
    for sid in screen_ids:
        u = _up(sid)
        if u and u not in in_run:
            in_run.append(u)
    selected = set(in_run)

    added: list[str] = []
    links: list[dict] = []
    queue = list(in_run)
    seen_links: set[tuple[str, str]] = set()

    while queue:
        child = queue.pop(0)
        child_feat = feats.get(child)
        if not child_feat:
            continue
        for parent_raw in child_feat.get("dependencies") or []:
            parent = _up(parent_raw)
            if not parent or parent == child or (child, parent) in seen_links:
                continue
            seen_links.add((child, parent))
            pf = feats.get(parent)
            if parent in selected:
                status = "in_run"
            elif parent in added:
                status = "auto_added"
            elif parent in stored:
                status = "reused"
            elif pf is None or pf.get("generatable") is False or parent not in with_data:
                status = "unavailable"
            elif not auto_add:
                status = "not_generated"
            elif len(added) >= MAX_AUTO_PARENTS:
                status = "limit"
            else:
                added.append(parent)
                queue.append(parent)
                status = "auto_added"
            links.append({"child": child, "parent": parent, "columns": cols.get((child, parent), []), "status": status})

    # ---- order: parents first (Kahn's algorithm, stable; a cycle just keeps its original order) ----
    members = in_run + [a for a in added if a not in selected]
    member_set = set(members)
    needs: dict[str, set[str]] = {m: set() for m in members}
    for l in links:
        if l["status"] in ("in_run", "auto_added") and l["child"] in member_set and l["parent"] in member_set:
            needs[l["child"]].add(l["parent"])
    order: list[str] = []
    done: set[str] = set()
    remaining = list(members)
    while remaining:
        ready = [m for m in remaining if needs[m] <= done]
        if not ready:  # cycle: keep what is left in its original order
            order.extend(remaining)
            break
        for m in ready:
            order.append(m)
            done.add(m)
        remaining = [m for m in remaining if m not in done]

    names = {sid: screen_name(feats, sid) for sid in set(order) | {l["parent"] for l in links} | {l["child"] for l in links}}
    return {"order": order, "auto_add": added, "links": links, "names": names}


def _list_names(names: list[str], limit: int = 3) -> str:
    names = sorted(set(names))
    shown = ", ".join(names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def describe_plan(plan: dict, stored: dict[str, int] | None = None, stored_rows: dict[str, int] | None = None) -> list[dict]:
    """Plain-language notices for the page, one per (parent, status) so a big run stays readable.
    stored_rows ({SCREEN_ID: row count}) lets the "reused" notice say how many rows already exist."""
    stored = {_up(k): v for k, v in (stored or {}).items()}
    stored_rows = {_up(k): v for k, v in (stored_rows or {}).items()}
    names = plan["names"]
    groups: dict[tuple[str, str], list[str]] = {}
    for l in plan["links"]:
        groups.setdefault((l["parent"], l["status"]), []).append(names.get(l["child"], l["child"]))

    out: list[dict] = []
    for (parent, status), children in sorted(groups.items(), key=lambda kv: (kv[0][1], names.get(kv[0][0], ""))):
        p = names.get(parent, parent)
        who = _list_names(children)
        n = len(set(children))
        if status == "auto_added":
            msg = f"{who} {'needs' if n == 1 else 'need'} {p}. {p} will be generated first and used there."
            level = "info"
        elif status == "in_run":
            msg = f"{who} will use {p}, which is already in this run (generated first)."
            level = "info"
        elif status == "reused":
            n_rows = stored_rows.get(parent)
            rows_text = f"{n_rows} row{'' if n_rows == 1 else 's'}" if n_rows else "data"
            msg = f"{p}: {rows_text} already in your database (v{stored.get(parent, '?')}), will be reused for {who}. It is not generated again."
            level = "info"
        elif status == "not_generated":
            msg = f"{who} {'needs' if n == 1 else 'need'} {p}, but adding it automatically is switched off. Those fields get invented values."
            level = "warning"
        elif status == "unavailable":
            msg = f"{who} {'needs' if n == 1 else 'need'} {p}, which cannot be generated (it is a report screen or KMS has no field data for it). Those fields get invented values."
            level = "warning"
        else:  # limit
            msg = f"{who} {'needs' if n == 1 else 'need'} {p}, but the limit of {MAX_AUTO_PARENTS} automatically added screens was reached. Tick it yourself to include it."
            level = "warning"
        out.append({"level": level, "kind": status, "screen": parent, "parent": p, "message": msg})
    return out


def child_notes(plan: dict, screen_id: str, stored: dict[str, int] | None = None) -> list[str]:
    """Notices about ONE child screen's parents, for that screen's result."""
    stored = {_up(k): v for k, v in (stored or {}).items()}
    names = plan["names"]
    sid = _up(screen_id)
    notes: list[str] = []
    for l in plan["links"]:
        if l["child"] != sid:
            continue
        p = names.get(l["parent"], l["parent"])
        if l["status"] in ("in_run", "auto_added"):
            notes.append(f"Uses {p} generated earlier in this run.")
        elif l["status"] == "reused":
            notes.append(f"Reuses {p} data already stored in v{stored.get(l['parent'], '?')}.")
        elif l["status"] == "not_generated":
            notes.append(f"{p} was not generated (automatic adding is off); its fields have invented values.")
        elif l["status"] == "unavailable":
            notes.append(f"{p} cannot be generated (report screen or no KMS field data); its fields have invented values.")
        else:
            notes.append(f"{p} was not added (limit of {MAX_AUTO_PARENTS} reached); its fields have invented values.")
    return notes


# ----------------------------------------------------------------------
# Field-level values
# ----------------------------------------------------------------------

def _unique_values(rows: list[dict], field_name: str) -> list:
    seen: set[str] = set()
    out: list = []
    for r in rows:
        v = r.get(field_name)
        if v is None or str(v).strip() == "":
            continue
        k = str(v)
        if k not in seen:
            seen.add(k)
            out.append(v)
        if len(out) >= MAX_PARENT_VALUES:
            break
    return out


def _pick_parent_field(parent_fields: list[dict], fk_column: str) -> str | None:
    """The parent field a child's foreign key points at: the one stored in that column, else the
    screen's code/id field, else its name field."""
    want = _norm(fk_column)
    if want:
        for f in parent_fields:
            if _norm(f.get("db_column")) == want:
                return f["field_name"]
    for kind in ("identifier", "name"):
        for f in parent_fields:
            if f.get("kind") == kind:
                return f["field_name"]
    return None


def parent_values_for(
    fields: list[dict],
    own_screen_id: str,
    sources: dict[str, dict],
    owners: dict[str, str],
) -> tuple[dict[str, list], list[str]]:
    """
    fields   : the child screen's field records (they carry fk_table / fk_column from KMS).
    sources  : {SCREEN_ID(upper): {"name", "fields", "rows"}} -- screens whose rows are available
               (generated earlier in this run, or reused from the stored version).
    owners   : table -> owning screen (table_owners()).
    Returns ({child field name: allowed values}, readable notes).
    """
    own = _up(own_screen_id)
    values: dict[str, list] = {}
    notes: list[str] = []
    for f in fields:
        table = str(f.get("fk_table") or "").upper()
        if not table:
            continue
        owner = owners.get(table)
        if not owner or owner == own:
            continue
        src = sources.get(owner)
        if not src:
            continue
        via = _pick_parent_field(src.get("fields") or [], f.get("fk_column") or "")
        if not via:
            continue
        vals = _unique_values(src.get("rows") or [], via)
        if not vals:
            continue
        values[f["field_name"]] = vals
        notes.append(f"{f['field_name']} takes its values from {src.get('name', owner)} → {via} ({len(vals)} value(s)).")
    return values, notes


def enforce_parent_values(rows: list[dict], parent_values: dict[str, list]) -> int:
    """The model is told to use only the parent's values; this makes sure of it. A value that is not
    one of them is replaced by the next allowed value (round-robin). Returns how many cells were replaced."""
    fixed = 0
    for field, allowed in parent_values.items():
        if not allowed:
            continue
        allowed_str = {str(a) for a in allowed}
        for i, row in enumerate(rows):
            if str(row.get(field)) not in allowed_str:
                row[field] = allowed[i % len(allowed)]
                fixed += 1
    return fixed