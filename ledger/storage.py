"""
Dual-mode storage backend.

USE_SUPABASE=false (default) → SQLite  (local dev, tests)
USE_SUPABASE=true            → Supabase (Routines cloud environment)

All ledger code imports from here instead of using sqlite3 directly.
The interface is intentionally minimal: insert, upsert, select, update, delete, sum.
"""

from __future__ import annotations
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
load_dotenv()

USE_SUPABASE: bool = os.getenv("USE_SUPABASE", "false").lower() == "true"

# ── SQLite path (local only) ─────────────────────────────────────────────────
DB_PATH = Path(__file__).parent.parent / "data" / "ledger.db"


# ── Supabase client (lazy-loaded) ────────────────────────────────────────────
_supabase_client = None

def _get_supabase():
    global _supabase_client
    if _supabase_client is None:
        from supabase import create_client
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _supabase_client = create_client(url, key)
    return _supabase_client


# ── SQLite helpers ───────────────────────────────────────────────────────────

def _sqlite_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class _SQLiteCtx:
    """Context manager wrapping sqlite3 connection — keeps existing 'with get_conn()' pattern."""
    def __enter__(self):
        self._conn = _sqlite_conn()
        return self._conn
    def __exit__(self, *_):
        self._conn.commit()
        self._conn.close()


def get_conn():
    """Returns context manager. Use: with get_conn() as conn: conn.execute(...)"""
    if USE_SUPABASE:
        raise RuntimeError(
            "get_conn() is SQLite-only. "
            "In Supabase mode use storage.insert/select/update/upsert/delete directly."
        )
    return _SQLiteCtx()


# ── Unified storage operations (work in both modes) ──────────────────────────

def insert(table: str, data: dict) -> int | None:
    """Insert a row. Returns the new row id (SQLite) or None (Supabase)."""
    if USE_SUPABASE:
        sb = _get_supabase()
        result = sb.table(table).insert(data).execute()
        rows = result.data
        return rows[0].get("id") if rows else None
    else:
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        with get_conn() as conn:
            cur = conn.execute(
                f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
                list(data.values()),
            )
            return cur.lastrowid


def upsert(table: str, data: dict, conflict_col: str = "ticker") -> None:
    """Insert or replace on conflict."""
    if USE_SUPABASE:
        sb = _get_supabase()
        sb.table(table).upsert(data, on_conflict=conflict_col).execute()
    else:
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        updates = ", ".join(
            f"{k}=excluded.{k}" for k in data if k != conflict_col
        )
        sql = (
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict_col}) DO UPDATE SET {updates}"
        )
        with get_conn() as conn:
            conn.execute(sql, list(data.values()))


