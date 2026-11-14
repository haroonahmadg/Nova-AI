import os
import sqlite3
import pytest
from database.db import (
    get_connection,
    init_db,
    save_message,
    get_messages,
    save_memory,
    get_memories,
    delete_thread,
    update_thread_title,
    search_threads,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    test_db_path = str(tmp_path / "test_assistant.db")
    test_db_dir = str(tmp_path)

    monkeypatch.setattr("database.db.DB_PATH", test_db_path)
    monkeypatch.setattr("database.db.DB_DIR", test_db_dir)

    init_db()
    yield
    if os.path.exists(test_db_path):
        os.remove(test_db_path)


def test_wal_mode_enabled():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode;")
    mode = cursor.fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_closing_connection_preserves_wal_sidecars_used_by_other_connections(
    monkeypatch,
):
    import database.db as database_module

    writer = get_connection()
    reader = get_connection()
    writer.execute("CREATE TABLE wal_probe (value INTEGER)")
    writer.commit()

    reader.execute("BEGIN")
    reader.execute("SELECT * FROM wal_probe").fetchall()
    writer.execute("INSERT INTO wal_probe (value) VALUES (1)")
    writer.commit()

    removed_sidecars = []
    original_remove = os.remove

    def track_sidecar_removal(path):
        if str(path).endswith(("-wal", "-shm")):
            removed_sidecars.append(str(path))
        original_remove(path)

    monkeypatch.setattr(database_module.os, "remove", track_sidecar_removal)
    writer.close()

    assert removed_sidecars == []
    assert reader.execute("SELECT * FROM wal_probe").fetchall() == []

    reader.commit()
    reader.close()

    with get_connection() as conn:
        values = conn.execute("SELECT value FROM wal_probe").fetchall()
    assert [row[0] for row in values] == [1]


def test_save_and_get_messages():
    thread_id = "test_thread_100"
    save_message(thread_id, "user", "Hello Assistant")
    save_message(thread_id, "assistant", "Hello! How can I help?")

    messages = get_messages(thread_id)
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hello Assistant"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "Hello! How can I help?"


def test_thread_helpers_use_configured_database():
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO threads (thread_id, title) VALUES (?, ?)",
            ("thread-for-search", "Original title"),
        )
        conn.execute(
            "INSERT INTO messages (thread_id, role, content) VALUES (?, ?, ?)",
            ("thread-for-search", "user", "Searchable message text"),
        )
        conn.commit()

    update_thread_title("thread-for-search", "Updated title")

    with get_connection() as conn:
        title = conn.execute(
            "SELECT title FROM threads WHERE thread_id = ?",
            ("thread-for-search",),
        ).fetchone()["title"]

    assert title == "Updated title"
    assert search_threads("Searchable") == [
        ("thread-for-search", "Updated title")
    ]


def test_save_and_get_memories():
    save_memory("user_name", "Alex")
    save_memory("favorite_language", "Python")

    memories = get_memories()
    memory_dict = dict(memories)

    assert memory_dict.get("user_name") == "Alex"
    assert memory_dict.get("favorite_language") == "Python"


@pytest.mark.parametrize("has_user_id_column", [False, True])
def test_initialize_migrates_legacy_memories_table(has_user_id_column):
    with get_connection() as conn:
        conn.execute("DROP TABLE memories")
        user_id_column = ", user_id TEXT" if has_user_id_column else ""
        conn.execute(
            f"""
            CREATE TABLE memories (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
                {user_id_column},
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        if has_user_id_column:
            conn.execute(
                "INSERT INTO memories (key, value, user_id) VALUES (?, ?, NULL)",
                ("legacy_fact", "kept"),
            )
        else:
            conn.execute(
                "INSERT INTO memories (key, value) VALUES (?, ?)",
                ("legacy_fact", "kept"),
            )
        conn.commit()

    init_db()
    init_db()

    assert save_memory("favorite_color", "blue", "user-1")
    assert save_memory("favorite_color", "green", "user-2")
    assert dict(get_memories("user-1"))["favorite_color"] == "blue"
    assert dict(get_memories("user-2"))["favorite_color"] == "green"

    with get_connection() as conn:
        memory_columns = conn.execute("PRAGMA table_info(memories)").fetchall()
        primary_key = [
            row[1]
            for row in sorted(
                (row for row in memory_columns if row[5] > 0),
                key=lambda row: row[5],
            )
        ]
        legacy_memory = conn.execute(
            "SELECT value, user_id FROM memories WHERE key = ?",
            ("legacy_fact",),
        ).fetchone()

    assert primary_key == ["key", "user_id"]
    assert tuple(legacy_memory) == ("kept", None)


def test_initialize_creates_user_scoped_events_table():
    with get_connection() as conn:
        columns = {
            row["name"]: row
            for row in conn.execute("PRAGMA table_info(events)").fetchall()
        }
        indexes = {
            row["name"]
            for row in conn.execute("PRAGMA index_list(events)").fetchall()
        }

    required_columns = {
        "id",
        "user_id",
        "title",
        "event_date",
        "event_time",
        "description",
    }
    assert required_columns <= set(columns)
    assert columns["user_id"]["notnull"] == 1
    assert "idx_events_user_date" in indexes


def test_initialize_migrates_legacy_events_without_assigning_an_owner():
    with get_connection() as conn:
        conn.execute("DROP TABLE events")
        conn.execute(
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                event_time TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO events (title, event_date, event_time, description)
            VALUES (?, ?, ?, ?)
            """,
            ("Legacy event", "2026-11-14", "", "Unassigned"),
        )
        conn.commit()

    init_db()

    with get_connection() as conn:
        event = conn.execute(
            "SELECT title, user_id FROM events WHERE id = 1"
        ).fetchone()

    assert tuple(event) == ("Legacy event", None)


def test_delete_thread_cascades():
    thread_id = "delete_me_thread"
    save_message(thread_id, "user", "Message to delete")
    delete_thread(thread_id)

    messages = get_messages(thread_id)
    assert len(messages) == 0