"""SQLite storage: one file, WAL mode, safe to use from several threads with one connection each."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  original_path TEXT NOT NULL,
  current_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  dhash TEXT,
  size INTEGER NOT NULL DEFAULT 0,
  doc_type TEXT,
  sensitivity TEXT,
  confidence REAL,
  title TEXT,
  summary TEXT,
  fields_json TEXT NOT NULL DEFAULT '{}',
  checks_json TEXT NOT NULL DEFAULT '[]',
  text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued',
  duplicate_of INTEGER,
  vaulted INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS files_path ON files(current_path);
CREATE INDEX IF NOT EXISTS files_sha ON files(sha256);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL REFERENCES files(id),
  stage TEXT NOT NULL DEFAULT 'sense',
  state TEXT NOT NULL DEFAULT 'queued',
  checkpoint_json TEXT NOT NULL DEFAULT '{}',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  lease_until REAL,
  not_before REAL NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_state ON tasks(state, not_before);

CREATE TABLE IF NOT EXISTS journal (
  id INTEGER PRIMARY KEY,
  task_id INTEGER,
  file_id INTEGER NOT NULL,
  tool TEXT NOT NULL,
  args_json TEXT NOT NULL,
  result_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS inbox (
  id INTEGER PRIMARY KEY,
  task_id INTEGER,
  file_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  actions_json TEXT NOT NULL DEFAULT '[]',
  state TEXT NOT NULL DEFAULT 'open',
  resolution_json TEXT,
  created_at REAL NOT NULL,
  resolved_at REAL
);

CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  due_date TEXT NOT NULL,
  amount TEXT,
  state TEXT NOT NULL DEFAULT 'active',
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
  id INTEGER PRIMARY KEY,
  match_json TEXT NOT NULL,
  effect_json TEXT NOT NULL,
  source TEXT NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  level TEXT NOT NULL,
  stage TEXT NOT NULL,
  file_id INTEGER,
  message TEXT NOT NULL,
  data_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS embeddings (
  file_id INTEGER PRIMARY KEY,
  vector_json TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(file_id UNINDEXED, title, summary, body, doc_type);
"""


def connect(db_path: Path, init: bool = True) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    if init:
        conn.executescript(SCHEMA)
    return conn


@contextmanager
def tx(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def now() -> float:
    return time.time()


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(s, default=None):
    return json.loads(s) if s else default