def select(
    table: str,
    cols: str = "*",
    where: dict | None = None,
    order_by: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Select rows. where = {col: value} (AND equality only)."""
    if USE_SUPABASE:
        sb = _get_supabase()
        q = sb.table(table).select(cols)
        if where:
            for k, v in where.items():
                q = q.eq(k, v)
        if order_by:
            col, *rest = order_by.split(" ")
            desc = bool(rest and rest[0].upper() == "DESC")
            q = q.order(col, desc=desc)
        if limit:
            q = q.limit(limit)
        result = q.execute()
        return result.data or []
    else:
        parts = [f"SELECT {cols} FROM {table}"]
        params = []
        if where:
            clauses = [f"{k}=?" for k in where]
            parts.append("WHERE " + " AND ".join(clauses))
            params = list(where.values())
        if order_by:
            parts.append(f"ORDER BY {order_by}")
        if limit:
            parts.append(f"LIMIT {limit}")
        with get_conn() as conn:
            rows = conn.execute(" ".join(parts), params).fetchall()
            return [dict(r) for r in rows]


def update(table: str, data: dict, where: dict) -> None:
    """Update rows matching where conditions."""
    if USE_SUPABASE:
        sb = _get_supabase()
        q = sb.table(table).update(data)
        for k, v in where.items():
            q = q.eq(k, v)
        q.execute()
    else:
        set_clause = ", ".join(f"{k}=?" for k in data)
        where_clause = " AND ".join(f"{k}=?" for k in where)
        params = list(data.values()) + list(where.values())
        with get_conn() as conn:
            conn.execute(
                f"UPDATE {table} SET {set_clause} WHERE {where_clause}",
                params,
            )


def delete(table: str, where: dict) -> None:
    """Delete rows matching where conditions."""
    if USE_SUPABASE:
        sb = _get_supabase()
        q = sb.table(table).delete()
        for k, v in where.items():
            q = q.eq(k, v)
        q.execute()
    else:
        where_clause = " AND ".join(f"{k}=?" for k in where)
        with get_conn() as conn:
            conn.execute(
                f"DELETE FROM {table} WHERE {where_clause}",
                list(where.values()),
            )


def scalar_sum(table: str, col: str, where: dict | None = None) -> float:
    """Return SUM(col) from table, optionally filtered."""
    if USE_SUPABASE:
        # Supabase doesn't support aggregates via REST — fetch all rows and sum
        rows = select(table, cols=col, where=where)
        return sum(r.get(col, 0) or 0 for r in rows)
    else:
        parts = [f"SELECT COALESCE(SUM({col}), 0) as total FROM {table}"]
        params = []
        if where:
            clauses = [f"{k}=?" for k in where]
            parts.append("WHERE " + " AND ".join(clauses))
            params = list(where.values())
        with get_conn() as conn:
            row = conn.execute(" ".join(parts), params).fetchone()
            return float(row["total"])


def init_schema() -> None:
    """Create tables if they don't exist (SQLite only — Supabase uses supabase_schema.sql)."""
    if USE_SUPABASE:
        return  # schema must be applied via Supabase SQL editor
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_ts      TEXT NOT NULL,
                nav_before  REAL NOT NULL,
                cash_before REAL NOT NULL,
                note        TEXT
            );
            CREATE TABLE IF NOT EXISTS orders (
                order_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER REFERENCES runs(run_id),
                ticker      TEXT NOT NULL,
                side        TEXT NOT NULL,
                intended_pct REAL NOT NULL,
                limit_price REAL NOT NULL,
                shares      INTEGER NOT NULL,
                status      TEXT NOT NULL,
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
                tx_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id  INTEGER REFERENCES runs(run_id),
                delta   REAL NOT NULL,
                reason  TEXT NOT NULL,
                ts      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS nav_history (
                nav_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT NOT NULL,
                nav       REAL NOT NULL,
                cash      REAL NOT NULL,
                equity    REAL NOT NULL,
                spy_price REAL,
                qqq_price REAL,
                rsp_price REAL
            );
            CREATE TABLE IF NOT EXISTS watchlist (
                ticker         TEXT PRIMARY KEY,
                layer          TEXT NOT NULL DEFAULT 'core',
                active         INTEGER NOT NULL DEFAULT 1,
                cooldown_until TEXT,
                added_ts       TEXT NOT NULL,
                note           TEXT
            );
            CREATE TABLE IF NOT EXISTS conviction_store (
                ticker     TEXT PRIMARY KEY,
                conviction REAL,
                status     TEXT NOT NULL DEFAULT 'not_reviewed',
                run_ts     TEXT NOT NULL,
                source     TEXT NOT NULL DEFAULT 'execution_agent'
            );
            CREATE TABLE IF NOT EXISTS verdict_store (
                ticker  TEXT PRIMARY KEY,
                verdict TEXT NOT NULL DEFAULT 'not_reviewed',
                run_ts  TEXT NOT NULL,
                source  TEXT NOT NULL DEFAULT 'fundamental_agent'
            );
            CREATE TABLE IF NOT EXISTS agent_outputs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_ts      TEXT NOT NULL,
                agent_name  TEXT NOT NULL,
                output_json TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );
        """)
