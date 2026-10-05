"""SQLite mirror of the YNAB plans. Rows are upserted as YNAB sends them (deleted ones keep deleted=1)."""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "/data/ynab.sqlite")

# YNAB entity -> (table, columns copied from the API object). Every table also has plan_id.
ENTITIES = {
    "accounts": ("accounts", ["id", "name", "type", "on_budget", "closed", "balance", "deleted"]),
    "payees": ("payees", ["id", "name", "transfer_account_id", "deleted"]),
    "category_groups": ("category_groups", ["id", "name", "hidden", "internal", "deleted"]),
    "categories": ("categories", ["id", "category_group_id", "name", "hidden", "internal",
                                  "budgeted", "activity", "balance", "goal_target", "deleted"]),
    "transactions": ("transactions", ["id", "date", "amount", "memo", "account_id", "payee_id",
                                      "category_id", "transfer_account_id", "approved", "deleted"]),
    "subtransactions": ("subtransactions", ["id", "transaction_id", "amount", "memo", "payee_id",
                                            "category_id", "transfer_account_id", "deleted"]),
}
MONTH_COLS = ["month", "income", "budgeted", "activity", "to_be_budgeted", "age_of_money", "deleted"]
MONTH_CATEGORY_COLS = ["id", "budgeted", "activity", "balance", "deleted"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY, person TEXT NOT NULL, name TEXT, iso_code TEXT,
    server_knowledge INTEGER, last_sync TEXT, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS accounts (
    plan_id TEXT, id TEXT, name TEXT, type TEXT, on_budget INTEGER, closed INTEGER,
    balance INTEGER, deleted INTEGER, PRIMARY KEY (plan_id, id)
);
CREATE TABLE IF NOT EXISTS payees (
    plan_id TEXT, id TEXT, name TEXT, transfer_account_id TEXT, deleted INTEGER,
    PRIMARY KEY (plan_id, id)
);
CREATE TABLE IF NOT EXISTS category_groups (
    plan_id TEXT, id TEXT, name TEXT, hidden INTEGER, internal INTEGER, deleted INTEGER,
    PRIMARY KEY (plan_id, id)
);
CREATE TABLE IF NOT EXISTS categories (
    plan_id TEXT, id TEXT, category_group_id TEXT, name TEXT, hidden INTEGER, internal INTEGER,
    budgeted INTEGER, activity INTEGER, balance INTEGER, goal_target INTEGER, deleted INTEGER,
    PRIMARY KEY (plan_id, id)
);
CREATE TABLE IF NOT EXISTS transactions (
    plan_id TEXT, id TEXT, date TEXT, amount INTEGER, memo TEXT, account_id TEXT, payee_id TEXT,
    category_id TEXT, transfer_account_id TEXT, approved INTEGER, deleted INTEGER,
    PRIMARY KEY (plan_id, id)
);
CREATE INDEX IF NOT EXISTS transactions_date ON transactions (plan_id, date);
CREATE TABLE IF NOT EXISTS subtransactions (
    plan_id TEXT, id TEXT, transaction_id TEXT, amount INTEGER, memo TEXT, payee_id TEXT,
    category_id TEXT, transfer_account_id TEXT, deleted INTEGER, PRIMARY KEY (plan_id, id)
);
CREATE INDEX IF NOT EXISTS subtransactions_parent ON subtransactions (plan_id, transaction_id);
CREATE TABLE IF NOT EXISTS months (
    plan_id TEXT, month TEXT, income INTEGER, budgeted INTEGER, activity INTEGER,
    to_be_budgeted INTEGER, age_of_money INTEGER, deleted INTEGER, PRIMARY KEY (plan_id, month)
);
CREATE TABLE IF NOT EXISTS month_categories (
    plan_id TEXT, month TEXT, id TEXT, budgeted INTEGER, activity INTEGER, balance INTEGER,
    deleted INTEGER, PRIMARY KEY (plan_id, month, id)
);

-- One row per categorised line: split transactions are replaced by their subtransactions.
CREATE VIEW IF NOT EXISTS lines AS
SELECT t.plan_id, t.id AS transaction_id, t.date, t.account_id,
       t.amount, t.category_id, t.payee_id, t.transfer_account_id, t.memo
FROM transactions t
WHERE t.deleted = 0 AND NOT EXISTS (
    SELECT 1 FROM subtransactions s
    WHERE s.plan_id = t.plan_id AND s.transaction_id = t.id AND s.deleted = 0)
UNION ALL
SELECT t.plan_id, t.id, t.date, t.account_id,
       s.amount, s.category_id, COALESCE(s.payee_id, t.payee_id), s.transfer_account_id,
       COALESCE(s.memo, t.memo)
FROM transactions t
JOIN subtransactions s ON s.plan_id = t.plan_id AND s.transaction_id = t.id AND s.deleted = 0
WHERE t.deleted = 0;
"""


@contextmanager
def connect():
    """Connection that commits on success, rolls back on error, and always closes."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


def upsert(conn: sqlite3.Connection, table: str, plan_id: str, cols: list[str], rows: list[dict],
           extra: dict | None = None) -> None:
    extra = extra or {}
    names = ["plan_id", *extra.keys(), *cols]
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})"
    conn.executemany(sql, [(plan_id, *extra.values(), *(r.get(c) for c in cols)) for r in rows])
