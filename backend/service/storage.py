import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .models import FailReason, MessageStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    token_hash  TEXT NOT NULL,
    expires_at  REAL NOT NULL 
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS messages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    client_message_id   TEXT,
    text                TEXT NOT NULL,
    status              TEXT NOT NULL,
    answer              TEXT NOT NULL DEFAULT '',
    fail_reason         TEXT,
    latency_ms          INTEGER,
    attempts            INTEGER NOT NULL DEFAULT 1,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    UNIQUE (session_id, client_message_id)
);

CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    message_id      INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
    idempotency_key TEXT,
    status          TEXT NOT NULL,
    created_at      REAL NOT NULL
);
"""


class Storage:
    def __init__(self, db_path: str):
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            
    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_session(self, session_id: str, token_hash: str, expires_at: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, token_hash, expires_at) VALUES (?, ?, ?)",
                (session_id, token_hash, expires_at),
            )
    def insert_message(self, session_id: str, client_message_id: str | None, text: str) -> int |None:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO messages
                        (session_id, client_message_id, text, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, client_message_id) DO NOTHING""",
                (session_id, client_message_id, text,
                 MessageStatus.PROCESSING.value, now, now),
            )
            if cur.rowcount == 1:
                return cur.lastrowid
            return None
        
    def get_message(self, session_id: str, client_message_id:str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? AND client_message_id=?",
                (session_id, client_message_id),
            ).fetchone()
        return dict(row) if row else None
    def finish_message(self, message_id: int, status: MessageStatus, answer: str = "",
                       fail_reason: FailReason | None = None,
                       latency_ms: int | None = None) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE messages
                SET status = ?, answer = ?, fail_reason = ?, latency_ms = ?, updated_at = ?
                WHERE id = ? AND status = ?""",
                (status.value, answer,
                 fail_reason.value if fail_reason else None,
                 latency_ms, time.time(),
                 message_id, MessageStatus.PROCESSING.value),
            )
            return cur.rowcount == 1
    def retry_failed(self, message_id:int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE messages
                SET status = ?, fail_reason = NULL, attempts = attempts + 1, updated_at = ?
                WHERE id = ? AND status = ?""",
                (MessageStatus.PROCESSING.value, time.time(),
                 message_id, MessageStatus.FAILED.value),
            )
            return cur.rowcount == 1
    