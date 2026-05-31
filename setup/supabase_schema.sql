-- Capital Surabaya — Supabase Schema
-- Run this once in Supabase SQL Editor: https://supabase.com/dashboard/project/_/sql

-- ── Core ledger tables ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS runs (
    run_id      SERIAL PRIMARY KEY,
    run_ts      TEXT NOT NULL,
    nav_before  REAL NOT NULL,
    cash_before REAL NOT NULL,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    order_id     SERIAL PRIMARY KEY,
    run_id       INTEGER REFERENCES runs(run_id),
    ticker       TEXT NOT NULL,
    side         TEXT NOT NULL,
    intended_pct REAL NOT NULL,
    limit_price  REAL NOT NULL,
    shares       INTEGER NOT NULL,
    status       TEXT NOT NULL,
    fill_price   REAL,
    slippage     REAL,
    ts           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    ticker      TEXT PRIMARY KEY,
    shares      INTEGER NOT NULL,
    avg_cost    REAL NOT NULL,
    last_price  REAL NOT NULL,
    updated_ts  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cash_ledger (
    tx_id   SERIAL PRIMARY KEY,
    run_id  INTEGER REFERENCES runs(run_id),
    delta   REAL NOT NULL,
    reason  TEXT NOT NULL,
    ts      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nav_history (
    nav_id    SERIAL PRIMARY KEY,
    ts        TEXT NOT NULL,
    nav       REAL NOT NULL,
    cash      REAL NOT NULL,
    equity    REAL NOT NULL,
    spy_price REAL,
    qqq_price REAL,
    rsp_price REAL
);

-- ── Watchlist ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS watchlist (
    ticker         TEXT PRIMARY KEY,
    layer          TEXT NOT NULL DEFAULT 'core',
    active         INTEGER NOT NULL DEFAULT 1,
    cooldown_until TEXT,
    added_ts       TEXT NOT NULL,
    note           TEXT
);

-- ── Agent state ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS conviction_store (
    ticker     TEXT PRIMARY KEY,
    conviction REAL,
    status     TEXT NOT NULL DEFAULT 'not_reviewed',
    run_ts     TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'execution_agent'
);

CREATE TABLE IF NOT EXISTS verdict_store (
    ticker TEXT PRIMARY KEY,
    verdict TEXT NOT NULL DEFAULT 'not_reviewed',
    run_ts TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'fundamental_agent'
);

-- ── Agent outputs (optional — for audit trail) ───────────────────────────────

CREATE TABLE IF NOT EXISTS agent_outputs (
    id         SERIAL PRIMARY KEY,
    run_ts     TEXT NOT NULL,
    agent      TEXT NOT NULL,
    output_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
