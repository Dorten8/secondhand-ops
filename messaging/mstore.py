#!/usr/bin/env python3
"""mstore.py — durable state for the messaging lane.

One SQLite file is the single source of truth. Everything else (pollers,
drafters, senders, agents, humans) is a *stateless actor* reading and writing
here, which is what makes the lane portable: any process on any machine can run
any stage without holding state in memory.

Tables
  conversations  one row per (provider, conversation_id) — the ad + counterpart
  messages       every message we have seen, inbound or outbound
  drafts         proposed replies awaiting a human decision
  events         the queue the agent works from (what needs attention, why)
  runs           audit trail: who did what, when, with what result

Design rules
  * Every write is idempotent where possible (UNIQUE keys, INSERT OR IGNORE).
  * Status transitions are explicit and checked; nothing "just happens".
  * Nothing here knows about Telegram, Hermes, DBA or Facebook. Providers
    translate; this file only stores.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = Path("/home/hermes/hermes/home/messaging/state.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id             INTEGER PRIMARY KEY,
  provider       TEXT NOT NULL,              -- 'dba' | 'fb' | ...
  conv_id        TEXT NOT NULL,              -- provider's opaque thread id
  item_id        TEXT,                       -- provider's listing id
  item_title     TEXT,
  partner_name   TEXT,
  partner_id     TEXT,
  last_preview   TEXT,
  last_outgoing  INTEGER,                    -- 1 = we spoke last
  unseen         INTEGER DEFAULT 0,
  sold           INTEGER DEFAULT 0,
  last_at        TEXT,                       -- provider's lastMessageDate
  updated_at     REAL,
  UNIQUE(provider, conv_id)
);

CREATE TABLE IF NOT EXISTS messages (
  id             INTEGER PRIMARY KEY,
  provider       TEXT NOT NULL,
  conv_id        TEXT NOT NULL,
  msg_hash       TEXT NOT NULL,              -- dedupe key
  direction      TEXT NOT NULL,              -- 'in' | 'out'
  body           TEXT,
  seen_at        REAL,
  UNIQUE(provider, conv_id, msg_hash)
);

CREATE TABLE IF NOT EXISTS drafts (
  id             INTEGER PRIMARY KEY,
  provider       TEXT NOT NULL,
  conv_id        TEXT NOT NULL,
  item_title     TEXT,
  partner_name   TEXT,
  inbound        TEXT,                       -- their message we are answering
  body           TEXT NOT NULL,              -- the proposed reply
  status         TEXT NOT NULL DEFAULT 'pending',
                 -- pending | sent | ignored | superseded | failed
  created_at     REAL,
  decided_at     REAL,
  decided_by     TEXT,
  result         TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id             INTEGER PRIMARY KEY,
  kind           TEXT NOT NULL,              -- 'needs_draft' | 'draft_ready' | 'draft_failed'
  provider       TEXT NOT NULL,
  conv_id        TEXT NOT NULL,
  payload        TEXT,
  state          TEXT NOT NULL DEFAULT 'open',  -- open | claimed | done
  created_at     REAL,
  UNIQUE(kind, provider, conv_id, payload)
);

CREATE TABLE IF NOT EXISTS runs (
  id             INTEGER PRIMARY KEY,
  stage          TEXT NOT NULL,
  provider       TEXT,
  started_at     REAL,
  finished_at    REAL,
  status         TEXT,
  detail         TEXT
);
"""

