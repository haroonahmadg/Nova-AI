import os
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

# Configure logging for tracking database errors
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("database")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_DIR = str(Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data"))).resolve())
DB_PATH = os.path.join(DB_DIR, "assistant.db")
VALID_ROLES = {"user", "assistant", "system", "tool"}


class SQLiteConnection:
    """Small compatibility wrapper around a SQLite connection."""

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def __getattr__(self, name: str):
        return getattr(self._connection, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self) -> None:
        self._connection.close()

    def cursor(self):
        return self._connection.cursor()

    def execute(self, *args, **kwargs):
        return self._connection.execute(*args, **kwargs)

    def commit(self):
        self._connection.commit()


def get_connection() -> SQLiteConnection:
    """Creates database folder if needed and returns a WAL-optimized thread-safe connection."""
    try:
        os.makedirs(DB_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row

        # Performance pragmas and foreign key support
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")

        return SQLiteConnection(conn)
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        raise RuntimeError(f"Database connection error: {e}")


# Unassigned legacy rows stay NULL until the explicit data migration runs.
_MEMORIES_CREATE_SQL = """
    CREATE TABLE memories (
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        user_id TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (key, user_id)
    )
"""

_EVENTS_CREATE_SQL = """
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        event_date TEXT NOT NULL,
        event_time TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT ''
    )
"""


def _ensure_memories_table(connection: SQLiteConnection) -> None:
    """Create or migrate the memories table while preserving existing rows."""

    connection.commit()
    connection.execute("BEGIN IMMEDIATE")

    try:
        info = connection.execute("PRAGMA table_info(memories)").fetchall()

        if not info:
            connection.execute(_MEMORIES_CREATE_SQL)
            connection.commit()
            return

        columns = {row[1] for row in info}
        primary_key = [
            row[1]
            for row in sorted(
                (row for row in info if row[5] > 0),
                key=lambda row: row[5],
            )
        ]

        if primary_key == ["key", "user_id"] and {
            "key",
            "value",
            "user_id",
        }.issubset(columns):
            connection.commit()
            return

        if not {"key", "value"}.issubset(columns):
            raise RuntimeError(
                "The memories table is missing required key or value columns."
            )

        legacy_table = "memories_legacy_migration"
        legacy_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (legacy_table,),
        ).fetchone()
        if legacy_exists:
            raise RuntimeError(
                f"Cannot migrate memories: table '{legacy_table}' already exists."
            )

        connection.execute(f"ALTER TABLE memories RENAME TO {legacy_table}")
        connection.execute(_MEMORIES_CREATE_SQL)

        copied_columns = [
            column
            for column in ("key", "value", "user_id", "updated_at")
            if column in columns
        ]
        column_list = ", ".join(copied_columns)
        connection.execute(
            f"INSERT INTO memories ({column_list}) "
            f"SELECT {column_list} FROM {legacy_table}"
        )
        connection.execute(f"DROP TABLE {legacy_table}")
        connection.commit()

    except Exception:
        connection.rollback()
        raise


def _ensure_events_table(connection: SQLiteConnection) -> None:
    """Create user-scoped calendar storage and quarantine unowned legacy rows."""

    connection.commit()
    connection.execute("BEGIN IMMEDIATE")

    try:
        info = connection.execute("PRAGMA table_info(events)").fetchall()
        if not info:
            connection.execute(_EVENTS_CREATE_SQL)
        else:
            columns = {row[1] for row in info}
            required_columns = {
                "id",
                "title",
                "event_date",
                "event_time",
                "description",
            }
            if not required_columns.issubset(columns):
                raise RuntimeError(
                    "The events table is missing required calendar columns."
                )
            if "user_id" not in columns:
                # Existing rows have no reliable owner. Keep them intact but
                # inaccessible to signed-in users rather than exposing them.
                connection.execute("ALTER TABLE events ADD COLUMN user_id TEXT")

        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_user_date "
            "ON events (user_id, event_date, event_time)"
        )
        connection.commit()

    except Exception:
        connection.rollback()
        raise


def initialize_database() -> None:
    """Sets up database tables with indexes for fast searching."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            
            # Threads table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS threads (
                    thread_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Chat messages table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (thread_id) REFERENCES threads (thread_id) ON DELETE CASCADE
                )
            """)
            
            _ensure_memories_table(conn)
            _ensure_events_table(conn)
            
            # Index for fast lookup of chat messages by thread
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_thread_id 
                ON messages (thread_id)
            """)
            
            conn.commit()
            logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise


# Backward-compatible alias used by older callers/tests.
init_db = initialize_database

# ---------------------------------------------------------
# Validation Helpers
# ---------------------------------------------------------

def _clean_string(text: Optional[str]) -> str:
    """Trims whitespace and checks for valid string input."""
    if text is None:
        return ""
    return str(text).strip()

# ---------------------------------------------------------
# Thread Operations
# ---------------------------------------------------------

def save_thread(thread_id: str, title: str) -> bool:
    """Creates a new chat thread or updates an existing one."""
    clean_id = _clean_string(thread_id)
    clean_title = _clean_string(title)
    
    if not clean_id or not clean_title:
        logger.warning("Failed to save thread: thread_id or title is empty.")
        return False
        
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO threads (thread_id, title, updated_at) 
                VALUES (?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    title = excluded.title,
                    updated_at = excluded.updated_at
                """,
                (clean_id, clean_title, datetime.now().isoformat(sep=" "))
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error saving thread {clean_id}: {e}")
        return False

