"""config.yml is re-read on every use, so edits apply without restarting the container."""
import os
import unicodedata

import yaml

CONFIG_PATH = os.environ.get("CONFIG_PATH", "/config/config.yml")
DEFAULT_COLORS = ["#4f8cff", "#ff6b9a", "#2ec4b6", "#ffb347"]


def load() -> dict:
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f) or {}
    for i, person in enumerate(cfg.setdefault("people", [])):
        person.setdefault("plans", [])
        person.setdefault("color", DEFAULT_COLORS[i % len(DEFAULT_COLORS)])
    combined = cfg.setdefault("combined", {}) or {}
    cfg["combined"] = combined
    combined["exclude_payees"] = {normalize(p) for p in combined.get("exclude_payees") or []}
    combined["exclude_categories"] = {normalize(c) for c in combined.get("exclude_categories") or []}
    cfg["group_aliases"] = {normalize(k): v for k, v in (cfg.get("group_aliases") or {}).items()}
    cfg["savings_groups"] = {normalize(g) for g in cfg.get("savings_groups") or []}
    cfg["savings_categories"] = {normalize(c) for c in cfg.get("savings_categories") or []}
    cfg.setdefault("sync_interval_minutes", 30)
    return cfg


def normalize(name: str | None) -> str:
    """'🛒 Groceries ' and 'groceries' compare equal: lowercase, letters/digits only."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name).lower()
    kept = "".join(ch if ch.isalnum() else " " for ch in text if not unicodedata.combining(ch))
    return " ".join(kept.split())
