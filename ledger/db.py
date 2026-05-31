"""SQLite schema and connection management."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "ledger.db"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_ts      TEXT NOT NULL,          -- ISO-8601 UTC decision cutoff
                nav_before  REAL NOT NULL,
                cash_before REAL NOT NULL,
                note        TEXT
            );

            CREATE TABLE IF NOT EXISTS orders (
                order_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER NOT NULL REFERENCES runs(run_id),
                ticker      TEXT NOT NULL,
                side        TEXT NOT NULL,          -- 'buy' | 'sell'
                intended_pct REAL NOT NULL,         -- % of NAV intended
                limit_price REAL NOT NULL,          -- agent's entry price
                shares      INTEGER NOT NULL,
                status      TEXT NOT NULL,          -- 'filled' | 'missed' | 'skipped'
                fill_price  REAL,
                slippage    REAL,
                ts          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                ticker      TEXT PRIMARY KEY,
                shares      INTEGER NOT NULL,
                avg_cost    REAL NOT NULL,
                last_price  REAL NOT NULL,
                updated_ts  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cash_ledger (
                tx_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER REFERENCES runs(run_id),
                delta       REAL NOT NULL,          -- positive = inflow, negative = outflow
                reason      TEXT NOT NULL,          -- 'fill' | 'injection' | 'withdrawal'
                ts          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS nav_history (
                nav_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT NOT NULL,
                nav         REAL NOT NULL,
                cash        REAL NOT NULL,
                equity      REAL NOT NULL,
                spy_price   REAL,
                qqq_price   REAL,
                rsp_price   REAL
            );
        """)