def get_all_threads() -> List[Tuple[str, str]]:
    """Retrieves all chat threads ordered by most recent update."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT thread_id, title FROM threads ORDER BY updated_at DESC"
            )
            rows = cursor.fetchall()
            return [(row["thread_id"], row["title"]) for row in rows]
    except Exception as e:
        logger.error(f"Error retrieving threads: {e}")
        return []

def delete_thread(thread_id: str) -> bool:
    """Deletes a thread and its messages from storage."""
    clean_id = _clean_string(thread_id)
    if not clean_id:
        return False
        
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages WHERE thread_id = ?", (clean_id,))
            cursor.execute("DELETE FROM threads WHERE thread_id = ?", (clean_id,))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error deleting thread {clean_id}: {e}")
        return False

# ---------------------------------------------------------
# Message Operations
# ---------------------------------------------------------

def save_message(thread_id: str, role: str, content: str) -> bool:
    """Stores a chat message after validating role and text content."""
    clean_id = _clean_string(thread_id)
    clean_role = _clean_string(role).lower()
    clean_content = _clean_string(content)
    
    if not clean_id or not clean_content:
        logger.warning("Message not saved: missing thread_id or message content.")
        return False
        
    if clean_role not in VALID_ROLES:
        logger.warning(f"Message not saved: invalid role '{clean_role}'.")
        return False

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            
            # Make sure thread exists before saving message
            cursor.execute("SELECT 1 FROM threads WHERE thread_id = ?", (clean_id,))
            if not cursor.fetchone():
                default_title = clean_content[:30] + "..." if len(clean_content) > 30 else clean_content
                cursor.execute(
                    "INSERT INTO threads (thread_id, title) VALUES (?, ?)",
                    (clean_id, default_title or "New Chat")
                )
            
            cursor.execute(
                "INSERT INTO messages (thread_id, role, content) VALUES (?, ?, ?)",
                (clean_id, clean_role, clean_content)
            )
            
            # Update thread timestamp
            cursor.execute(
                "UPDATE threads SET updated_at = ? WHERE thread_id = ?",
                (datetime.now().isoformat(sep=" "), clean_id)
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error saving message for thread {clean_id}: {e}")
        return False

def get_messages(thread_id: str) -> List[Dict[str, str]]:
    """Loads all chat messages for a specific thread in order."""
    clean_id = _clean_string(thread_id)
    if not clean_id:
        return []
        
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT role, content FROM messages WHERE thread_id = ? ORDER BY id ASC",
                (clean_id,)
            )
            rows = cursor.fetchall()
            return [{"role": row["role"], "content": row["content"]} for row in rows]
    except Exception as e:
        logger.error(f"Error reading messages for thread {clean_id}: {e}")
        return []

# ---------------------------------------------------------
# Long-Term Memory Operations
# ---------------------------------------------------------

def save_memory(key: str, value: str, user_id: Optional[str] = None) -> bool:
    """Stores or updates a key-value fact about a specific user.

    Older code/tests call this without a user_id; we treat that as the
    default/legacy user bucket to preserve compatibility while still
    supporting the newer per-user API.
    """
    clean_key = _clean_string(key).lower()
    clean_val = _clean_string(value)
    clean_user_id = _clean_string(user_id) if user_id is not None else "default"

    if not clean_key or not clean_val:
        logger.warning("Memory not saved: key or value is blank.")
        return False

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO memories (key, value, user_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key, user_id) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_key,
                    clean_val,
                    clean_user_id,
                    datetime.now().isoformat(sep=" "),
                )
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error saving memory key '{clean_key}' for user {clean_user_id}: {e}")
        return False

def get_memories(user_id: Optional[str] = None) -> List[Tuple[str, str]]:
    """Fetch stored facts.

    If user_id is omitted, return the legacy/global memory set across all
    users; otherwise only return entries for that specific user.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            if user_id is not None:
                clean_user_id = _clean_string(user_id)
                if not clean_user_id:
                    return []
                cursor.execute(
                    "SELECT key, value FROM memories WHERE user_id = ? ORDER BY updated_at DESC",
                    (clean_user_id,),
                )
            else:
                cursor.execute(
                    "SELECT key, value FROM memories ORDER BY updated_at DESC"
                )
            rows = cursor.fetchall()
            return [(row["key"], row["value"]) for row in rows]
    except Exception as e:
        logger.error(f"Error fetching memories for user {user_id}: {e}")
        return []

def delete_memory(key: str, user_id: str) -> bool:
    """Removes a specific memory fact by its key, scoped to a user."""
    clean_key = _clean_string(key).lower()
    clean_user_id = _clean_string(user_id)

    if not clean_key or not clean_user_id:
        return False

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM memories WHERE key = ? AND user_id = ?",
                (clean_key, clean_user_id)
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error deleting memory key '{clean_key}' for user {clean_user_id}: {e}")
        return False

def update_thread_title(thread_id: str, new_title: str):
    """Update the title of a specific chat thread in the database."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE threads SET title = ? WHERE thread_id = ?",
            (new_title, thread_id),
        )
        conn.commit()

def search_threads(query: str):
    """Search threads by matching title or message content."""
    search_pattern = f"%{query.strip()}%"

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT t.thread_id, t.title
            FROM threads t
            LEFT JOIN messages m ON t.thread_id = m.thread_id
            WHERE t.title LIKE ? OR m.content LIKE ?
            ORDER BY t.created_at DESC
            """,
            (search_pattern, search_pattern),
        ).fetchall()

    return [tuple(row) for row in rows]
