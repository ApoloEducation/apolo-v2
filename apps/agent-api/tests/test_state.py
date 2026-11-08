import sqlite3

from app import state


def test_legacy_database_is_migrated_to_horizonte(tmp_path, monkeypatch):
    path = tmp_path / "agent.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE conversations (guardian_id TEXT PRIMARY KEY, chatwoot_conversation_id INTEGER, chatwoot_contact_id INTEGER,
      mode TEXT NOT NULL DEFAULT 'auto', updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, guardian_id TEXT NOT NULL, sender TEXT NOT NULL,
      content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    INSERT INTO conversations (guardian_id, chatwoot_conversation_id, chatwoot_contact_id, mode) VALUES ('g01', 7, 3, 'human');
    INSERT INTO messages (guardian_id, sender, content) VALUES ('g01', 'guardian', 'oi');
    """)
    conn.commit(); conn.close()
    monkeypatch.setattr(state, "PATH", path)
    state.initialize()
    state.initialize()  # idempotent
    assert state.conversation("horizonte", "g01")["mode"] == "human"
    assert state.history("horizonte", "g01")[0]["content"] == "oi"


def test_same_guardian_id_in_two_schools_is_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "PATH", tmp_path / "agent.sqlite3")
    state.initialize()
    state.save_conversation("horizonte", "g01", 1, 1, "human")
    state.save_conversation("aurora", "g01", 2, 2, "auto")
    state.save_message("aurora", "g01", "guardian", "olá")
    assert state.conversation("horizonte", "g01")["mode"] == "human"
    assert state.conversation("aurora", "g01")["mode"] == "auto"
    assert state.history("horizonte", "g01") == []
    assert state.conversation_by_chatwoot_id(2)["school_id"] == "aurora"
