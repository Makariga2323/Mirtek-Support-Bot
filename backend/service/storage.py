import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .models import FailReason, MessageStatus

TICKET_QUEUED = "queued"

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
    def get_session(self, session_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] < time.time():
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                return None
        return dict(row)
    def delete_session(self, session_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cur.rowcount == 1
    def list_messages(self, session_id: str) -> bool:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT m.id, m.status, t.id AS ticket_id
                FROM messages m
                LEFT JOIN tickets t ON t.message_id = m.id
                WHERE m.session_id = ?
                ORDER BY m.id""",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    def create_ticket(self, session_id: str, message_id: int, idempotency_key:str | None) -> tuple[str, dict|None]:
        with self._connect() as conn:
            if idempotency_key:
                row = conn.execute(
                    "SELECT * FROM tickets WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if row is not None:
                    if row["session_id"] != session_id or row["message_id"] != message_id:
                        return "idempotency_conflict", None
                    return "existing", dict(row)
            owner = conn.execute(
                "SELECT 1 FROM messages WHERE id = ? AND session_id = ?",
                (message_id, session_id),
            ).fetchone()
            if owner is None:
                return "message_not_found", None

            cur = conn.execute(
                """INSERT INTO tickets (session_id, message_id, idempotency_key, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING""",
                (session_id, message_id, idempotency_key, TICKET_QUEUED, time.time()),
            )
            if cur.rowcount == 1:
                row = conn.execute(
                    "SELECT * FROM tickets WHERE id = ?", (cur.lastrowid,)
                ).fetchone()
                return "created", dict(row)
            
            row = conn.execute(
                "SELECT * FROM tickets WHERE message_id = ?", (message_id,)
            ).fetchone()
            if row is None and idempotency_key:
                row = conn.execute(
                    "SELECT * FROM tickets WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
            return "existing", dict(row)
    def get_ticket(self, ticket_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
        return dict(row) if row else None
    def list_tickets(self, session_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tickets WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
        return [dict(r) for r in rows] 
    def get_message_by_id(self, message_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
        return dict(row) if row else None
    def purge_expired(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
            return cur.rowcount
    def recover_interrupted(self, older_than_s: float) -> int:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE messages
                SET status = ?, fail_reason = ?, updated_at = ?
                WHERE status = ? AND updated_at <= ?""",
                (MessageStatus.FAILED.value, FailReason.INTERRUPTED.value, now, MessageStatus.PROCESSING.value, now - older_than_s)
            )
            return cur.rowcount