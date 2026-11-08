import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

PATH = Path(os.getenv("AGENT_STATE_DB", "/tmp/agent.sqlite3"))


@contextmanager
def db():
    PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def initialize():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
          school_id TEXT NOT NULL DEFAULT 'horizonte', guardian_id TEXT NOT NULL, chatwoot_conversation_id INTEGER,
          chatwoot_contact_id INTEGER, mode TEXT NOT NULL DEFAULT 'auto', updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (school_id, guardian_id)
        );
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT, school_id TEXT NOT NULL DEFAULT 'horizonte', guardian_id TEXT NOT NULL,
          sender TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS locally_delivered_chatwoot_messages (
          chatwoot_message_id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS pending_outgoing (
          id INTEGER PRIMARY KEY AUTOINCREMENT, chatwoot_conversation_id INTEGER NOT NULL, content TEXT NOT NULL, created REAL NOT NULL
        );
        """)
        _migrate_single_school(conn)


def _migrate_single_school(conn):
    """Pre-multi-school databases keyed conversations by guardian_id alone; they all belonged to 'horizonte'."""
    if "school_id" not in [row["name"] for row in conn.execute("PRAGMA table_info(conversations)")]:
        conn.executescript("""
        ALTER TABLE conversations RENAME TO conversations_old;
        CREATE TABLE conversations (
          school_id TEXT NOT NULL DEFAULT 'horizonte', guardian_id TEXT NOT NULL, chatwoot_conversation_id INTEGER,
          chatwoot_contact_id INTEGER, mode TEXT NOT NULL DEFAULT 'auto', updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (school_id, guardian_id)
        );
        INSERT INTO conversations (guardian_id, chatwoot_conversation_id, chatwoot_contact_id, mode, updated_at)
          SELECT guardian_id, chatwoot_conversation_id, chatwoot_contact_id, mode, updated_at FROM conversations_old;
        DROP TABLE conversations_old;
        """)
    if "school_id" not in [row["name"] for row in conn.execute("PRAGMA table_info(messages)")]:
        conn.execute("ALTER TABLE messages ADD COLUMN school_id TEXT NOT NULL DEFAULT 'horizonte'")


def conversation(school_id: str, guardian_id: str):
    with db() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE school_id=? AND guardian_id=?", (school_id, guardian_id)).fetchone()
        return dict(row) if row else None


def conversation_by_chatwoot_id(conversation_id: int | None):
    with db() as conn:
        row = conn.execute("SELECT school_id, guardian_id FROM conversations WHERE chatwoot_conversation_id=?", (conversation_id,)).fetchone()
        return dict(row) if row else None


def save_conversation(school_id: str, guardian_id: str, conversation_id: int | None, contact_id: int | None, mode: str = "auto"):
    with db() as conn:
        conn.execute("""INSERT INTO conversations (school_id, guardian_id, chatwoot_conversation_id, chatwoot_contact_id, mode)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(school_id, guardian_id) DO UPDATE SET
            chatwoot_conversation_id=COALESCE(excluded.chatwoot_conversation_id, chatwoot_conversation_id),
            chatwoot_contact_id=COALESCE(excluded.chatwoot_contact_id, chatwoot_contact_id), mode=excluded.mode,
            updated_at=CURRENT_TIMESTAMP""", (school_id, guardian_id, conversation_id, contact_id, mode))


def save_message(school_id: str, guardian_id: str, sender: str, content: str):
    with db() as conn:
        conn.execute("INSERT INTO messages (school_id, guardian_id, sender, content) VALUES (?, ?, ?, ?)", (school_id, guardian_id, sender, content))


def history(school_id: str, guardian_id: str):
    with db() as conn:
        return [dict(row) for row in conn.execute("SELECT sender, content, created_at FROM messages WHERE school_id=? AND guardian_id=? ORDER BY id", (school_id, guardian_id))]


def mark_local_delivery(chatwoot_message_id: int | None) -> None:
    if chatwoot_message_id is None:
        return
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO locally_delivered_chatwoot_messages VALUES (?)", (chatwoot_message_id,))


def was_locally_delivered(chatwoot_message_id: int | None) -> bool:
    if chatwoot_message_id is None:
        return False
    with db() as conn:
        return conn.execute("SELECT 1 FROM locally_delivered_chatwoot_messages WHERE chatwoot_message_id=?", (chatwoot_message_id,)).fetchone() is not None


PENDING_TTL_SECONDS = 120


def expect_local_delivery(chatwoot_conversation_id: int, content: str) -> None:
    """Register a message we are about to post to Chatwoot. Chatwoot can fire its message_created webhook
    before our POST returns (so before the message id is known); the webhook matches on this instead."""
    with db() as conn:
        conn.execute("DELETE FROM pending_outgoing WHERE created < ?", (time.time() - PENDING_TTL_SECONDS,))
        conn.execute("INSERT INTO pending_outgoing (chatwoot_conversation_id, content, created) VALUES (?, ?, ?)",
                     (chatwoot_conversation_id, content.strip(), time.time()))


def consume_local_delivery(chatwoot_conversation_id: int | None, content: str) -> bool:
    """True (once per registered message) if this webhook content is one we posted ourselves."""
    with db() as conn:
        row = conn.execute("SELECT id FROM pending_outgoing WHERE chatwoot_conversation_id=? AND content=? AND created >= ? ORDER BY id LIMIT 1",
                           (chatwoot_conversation_id, content.strip(), time.time() - PENDING_TTL_SECONDS)).fetchone()
        if row:
            conn.execute("DELETE FROM pending_outgoing WHERE id=?", (row["id"],))
        return row is not None
