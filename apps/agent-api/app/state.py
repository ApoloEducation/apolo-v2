import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

PATH = Path(os.getenv("AGENT_STATE_DB", "/tmp/agent.sqlite3"))


@contextmanager
def db():
    PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
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
          guardian_id TEXT PRIMARY KEY, chatwoot_conversation_id INTEGER,
          chatwoot_contact_id INTEGER, mode TEXT NOT NULL DEFAULT 'auto', updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT, guardian_id TEXT NOT NULL, sender TEXT NOT NULL,
          content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS locally_delivered_chatwoot_messages (
          chatwoot_message_id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS processed_webhook_messages (
          chatwoot_message_id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS processed_whatsapp_messages (
          wamid TEXT PRIMARY KEY
        );
        """)


def conversation(guardian_id: str):
    with db() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE guardian_id=?", (guardian_id,)).fetchone()
        return dict(row) if row else None


def save_conversation(guardian_id: str, conversation_id: int | None, contact_id: int | None, mode: str = "auto"):
    with db() as conn:
        conn.execute("""INSERT INTO conversations (guardian_id, chatwoot_conversation_id, chatwoot_contact_id, mode)
            VALUES (?, ?, ?, ?) ON CONFLICT(guardian_id) DO UPDATE SET
            chatwoot_conversation_id=COALESCE(excluded.chatwoot_conversation_id, chatwoot_conversation_id),
            chatwoot_contact_id=COALESCE(excluded.chatwoot_contact_id, chatwoot_contact_id), mode=excluded.mode,
            updated_at=CURRENT_TIMESTAMP""", (guardian_id, conversation_id, contact_id, mode))


def save_message(guardian_id: str, sender: str, content: str):
    with db() as conn:
        conn.execute("INSERT INTO messages (guardian_id, sender, content) VALUES (?, ?, ?)", (guardian_id, sender, content))


def history(guardian_id: str):
    with db() as conn:
        return [dict(row) for row in conn.execute("SELECT sender, content, created_at FROM messages WHERE guardian_id=? ORDER BY id", (guardian_id,))]


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


def claim_webhook_message(chatwoot_message_id: int | None) -> bool:
    """True only the first time a Chatwoot message id is seen (webhook idempotency)."""
    if chatwoot_message_id is None:
        return True
    with db() as conn:
        return conn.execute("INSERT OR IGNORE INTO processed_webhook_messages VALUES (?)", (chatwoot_message_id,)).rowcount == 1


def claim_whatsapp_message(wamid: str | None) -> bool:
    """True only the first time a WhatsApp message id is seen (Meta retries webhooks that answer slowly)."""
    if not wamid:
        return True
    with db() as conn:
        return conn.execute("INSERT OR IGNORE INTO processed_whatsapp_messages VALUES (?)", (wamid,)).rowcount == 1


def guardian_for_conversation(chatwoot_conversation_id: int | None) -> str | None:
    with db() as conn:
        row = conn.execute("SELECT guardian_id FROM conversations WHERE chatwoot_conversation_id=?", (chatwoot_conversation_id,)).fetchone()
        return row["guardian_id"] if row else None
