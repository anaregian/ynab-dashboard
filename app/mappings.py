"""Category mappings edited from the Categories page: {plan_id: {category_id: shared name}}.

Keyed by YNAB ids, so renaming a category in YNAB keeps its mapping. Categories whose shared
names normalize to the same text are merged in the stats.
"""
import json
import os
import threading

PATH = os.path.join(os.path.dirname(os.environ.get("DB_PATH", "/data/ynab.sqlite")), "mappings.json")
_lock = threading.Lock()


def load() -> dict[str, dict[str, str]]:
    try:
        with open(PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def set_shared(plan_id: str, category_id: str, shared: str | None) -> None:
    """Map a category onto a shared name, or back to its own name when shared is None."""
    with _lock:
        data = load()
        plan = data.setdefault(plan_id, {})
        if shared:
            plan[category_id] = shared
        else:
            plan.pop(category_id, None)
            if not plan:
                del data[plan_id]
        tmp = PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, PATH)
