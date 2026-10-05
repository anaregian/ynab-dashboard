"""Pulls each person's YNAB plans into SQLite using delta requests (last_knowledge_of_server).

Cost per sync: 1 request to list plans + 1 per plan, per person. YNAB allows 200/hour per token.
"""
import logging
import os
from datetime import datetime, timezone

import httpx

from . import config, db

API = "https://api.ynab.com/v1"
log = logging.getLogger("sync")

# person name -> last error message (None when the last sync worked)
errors: dict[str, str | None] = {}


async def sync_all() -> None:
    cfg = config.load()
    claimed: dict[str, str] = {}  # plan id -> person; each plan's data belongs to one person
    async with httpx.AsyncClient(base_url=API, timeout=120) as client:
        for person in cfg["people"]:
            name = person["name"]
            token = os.environ.get(person.get("token_env", ""), "")
            if not token:
                errors[name] = f"No token: set {person.get('token_env')} in .env"
                continue
            try:
                await sync_person(client, name, token, person["plans"], claimed)
                errors[name] = None
            except httpx.HTTPStatusError as e:
                errors[name] = f"YNAB API {e.response.status_code}: {e.response.text[:200]}"
                log.warning("sync %s failed: %s", name, errors[name])
            except Exception as e:  # keep syncing the others, show the error in the UI
                errors[name] = f"{type(e).__name__}: {e}"
                log.exception("sync %s failed", name)


async def sync_person(client: httpx.AsyncClient, person: str, token: str, wanted: list[str],
                      claimed: dict[str, str]) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get("/plans", headers=headers)
    resp.raise_for_status()
    plans = resp.json()["data"]["plans"]
    if wanted:
        by_name = {p["name"]: p for p in plans}
        missing = [w for w in wanted if w not in by_name]
        if missing:
            raise ValueError(f"plan(s) not found: {missing}; available: {list(by_name)}")
        chosen = [by_name[w] for w in wanted]
    else:
        # No plan named in config.yml: use the most recently modified one
        chosen = sorted(plans, key=lambda p: p.get("last_modified_on") or "", reverse=True)[:1]
    # Shared plans (YNAB Together) are visible to both tokens; counting one twice would be wrong
    taken = [f"{p['name']!r} (already {claimed[p['id']]}'s)" for p in chosen if p["id"] in claimed]
    if taken:
        raise ValueError(f"plan {', '.join(taken)}: set `plans:` for each person in config.yml")
    claimed.update({p["id"]: person for p in chosen})

    with db.connect() as conn:
        known = {r["id"]: r["server_knowledge"]
                 for r in conn.execute("SELECT id, server_knowledge FROM plans WHERE person = ?", (person,))}

    for plan in chosen:
        params = {}
        if known.get(plan["id"]) is not None:
            params["last_knowledge_of_server"] = known[plan["id"]]
        resp = await client.get(f"/plans/{plan['id']}", headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()["data"]
        store_plan(person, plan, data["plan"], data["server_knowledge"])
        log.info("synced %s / %s (knowledge %s -> %s, %d transactions)", person, plan["name"],
                 params.get("last_knowledge_of_server"), data["server_knowledge"],
                 len(data["plan"].get("transactions", [])))

    # Plans no longer selected in config.yml drop out of the stats
    ids = [p["id"] for p in chosen]
    with db.connect() as conn:
        conn.execute(f"UPDATE plans SET active = 0 WHERE person = ? AND id NOT IN ({','.join('?' * len(ids))})",
                     (person, *ids))


def store_plan(person: str, summary: dict, plan: dict, knowledge: int) -> None:
    """summary is the entry from GET /plans, plan the (possibly delta) GET /plans/{id} body."""
    pid = summary["id"]
    iso = (plan.get("currency_format") or summary.get("currency_format") or {}).get("iso_code")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        for key, (table, cols) in db.ENTITIES.items():
            db.upsert(conn, table, pid, cols, plan.get(key) or [])
        for month in plan.get("months") or []:
            db.upsert(conn, "months", pid, db.MONTH_COLS, [month])
            db.upsert(conn, "month_categories", pid, db.MONTH_CATEGORY_COLS, month.get("categories") or [],
                      extra={"month": month["month"]})
        conn.execute(
            "INSERT OR REPLACE INTO plans (id, person, name, iso_code, server_knowledge, last_sync, active)"
            " VALUES (?, ?, ?, ?, ?, ?, 1)", (pid, person, summary["name"], iso, knowledge, now))
