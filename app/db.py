"""SQLite 存储层：建表、连接、通用读写辅助。

表结构对应简历里的三块：FAQ 知识条目、会话消息（含 AI 决策）、工单。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS faq (
    id          TEXT PRIMARY KEY,
    category    TEXT NOT NULL,
    question    TEXT NOT NULL,
    variants    TEXT NOT NULL DEFAULT '[]',
    models      TEXT NOT NULL DEFAULT '[]',
    scene       TEXT NOT NULL DEFAULT '',
    answer      TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    owner       TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT '生效',
    tags        TEXT NOT NULL DEFAULT '[]',
    priority    TEXT NOT NULL DEFAULT 'P2',
    hits        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS faq_fts USING fts5(
    entry_id UNINDEXED,
    text,
    tokenize='trigram'
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    customer    TEXT NOT NULL DEFAULT '',
    channel     TEXT NOT NULL DEFAULT '官网',
    model       TEXT NOT NULL DEFAULT '',
    scene       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL,
    intent      TEXT NOT NULL DEFAULT '',
    scene       TEXT NOT NULL DEFAULT '',
    emotion     TEXT NOT NULL DEFAULT '平稳',
    confidence  REAL NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',
    faq_ids     TEXT NOT NULL DEFAULT '[]',
    decision    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    reviewed_at TEXT,
    reviewer    TEXT,
    feedback    TEXT,
    feedback_at TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    id                TEXT PRIMARY KEY,
    no                TEXT NOT NULL UNIQUE,
    session_id        TEXT,
    message_id        TEXT,
    question          TEXT NOT NULL,
    intent            TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT '',
    scene             TEXT NOT NULL DEFAULT '',
    faq_ids           TEXT NOT NULL DEFAULT '[]',
    ai_answer         TEXT NOT NULL DEFAULT '',
    handoff_reason    TEXT NOT NULL DEFAULT '',
    priority          TEXT NOT NULL DEFAULT 'P2',
    owner             TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT '待处理',
    first_response_at TEXT,
    resolved_at       TEXT,
    root_cause        TEXT NOT NULL DEFAULT '',
    solution          TEXT NOT NULL DEFAULT '',
    satisfaction      INTEGER,
    suggest_faq       INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    sla_first_due     TEXT NOT NULL DEFAULT '',
    sla_resolve_due   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    ref        TEXT NOT NULL DEFAULT '',
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_msg_status ON messages(status);
CREATE INDEX IF NOT EXISTS idx_ticket_status ON tickets(status);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def row_to_dict(row: sqlite3.Row, json_fields: Iterable[str] = ()) -> dict[str, Any]:
    data = dict(row)
    for field in json_fields:
        if field in data and isinstance(data[field], str):
            try:
                data[field] = json.loads(data[field])
            except json.JSONDecodeError:
                data[field] = []
    return data


def log_event(conn: sqlite3.Connection, kind: str, ref: str = "", payload: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO events(kind, ref, payload, created_at) VALUES(?,?,?,?)",
        (kind, ref, json.dumps(payload or {}, ensure_ascii=False), now_iso()),
    )


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
