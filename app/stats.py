"""Aggregates the mirrored YNAB data into everything the dashboard shows.

Conventions (same as YNAB's own reports):
- Only on-budget accounts count towards income/spending.
- Income = lines categorised to 'Inflow: Ready to Assign'. Spending = -(all other categorised lines),
  so refunds reduce spending. Transfers between on-budget accounts have no category and are ignored.
- Lines in `savings_groups` / `savings_categories` (config.yml) count as invested, not spending.
- Net worth uses every account, including tracking accounts.
- In the combined view, lines whose payee/category is listed under `combined:` in config.yml are
  dropped (money moving between the two of you would otherwise be counted twice).
"""
from collections import defaultdict
from datetime import date

from . import config, db, mappings

COMBINED = "combined"
TOP_CATEGORIES = 8


def month_range(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= end:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def shift_month(month: str, delta: int) -> str:
    y, m = map(int, month.split("-"))
    total = y * 12 + (m - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def money(milli: int | float) -> float:
    return round(milli / 1000, 2)


def active_plans(conn, people: list[str]) -> list:
    marks = ",".join("?" * len(people))
    return conn.execute(f"SELECT * FROM plans WHERE active = 1 AND person IN ({marks})", people).fetchall()


def category_index(conn, plans: list, cfg: dict) -> dict:
    """(plan_id, category_id) -> {key, name, gkey, group, kind, hidden}.

    Categories merge on the normalized name, after applying mappings from the Categories page.
    Two categories of the same plan only merge when mapped together on purpose ('🐱 Food' and
    '🐶 Food' normalize alike but are different categories).

    A merged category takes its name and group from its first visible occurrence (people in
    config.yml order), so an archived/hidden category doesn't drag others into its group.
    """
    mapped = mappings.load()
    order = {p["name"]: i for i, p in enumerate(cfg["people"])}
    display: dict[str, str] = {}
    group_of: dict[str, str] = {}  # category key -> group key
    group_display: dict[str, str] = {}
    index = {}
    claimed: dict[tuple[str, str], str] = {}  # (plan_id, key) -> category id
    rows = []
    for plan in plans:
        rows += [(plan, r) for r in conn.execute(
            "SELECT c.id, c.name, c.internal, c.hidden OR COALESCE(g.hidden, 0) AS hidden,"
            " g.name AS group_name FROM categories c"
            " LEFT JOIN category_groups g ON g.plan_id = c.plan_id AND g.id = c.category_group_id"
            " WHERE c.plan_id = ?", (plan["id"],))]
    rows.sort(key=lambda pr: (pr[1]["hidden"], order.get(pr[0]["person"], 99)))
    for plan, r in rows:
        norm = config.normalize(r["name"])
        if r["internal"]:
            if norm.startswith("inflow") or "ready to assign" in norm or "to be budgeted" in norm:
                kind = "income"
            elif norm == "uncategorized":
                kind = "spend"
            else:
                kind = "skip"
        elif config.normalize(r["group_name"]) == "credit card payments":
            kind = "skip"  # YNAB-managed, no real spending of their own
        else:
            kind = "spend"
        explicit = mapped.get(plan["id"], {}).get(r["id"])
        name = explicit or r["name"]
        key = config.normalize(name)
        if not explicit and claimed.setdefault((plan["id"], key), r["id"]) != r["id"]:
            key = f"{key}|{r['name']}"
        group = cfg["group_aliases"].get(config.normalize(r["group_name"]), r["group_name"]) or "Other"
        gkey = config.normalize(group)
        if kind == "spend" and (gkey in cfg["savings_groups"] or key in cfg["savings_categories"]
                                or config.normalize(r["group_name"]) in cfg["savings_groups"]):
            kind = "save"
        display.setdefault(key, name)
        group_of.setdefault(key, gkey)
        group_display.setdefault(gkey, group)
        index[(plan["id"], r["id"])] = {"key": key, "kind": kind, "hidden": bool(r["hidden"])}
    for info in index.values():
        info["name"] = display[info["key"]]
        info["gkey"] = group_of[info["key"]]
        info["group"] = group_display[info["gkey"]]
    return index


UNCATEGORIZED = {"key": "uncategorized", "name": "Uncategorized", "gkey": "other", "group": "Other",
                 "kind": "spend"}


def summary(view: str, start: str, end: str) -> dict:
    cfg = config.load()
    everyone = [p["name"] for p in cfg["people"]]
    people = everyone if view == COMBINED else [view]
    months = month_range(start, end)
    combined = view == COMBINED
    exclude_payees = cfg["combined"]["exclude_payees"] if combined else set()
    exclude_categories = cfg["combined"]["exclude_categories"] if combined else set()

    with db.connect() as conn:
        plans = active_plans(conn, people)
        person_of = {p["id"]: p["person"] for p in plans}
        plan_ids = list(person_of)
        marks = ",".join("?" * len(plan_ids))
        cats = category_index(conn, plans, cfg)

        lines = conn.execute(
            f"SELECT l.plan_id, l.date, l.amount, l.category_id, l.transfer_account_id, l.memo,"
            f"       a.on_budget, py.name AS payee"
            f" FROM lines l"
            f" JOIN accounts a ON a.plan_id = l.plan_id AND a.id = l.account_id"
            f" LEFT JOIN payees py ON py.plan_id = l.plan_id AND py.id = l.payee_id"
            f" WHERE l.plan_id IN ({marks}) AND substr(l.date, 1, 7) BETWEEN ? AND ?",
            (*plan_ids, start, end)).fetchall()

        # --- income / spending -------------------------------------------------------------
        income = defaultdict(int)
        spend = defaultdict(int)
        invested = defaultdict(int)
        income_by_person = {p: defaultdict(int) for p in people}
        spend_by_person = {p: defaultdict(int) for p in people}
        invested_by_person = {p: defaultdict(int) for p in people}
        categories: dict[str, dict] = {}
        groups: dict[str, dict] = {}
        payees: dict[str, dict] = {}
        outflows = []
        excluded = {"count": 0, "amount": 0}

        for ln in lines:
            if not ln["on_budget"]:
                continue
            if ln["category_id"] is None:
                if ln["transfer_account_id"]:
                    continue
                cat = UNCATEGORIZED
            else:
                cat = cats.get((ln["plan_id"], ln["category_id"]), UNCATEGORIZED)
            if cat["kind"] == "skip":
                continue
            if config.normalize(ln["payee"]) in exclude_payees or cat["key"] in exclude_categories:
                excluded["count"] += 1
                excluded["amount"] += abs(ln["amount"])
                continue
            person = person_of[ln["plan_id"]]
            month = ln["date"][:7]
            amount = ln["amount"]
            if cat["kind"] == "income":
                income[month] += amount
                income_by_person[person][month] += amount
                continue
            if cat["kind"] == "save":
                invested[month] -= amount
                invested_by_person[person][month] -= amount
                continue

            spend[month] -= amount
            spend_by_person[person][month] -= amount
            c = categories.setdefault(cat["key"], {
                "name": cat["name"], "group": cat["group"], "total": 0,
                "by_person": defaultdict(int), "monthly": defaultdict(int)})
            c["total"] -= amount
            c["by_person"][person] -= amount
            c["monthly"][month] -= amount
            g = groups.setdefault(cat["gkey"], {"name": cat["group"], "total": 0})
            g["total"] -= amount
            payee_name = ln["payee"] or "(no payee)"
            pk = config.normalize(payee_name)
            p = payees.setdefault(pk, {"name": payee_name, "total": 0, "count": 0})
            p["total"] -= amount
            if amount < 0:
                p["count"] += 1
                outflows.append({"date": ln["date"], "payee": payee_name, "category": cat["name"],
                                 "person": person, "memo": ln["memo"], "amount": money(-amount)})

        total_income = sum(income.values())
        total_spend = sum(spend.values())
        total_invested = sum(invested.values())
        today = date.today().strftime("%Y-%m")
        full_months = [m for m in months if m < today] or months

        cat_list = sorted(categories.values(), key=lambda c: c["total"], reverse=True)
        top = [c for c in cat_list if c["total"] > 0][:TOP_CATEGORIES]
        top_names = {c["name"] for c in top}
        trend = [{"name": c["name"], "data": [money(c["monthly"][m]) for m in months]} for c in top]
        other = [sum(c["monthly"][m] for c in cat_list if c["name"] not in top_names) for m in months]
        if any(other):
            trend.append({"name": "Other", "data": [money(v) for v in other]})

        result = {
            "view": view,
            "people": people,
            "months": months,
            "kpi": {
                "income": money(total_income),
                "spending": money(total_spend),
                "invested": money(total_invested),
                "saved": money(total_income - total_spend),
                "savings_rate": round((total_income - total_spend) / total_income * 100, 1)
                if total_income > 0 else None,
                "avg_monthly_spending": money(sum(spend[m] for m in full_months) / len(full_months)),
                "avg_monthly_income": money(sum(income[m] for m in full_months) / len(full_months)),
            },
            "cashflow": {
                "income": [money(income[m]) for m in months],
                "spending": [money(spend[m]) for m in months],
                "net": [money(income[m] - spend[m]) for m in months],
                "invested": [money(invested[m]) for m in months],
                "spending_by_person": {p: [money(spend_by_person[p][m]) for m in months] for p in people},
                "income_by_person": {p: [money(income_by_person[p][m]) for m in months] for p in people},
                "invested_by_person": {p: [money(invested_by_person[p][m]) for m in months] for p in people},
            },
            "categories": [{
                "name": c["name"], "group": c["group"], "total": money(c["total"]),
                "monthly_avg": money(c["total"] / len(months)),
                "by_person": {p: money(c["by_person"][p]) for p in people},
            } for c in cat_list if c["total"] != 0],
            "category_trend": trend,
            "groups": sorted(({"name": g["name"], "total": money(g["total"])} for g in groups.values()
                              if g["total"] > 0), key=lambda g: g["total"], reverse=True),
            "payees": sorted(({"name": p["name"], "total": money(p["total"]), "count": p["count"]}
                              for p in payees.values() if p["total"] > 0),
                             key=lambda p: p["total"], reverse=True)[:15],
            "largest": sorted(outflows, key=lambda o: o["amount"], reverse=True)[:10],
            "excluded": {"count": excluded["count"], "amount": money(excluded["amount"])},
        }
        result["net_worth"] = net_worth(conn, plans, people, start, end)
        result["accounts"] = accounts(conn, plans)
        result["budget"] = budget_month(conn, plans, cats, min(end, today))
        result["plan_months"] = plan_month_stats(conn, plans, min(end, today))
    return result


def net_worth(conn, plans: list, people: list[str], start: str, end: str) -> dict:
    """Month-end balances, from summing every transaction per account. Shows at least 12 months."""
    person_of = {p["id"]: p["person"] for p in plans}
    marks = ",".join("?" * len(person_of))
    rows = conn.execute(
        f"SELECT t.plan_id, t.account_id, substr(t.date, 1, 7) AS month, SUM(t.amount) AS amount"
        f" FROM transactions t JOIN accounts a ON a.plan_id = t.plan_id AND a.id = t.account_id"
        f" WHERE t.deleted = 0 AND a.deleted = 0 AND t.plan_id IN ({marks}) AND month <= ?"
        f" GROUP BY 1, 2, 3 ORDER BY month", (*person_of, end)).fetchall()
    shown = month_range(min(start, shift_month(end, -11)), end)
    if not rows:
        return {"months": shown, "by_person": {p: [0] * len(shown) for p in people},
                "assets": [0] * len(shown), "debts": [0] * len(shown)}

    changes = defaultdict(lambda: defaultdict(int))  # month -> (plan, account) -> amount
    for r in rows:
        changes[r["month"]][(r["plan_id"], r["account_id"])] += r["amount"]
    balance = defaultdict(int)
    out = {"months": [], "by_person": {p: [] for p in people}, "assets": [], "debts": []}
    for month in month_range(min(rows[0]["month"], shown[0]), end):
        for acct, amount in changes[month].items():
            balance[acct] += amount
        if month < shown[0]:
            continue
        out["months"].append(month)
        for p in people:
            out["by_person"][p].append(money(sum(v for (pid, _), v in balance.items() if person_of[pid] == p)))
        out["assets"].append(money(sum(v for v in balance.values() if v > 0)))
        out["debts"].append(money(sum(v for v in balance.values() if v < 0)))
    return out


def accounts(conn, plans: list) -> list[dict]:
    person_of = {p["id"]: p["person"] for p in plans}
    marks = ",".join("?" * len(person_of))
    rows = conn.execute(
        f"SELECT plan_id, name, type, on_budget, balance FROM accounts"
        f" WHERE deleted = 0 AND closed = 0 AND plan_id IN ({marks}) ORDER BY balance DESC",
        list(person_of)).fetchall()
    return [{"person": person_of[r["plan_id"]], "name": r["name"], "type": r["type"],
             "on_budget": bool(r["on_budget"]), "balance": money(r["balance"])} for r in rows]


def budget_month(conn, plans: list, cats: dict, month: str) -> dict:
    """Assigned / spent / available per merged category for one month, from YNAB's month data."""
    marks = ",".join("?" * len(plans))
    rows = conn.execute(
        f"SELECT mc.plan_id, mc.id, mc.budgeted, mc.activity, mc.balance FROM month_categories mc"
        f" JOIN categories c ON c.plan_id = mc.plan_id AND c.id = mc.id"
        f" WHERE mc.deleted = 0 AND c.deleted = 0 AND mc.month = ? AND mc.plan_id IN ({marks})",
        (f"{month}-01", *(p["id"] for p in plans))).fetchall()
    merged: dict[str, dict] = {}
    for r in rows:
        cat = cats.get((r["plan_id"], r["id"]))
        if not cat or cat["kind"] not in ("spend", "save") or cat["key"] == "uncategorized":
            continue
        m = merged.setdefault(cat["key"], {"name": cat["name"], "group": cat["group"],
                                           "assigned": 0, "spent": 0, "available": 0})
        m["assigned"] += r["budgeted"]
        m["spent"] -= r["activity"]
        m["available"] += r["balance"]
    items = [{k: money(v) if isinstance(v, int) else v for k, v in m.items()}
             for m in merged.values() if m["assigned"] or m["spent"] or m["available"]]
    items.sort(key=lambda m: max(m["assigned"], m["spent"]), reverse=True)
    return {"month": month, "categories": items}


def plan_month_stats(conn, plans: list, month: str) -> list[dict]:
    """Per plan: Ready to Assign and Age of Money for the month."""
    out = []
    for plan in plans:
        r = conn.execute("SELECT to_be_budgeted, age_of_money FROM months"
                         " WHERE plan_id = ? AND month = ? AND deleted = 0",
                         (plan["id"], f"{month}-01")).fetchone()
        if r:
            out.append({"person": plan["person"], "plan": plan["name"],
                        "ready_to_assign": money(r["to_be_budgeted"]), "age_of_money": r["age_of_money"]})
    return out


def category_list() -> dict:
    """Every real category of every active plan, for the Categories page."""
    cfg = config.load()
    people = [p["name"] for p in cfg["people"]]
    since = date.today().replace(day=1)
    since = since.replace(year=since.year - 1).isoformat()
    mapped = mappings.load()
    with db.connect() as conn:
        plans = active_plans(conn, people)
        cats = category_index(conn, plans, cfg)
        person_of = {p["id"]: p["person"] for p in plans}
        rows = conn.execute(
            f"SELECT c.plan_id, c.id, c.name, g.name AS group_name FROM categories c"
            f" LEFT JOIN category_groups g ON g.plan_id = c.plan_id AND g.id = c.category_group_id"
            f" WHERE c.deleted = 0 AND c.plan_id IN ({','.join('?' * len(plans))})",
            [p["id"] for p in plans]).fetchall()
        spend = {(r["plan_id"], r["category_id"]): r["total"] for r in conn.execute(
            f"SELECT plan_id, category_id, -SUM(amount) AS total FROM lines"
            f" WHERE date >= ? AND plan_id IN ({','.join('?' * len(plans))}) GROUP BY 1, 2",
            [since, *(p["id"] for p in plans)])}
    order = {p: i for i, p in enumerate(people)}
    out = []
    for r in rows:
        info = cats.get((r["plan_id"], r["id"]))
        if not info or info["kind"] not in ("spend", "save"):
            continue
        out.append({
            "plan_id": r["plan_id"], "id": r["id"], "person": person_of[r["plan_id"]],
            "name": r["name"], "ynab_group": r["group_name"], "hidden": info["hidden"],
            "shared": info["name"], "group": info["group"], "key": info["key"],
            "mapped": mapped.get(r["plan_id"], {}).get(r["id"]),
            "spend_12m": money(spend.get((r["plan_id"], r["id"])) or 0),
        })
    out.sort(key=lambda c: (order.get(c["person"], 99), c["ynab_group"] or "", c["name"]))
    return {"people": people, "categories": out}


def meta() -> dict:
    cfg = config.load()
    people = [{"name": p["name"], "color": p["color"]} for p in cfg["people"]]
    with db.connect() as conn:
        plans = active_plans(conn, [p["name"] for p in people]) if people else []
        ids = [p["id"] for p in plans]
        first = None
        if ids:
            first = conn.execute(
                f"SELECT MIN(substr(date, 1, 7)) FROM transactions"
                f" WHERE deleted = 0 AND plan_id IN ({','.join('?' * len(ids))})", ids).fetchone()[0]
    currencies = sorted({p["iso_code"] for p in plans if p["iso_code"]})
    return {
        "people": people,
        "plans": [{"person": p["person"], "name": p["name"], "currency": p["iso_code"],
                   "last_sync": p["last_sync"]} for p in plans],
        "currency": currencies[0] if currencies else "USD",
        "mixed_currencies": len(currencies) > 1,
        "first_month": first,
        "today": date.today().strftime("%Y-%m"),
    }