# drafts.status transitions that are legal. Anything else is a bug, not a choice.
LEGAL = {
    "pending":  {"sent", "ignored", "superseded", "failed"},
    "sent":     set(),
    "ignored":  set(),
    "superseded": set(),
    "failed":   {"pending"},          # a failed send may be retried
}


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        # additive migration for stores created before last_at existed
        try:
            self.db.execute("ALTER TABLE conversations ADD COLUMN last_at TEXT")
        except sqlite3.OperationalError:
            pass
        self.db.commit()

    # ---------- conversations ----------
    def upsert_conversation(self, provider: str, conv_id: str, **f) -> int:
        now = time.time()
        self.db.execute(
            """INSERT INTO conversations
                 (provider, conv_id, item_id, item_title, partner_name, partner_id,
                  last_preview, last_outgoing, unseen, sold, last_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(provider, conv_id) DO UPDATE SET
                 item_id=excluded.item_id, item_title=excluded.item_title,
                 partner_name=excluded.partner_name, partner_id=excluded.partner_id,
                 last_preview=excluded.last_preview,
                 last_outgoing=excluded.last_outgoing, unseen=excluded.unseen,
                 sold=excluded.sold, last_at=excluded.last_at,
                 updated_at=excluded.updated_at""",
            (provider, conv_id, f.get("item_id"), f.get("item_title"),
             f.get("partner_name"), f.get("partner_id"), f.get("last_preview"),
             1 if f.get("last_outgoing") else 0, int(f.get("unseen") or 0),
             1 if f.get("sold") else 0, f.get("last_at"), now))
        self.db.commit()
        row = self.db.execute(
            "SELECT id FROM conversations WHERE provider=? AND conv_id=?",
            (provider, conv_id)).fetchone()
        return int(row["id"])

    def conversations(self, provider: str | None = None):
        q = "SELECT * FROM conversations"
        args = ()
        if provider:
            q += " WHERE provider=?"
            args = (provider,)
        return [dict(r) for r in self.db.execute(q + " ORDER BY updated_at DESC", args)]

    # ---------- messages ----------
    def add_message(self, provider: str, conv_id: str, body: str, direction: str) -> bool:
        """True if newly stored, False if already known."""
        # Python's hash() is salted per process (PYTHONHASHSEED), so it produced a
        # different key on every run and every message was re-inserted. sha1 is stable.
        h = hashlib.sha1(
            f"{provider}|{conv_id}|{direction}|{body}".encode("utf-8")).hexdigest()
        cur = self.db.execute(
            """INSERT OR IGNORE INTO messages
                 (provider, conv_id, msg_hash, direction, body, seen_at)
               VALUES (?,?,?,?,?,?)""",
            (provider, conv_id, h, direction, body, time.time()))
        self.db.commit()
        return cur.rowcount > 0

    def messages(self, provider: str, conv_id: str):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM messages WHERE provider=? AND conv_id=? ORDER BY seen_at",
            (provider, conv_id))]

    # ---------- events ----------
    def push_event(self, kind: str, provider: str, conv_id: str, payload: str = "") -> bool:
        cur = self.db.execute(
            """INSERT OR IGNORE INTO events (kind, provider, conv_id, payload, state, created_at)
               VALUES (?,?,?,?, 'open', ?)""",
            (kind, provider, conv_id, payload, time.time()))
        self.db.commit()
        return cur.rowcount > 0

    def open_events(self, kind: str | None = None):
        q, args = "SELECT * FROM events WHERE state='open'", ()
        if kind:
            q += " AND kind=?"
            args = (kind,)
        return [dict(r) for r in self.db.execute(q + " ORDER BY created_at", args)]

    def close_event(self, event_id: int):
        self.db.execute("UPDATE events SET state='done' WHERE id=?", (event_id,))
        self.db.commit()

    # ---------- drafts ----------
    def create_draft(self, provider: str, conv_id: str, body: str, inbound: str = "",
                     item_title: str = "", partner_name: str = "") -> int:
        cur = self.db.execute(
            """INSERT INTO drafts (provider, conv_id, item_title, partner_name,
                                   inbound, body, status, created_at)
               VALUES (?,?,?,?,?,?, 'pending', ?)""",
            (provider, conv_id, item_title, partner_name, inbound, body, time.time()))
        self.db.commit()
        return int(cur.lastrowid)

    def draft(self, draft_id: int):
        r = self.db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        return dict(r) if r else None

    def pending_drafts(self):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM drafts WHERE status='pending' ORDER BY created_at")]

    def decide(self, draft_id: int, status: str, by: str, result: str = "") -> None:
        d = self.draft(draft_id)
        if not d:
            raise KeyError(f"draft {draft_id} does not exist")
        if status not in LEGAL.get(d["status"], set()):
            raise ValueError(
                f"illegal draft transition {d['status']!r} -> {status!r} for draft {draft_id}")
        self.db.execute(
            "UPDATE drafts SET status=?, decided_at=?, decided_by=?, result=? WHERE id=?",
            (status, time.time(), by, result, draft_id))
        self.db.commit()

    # ---------- runs ----------
    def run(self, stage: str, provider: str | None = None):
        cur = self.db.execute(
            "INSERT INTO runs (stage, provider, started_at) VALUES (?,?,?)",
            (stage, provider, time.time()))
        self.db.commit()
        return int(cur.lastrowid)

    def end_run(self, run_id: int, status: str, detail: str = ""):
        self.db.execute(
            "UPDATE runs SET finished_at=?, status=?, detail=? WHERE id=?",
            (time.time(), status, detail[:2000], run_id))
        self.db.commit()

    def stats(self):
        out = {}
        for t in ("conversations", "messages", "drafts", "events"):
            out[t] = self.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        out["pending_drafts"] = len(self.pending_drafts())
        out["open_events"] = len(self.open_events())
        return out


if __name__ == "__main__":
    s = Store()
    print(json.dumps(s.stats(), indent=1))
