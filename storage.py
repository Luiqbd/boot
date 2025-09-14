# storage.py

import sqlite3
from threading import Lock
from typing import List, Tuple, Optional

DB_PATH = "positions.db"

_schema_sql = """
CREATE TABLE IF NOT EXISTS positions (
    pair       TEXT PRIMARY KEY,
    amount     INTEGER NOT NULL,
    avg_price  REAL NOT NULL
);
"""

_lock = Lock()

def _get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(_schema_sql)
    return conn

def add_position(pair: str, amount: int, avg_price: float) -> None:
    with _lock, _get_conn() as conn:
        conn.execute(
            "REPLACE INTO positions(pair, amount, avg_price) VALUES (?, ?, ?)",
            (pair, amount, avg_price)
        )

def get_all_positions() -> List[Tuple[str, int, float]]:
    with _lock, _get_conn() as conn:
        rows = conn.execute("SELECT pair, amount, avg_price FROM positions").fetchall()
    return rows

def remove_position(pair: str) -> None:
    with _lock, _get_conn() as conn:
        conn.execute("DELETE FROM positions WHERE pair = ?", (pair,))

def clear_all_positions() -> None:
    with _lock, _get_conn() as conn:
        conn.execute("DELETE FROM positions")
