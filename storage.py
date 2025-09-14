# storage.py

import sqlite3
from threading import Lock
from typing import List, Tuple

from metrics import OPEN_POSITIONS

DB_PATH = "positions.db"

_lock = Lock()
_schema = """
CREATE TABLE IF NOT EXISTS positions (
    pair      TEXT PRIMARY KEY,
    amount    INTEGER NOT NULL,
    avg_price REAL NOT NULL
);
"""

def _get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(_schema)
    return conn

def add_position(pair: str, amount: int, avg_price: float):
    with _lock, _get_conn() as conn:
        conn.execute(
            "REPLACE INTO positions(pair, amount, avg_price) VALUES (?, ?, ?)",
            (pair, amount, avg_price)
        )
    OPEN_POSITIONS.set(len(get_all_positions()))

def get_all_positions() -> List[Tuple[str, int, float]]:
    with _lock, _get_conn() as conn:
        return conn.execute(
            "SELECT pair, amount, avg_price FROM positions"
        ).fetchall()

def remove_position(pair: str):
    with _lock, _get_conn() as conn:
        conn.execute("DELETE FROM positions WHERE pair = ?", (pair,))
    OPEN_POSITIONS.set(len(get_all_positions()))
