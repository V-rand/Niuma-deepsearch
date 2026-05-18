"""
SQLite-backed state store for sessions, messages, reminders, interventions and artifacts.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SQLiteStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._fts_enabled = False
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    work_dir TEXT NOT NULL,
                    todo_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    parent_session_id TEXT,
                    compression_version INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(parent_session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id);

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    reminder_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    fire_at TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    fired_at TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_reminders_pending ON reminders(status, fire_at);

                CREATE TABLE IF NOT EXISTS interventions (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    applied_at TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_interventions_pending ON interventions(session_id, status, created_at);

                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(session_id, path),
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id, updated_at);

                CREATE TABLE IF NOT EXISTS artifact_chunks (
                    id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding_json TEXT,
                    score_hint REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(artifact_id) REFERENCES artifacts(id) ON DELETE CASCADE,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_chunks_session ON artifact_chunks(session_id, updated_at);
                """
            )
            try:
                self._conn.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                        content,
                        session_id UNINDEXED,
                        kind UNINDEXED
                    );
                    CREATE VIRTUAL TABLE IF NOT EXISTS artifact_chunks_fts USING fts5(
                        content,
                        session_id UNINDEXED,
                        path UNINDEXED
                    );
                    """
                )
                self._fts_enabled = True
            except sqlite3.OperationalError:
                self._fts_enabled = False
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(
        self,
        *,
        session_id: str,
        name: str,
        description: str,
        stage: str,
        work_dir: str,
        metadata: dict[str, Any] | None = None,
        parent_session_id: str | None = None,
        compression_version: int = 1,
    ) -> None:
        now = utcnow_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (
                    id, name, description, status, stage, work_dir,
                    todo_json, metadata_json, created_at, updated_at,
                    parent_session_id, compression_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, name, description, "active", stage, work_dir,
                    "[]", json.dumps(metadata or {}, ensure_ascii=False),
                    now, now, parent_session_id, compression_version,
                ),
            )
            self._conn.commit()

    def update_session(
        self,
        *,
        session_id: str,
        name: str,
        description: str,
        status: str,
        stage: str,
        work_dir: str,
        todo_list: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        parent_session_id: str | None = None,
        compression_version: int | None = None,
    ) -> None:
        # Three SQL variants because SQLite doesn't natively accept optional
        # columns in a single parameterised statement. Each path adds or omits
        # the fields that are actually being updated:
        #   - compression_version is set → compression fork (full update)
        #   - parent_session_id is set  → sub-agent/fork linking (omit version)
        #   - neither                  → routine status/metadata/todo flush
        with self._lock:
            if compression_version is not None:
                self._conn.execute(
                    """
                    UPDATE sessions
                    SET name=?, description=?, status=?, stage=?, work_dir=?,
                        todo_json=?, metadata_json=?, updated_at=?,
                        parent_session_id=?, compression_version=?
                    WHERE id=?
                    """,
                    (name, description, status, stage, work_dir,
                     json.dumps(todo_list, ensure_ascii=False),
                     json.dumps(metadata or {}, ensure_ascii=False),
                     utcnow_iso(), parent_session_id, compression_version, session_id),
                )
            elif parent_session_id is not None:
                self._conn.execute(
                    """
                    UPDATE sessions
                    SET name=?, description=?, status=?, stage=?, work_dir=?,
                        todo_json=?, metadata_json=?, updated_at=?, parent_session_id=?
                    WHERE id=?
                    """,
                    (name, description, status, stage, work_dir,
                     json.dumps(todo_list, ensure_ascii=False),
                     json.dumps(metadata or {}, ensure_ascii=False),
                     utcnow_iso(), parent_session_id, session_id),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE sessions
                    SET name=?, description=?, status=?, stage=?, work_dir=?,
                        todo_json=?, metadata_json=?, updated_at=?
                    WHERE id=?
                    """,
                    (name, description, status, stage, work_dir,
                     json.dumps(todo_list, ensure_ascii=False),
                     json.dumps(metadata or {}, ensure_ascii=False),
                     utcnow_iso(), session_id),
                )
            self._conn.commit()

    def get_session_row(self, session_id: str) -> sqlite3.Row | None:
        cur = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return cur.fetchone()

    def list_sessions(self) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC, created_at DESC"
        )
        return cur.fetchall()

    def list_sessions_by_status(self, status: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM sessions WHERE status = ? ORDER BY updated_at DESC",
            (status,),
        )
        return cur.fetchall()

    def list_session_versions(self, root_session_id: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ? OR parent_session_id = ? ORDER BY compression_version ASC",
            (root_session_id, root_session_id),
        )
        return cur.fetchall()

    def touch_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (utcnow_iso(), session_id),
            )
            self._conn.commit()

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            if self._fts_enabled:
                # FTS5 virtual tables do NOT participate in FK CASCADE, so orphaned
                # rows linger and cause UNIQUE constraint failures on new INSERTs.
                # Must explicitly clean FTS entries before deleting the session.
                self._conn.execute(
                    "DELETE FROM artifact_chunks_fts WHERE rowid IN (SELECT rowid FROM artifact_chunks WHERE session_id = ?)",
                    (session_id,),
                )
                self._conn.execute(
                    "DELETE FROM messages_fts WHERE rowid IN (SELECT rowid FROM messages WHERE session_id = ?)",
                    (session_id,),
                )
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def add_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        kind: str = "chat",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO messages (session_id, role, content, kind, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, role, content, kind,
                 json.dumps(metadata or {}, ensure_ascii=False), utcnow_iso()),
            )
            message_id = int(cur.lastrowid)
            if self._fts_enabled:
                self._conn.execute(
                    "INSERT INTO messages_fts(rowid, content, session_id, kind) VALUES (?, ?, ?, ?)",
                    (message_id, content, session_id, kind),
                )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (utcnow_iso(), session_id),
            )
            self._conn.commit()
        return message_id

    def delete_message(self, message_id: int) -> None:
        with self._lock:
            if self._fts_enabled:
                self._conn.execute("DELETE FROM messages_fts WHERE rowid = ?", (message_id,))
            self._conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            self._conn.commit()

    def list_messages(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        kinds: list[str] | None = None,
    ) -> list[sqlite3.Row]:
        query = "SELECT * FROM messages WHERE session_id = ?"
        params: list[Any] = [session_id]
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            query += f" AND kind IN ({placeholders})"
            params.extend(kinds)
        query += " ORDER BY id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, tuple(params)).fetchall()
        rows.reverse()
        return rows

    def search_messages(self, session_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if self._fts_enabled:
            try:
                cur = self._conn.execute(
                    """
                    SELECT m.id as rowid, m.content, m.kind, m.metadata_json, m.created_at
                    FROM messages_fts f
                    JOIN messages m ON m.id = f.rowid
                    WHERE f.session_id = ? AND messages_fts MATCH ?
                    LIMIT ?
                    """,
                    (session_id, query, limit),
                )
                rows = [self._decode_json_row(row) for row in cur.fetchall()]
                if rows:
                    return rows
            except sqlite3.OperationalError:
                pass
        cur = self._conn.execute(
            """
            SELECT id as rowid, content, kind, metadata_json, created_at
            FROM messages WHERE session_id = ? AND content LIKE ?
            ORDER BY id DESC LIMIT ?
            """,
            (session_id, f"%{query}%", limit),
        )
        return [self._decode_json_row(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Reminders
    # ------------------------------------------------------------------

    def create_reminder(
        self,
        *,
        session_id: str,
        reminder_type: str,
        title: str,
        message: str,
        fire_at: str,
        priority: int,
        metadata: dict[str, Any] | None = None,
        reminder_id: str | None = None,
    ) -> str:
        resolved_id = reminder_id or f"int_{str(uuid.uuid4())[:8]}"
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO reminders (
                    id, session_id, reminder_type, title, message, fire_at, priority,
                    status, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (resolved_id, session_id, reminder_type, title, message, fire_at,
                 priority, "pending", json.dumps(metadata or {}, ensure_ascii=False), utcnow_iso()),
            )
            self._conn.commit()
        return resolved_id

    def mark_reminder_fired(self, reminder_id: str, fired_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE reminders SET status = 'fired', fired_at = ? WHERE id = ?",
                (fired_at, reminder_id),
            )
            self._conn.commit()

    def list_reminders(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[sqlite3.Row]:
        query = "SELECT * FROM reminders WHERE 1=1"
        params: list[Any] = []
        if session_id is not None:
            query += " AND session_id = ?"
            params.append(session_id)
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY fire_at ASC"
        cur = self._conn.execute(query, tuple(params))
        return cur.fetchall()

    # ------------------------------------------------------------------
    # Interventions
    # ------------------------------------------------------------------

    def create_intervention(
        self,
        *,
        session_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        intervention_id = str(uuid.uuid4())[:12]
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO interventions (id, session_id, content, status, metadata_json, created_at, applied_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (intervention_id, session_id, content, "pending",
                 json.dumps(metadata or {}, ensure_ascii=False), utcnow_iso()),
            )
            self._conn.commit()
        return intervention_id

    def list_interventions(
        self,
        session_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[sqlite3.Row]:
        query = "SELECT * FROM interventions WHERE session_id = ?"
        params: list[Any] = [session_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cur = self._conn.execute(query, tuple(params))
        return cur.fetchall()

    def mark_interventions_applied(
        self,
        session_id: str,
        intervention_ids: list[str],
    ) -> None:
        if not intervention_ids:
            return
        now = utcnow_iso()
        with self._lock:
            self._conn.executemany(
                """
                UPDATE interventions SET status = 'applied', applied_at = ?
                WHERE session_id = ? AND id = ?
                """,
                [(now, session_id, iid) for iid in intervention_ids],
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def upsert_artifact(
        self,
        *,
        session_id: str,
        path: str,
        title: str,
        artifact_type: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        now = utcnow_iso()
        row = self._conn.execute(
            "SELECT id, created_at FROM artifacts WHERE session_id = ? AND path = ?",
            (session_id, path),
        ).fetchone()
        artifact_id = row["id"] if row else str(uuid.uuid4())[:12]
        created_at = row["created_at"] if row else now
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO artifacts (
                    id, session_id, path, title, artifact_type, summary,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, path) DO UPDATE SET
                    title = excluded.title,
                    artifact_type = excluded.artifact_type,
                    summary = excluded.summary,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (artifact_id, session_id, path, title, artifact_type, summary,
                 json.dumps(metadata or {}, ensure_ascii=False), created_at, now),
            )
            self._conn.commit()
        return artifact_id

    def replace_artifact_chunks(
        self,
        *,
        artifact_id: str,
        session_id: str,
        path: str,
        chunks: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            existing = self._conn.execute(
                "SELECT id, rowid FROM artifact_chunks WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchall()
            if self._fts_enabled:
                for row in existing:
                    self._conn.execute(
                        "DELETE FROM artifact_chunks_fts WHERE rowid = ?",
                        (row["rowid"],),
                    )
            self._conn.execute("DELETE FROM artifact_chunks WHERE artifact_id = ?", (artifact_id,))
            for index, chunk in enumerate(chunks):
                chunk_id = str(uuid.uuid4())[:12]
                cur = self._conn.execute(
                    """
                    INSERT INTO artifact_chunks (
                        id, artifact_id, session_id, path, chunk_index, content,
                        embedding_json, score_hint, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (chunk_id, artifact_id, session_id, path, index, chunk["content"],
                     json.dumps(chunk.get("embedding"), ensure_ascii=False)
                     if chunk.get("embedding") is not None else None,
                     float(chunk.get("score_hint", 0.0)), utcnow_iso()),
                )
                if self._fts_enabled:
                    self._conn.execute(
                        "INSERT INTO artifact_chunks_fts(rowid, content, session_id, path) VALUES (?, ?, ?, ?)",
                        (int(cur.lastrowid), chunk["content"], session_id, path),
                    )
            self._conn.commit()

    def list_artifacts(self, session_id: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM artifacts WHERE session_id = ? ORDER BY updated_at DESC",
            (session_id,),
        )
        return cur.fetchall()

    def list_artifacts_by_work_dir(self, work_dir: str) -> list[sqlite3.Row]:
        """Find all artifacts across all sessions sharing the same work_dir."""
        cur = self._conn.execute(
            "SELECT a.* FROM artifacts a JOIN sessions s ON a.session_id = s.id WHERE s.work_dir = ? ORDER BY a.updated_at DESC",
            (work_dir,),
        )
        return cur.fetchall()

    def get_artifact(self, session_id: str, path: str) -> sqlite3.Row | None:
        cur = self._conn.execute(
            "SELECT * FROM artifacts WHERE session_id = ? AND path = ?",
            (session_id, path),
        )
        return cur.fetchone()

    def get_artifact_by_work_dir(self, work_dir: str, path: str) -> sqlite3.Row | None:
        cur = self._conn.execute(
            """
            SELECT a.*
            FROM artifacts a
            JOIN sessions s ON a.session_id = s.id
            WHERE s.work_dir = ? AND a.path = ?
            ORDER BY a.updated_at DESC
            LIMIT 1
            """,
            (work_dir, path),
        )
        return cur.fetchone()

    def delete_artifact(self, session_id: str, path: str) -> None:
        with self._lock:
            artifact = self._conn.execute(
                "SELECT id FROM artifacts WHERE session_id = ? AND path = ?",
                (session_id, path),
            ).fetchone()
            if artifact is None:
                return
            existing = self._conn.execute(
                "SELECT rowid FROM artifact_chunks WHERE artifact_id = ?",
                (artifact["id"],),
            ).fetchall()
            if self._fts_enabled:
                for row in existing:
                    self._conn.execute(
                        "DELETE FROM artifact_chunks_fts WHERE rowid = ?",
                        (row["rowid"],),
                    )
            self._conn.execute("DELETE FROM artifact_chunks WHERE artifact_id = ?", (artifact["id"],))
            self._conn.execute("DELETE FROM artifacts WHERE session_id = ? AND path = ?", (session_id, path))
            self._conn.commit()

    def delete_artifacts_by_work_dir(self, work_dir: str, path: str) -> None:
        with self._lock:
            artifacts = self._conn.execute(
                """
                SELECT a.id
                FROM artifacts a
                JOIN sessions s ON a.session_id = s.id
                WHERE s.work_dir = ? AND a.path = ?
                """,
                (work_dir, path),
            ).fetchall()
            artifact_ids = [row["id"] for row in artifacts]
            if not artifact_ids:
                return
            placeholders = ",".join("?" for _ in artifact_ids)
            existing = self._conn.execute(
                f"SELECT rowid FROM artifact_chunks WHERE artifact_id IN ({placeholders})",
                tuple(artifact_ids),
            ).fetchall()
            if self._fts_enabled:
                for row in existing:
                    self._conn.execute(
                        "DELETE FROM artifact_chunks_fts WHERE rowid = ?",
                        (row["rowid"],),
                    )
            self._conn.execute(
                f"DELETE FROM artifact_chunks WHERE artifact_id IN ({placeholders})",
                tuple(artifact_ids),
            )
            self._conn.execute(
                f"DELETE FROM artifacts WHERE id IN ({placeholders})",
                tuple(artifact_ids),
            )
            self._conn.commit()

    def search_artifact_chunks(
        self,
        session_id: str,
        query: str,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        terms = self._query_terms(query)
        if terms:
            where_like = " AND ".join("c.content LIKE ?" for _ in terms)
            cur = self._conn.execute(
                f"""
                SELECT
                    c.rowid as rowid, c.content, c.path, c.chunk_index,
                    a.artifact_type, a.title, a.summary, a.metadata_json
                FROM artifact_chunks c
                JOIN artifacts a ON a.id = c.artifact_id
                WHERE c.session_id = ? AND {where_like}
                ORDER BY c.updated_at DESC LIMIT ?
                """,
                (session_id, *(f"%{term}%" for term in terms), limit),
            )
            rows.extend(self._decode_json_row(row) for row in cur.fetchall())
        if self._fts_enabled:
            try:
                cur = self._conn.execute(
                    """
                    SELECT
                        c.rowid as rowid, c.content, c.path, c.chunk_index,
                        a.artifact_type, a.title, a.summary, a.metadata_json
                    FROM artifact_chunks_fts f
                    JOIN artifact_chunks c ON c.rowid = f.rowid
                    JOIN artifacts a ON a.id = c.artifact_id
                    WHERE f.session_id = ? AND artifact_chunks_fts MATCH ?
                    LIMIT ?
                    """,
                    (session_id, query, limit),
                )
                rows.extend(self._decode_json_row(row) for row in cur.fetchall())
            except sqlite3.OperationalError:
                pass
        rows = self._dedupe_rows(rows)
        if rows:
            return rows[:limit]
        cur = self._conn.execute(
            """
            SELECT
                c.rowid as rowid, c.content, c.path, c.chunk_index,
                a.artifact_type, a.title, a.summary, a.metadata_json
            FROM artifact_chunks c
            JOIN artifacts a ON a.id = c.artifact_id
            WHERE c.session_id = ? AND c.content LIKE ?
            ORDER BY c.updated_at DESC LIMIT ?
            """,
            (session_id, f"%{query}%", limit),
        )
        return [self._decode_json_row(row) for row in cur.fetchall()]

    def search_artifact_chunks_by_work_dir(self, work_dir: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """FTS search across all sessions sharing the same work_dir."""
        rows: list[dict[str, Any]] = []
        terms = self._query_terms(query)
        if terms:
            where_like = " AND ".join("c.content LIKE ?" for _ in terms)
            cur = self._conn.execute(
                f"""
                SELECT
                    c.rowid as rowid, c.content, c.path, c.chunk_index,
                    a.artifact_type, a.title, a.summary, a.metadata_json
                FROM artifact_chunks c
                JOIN sessions s ON c.session_id = s.id
                JOIN artifacts a ON a.id = c.artifact_id
                WHERE s.work_dir = ? AND {where_like}
                ORDER BY c.updated_at DESC LIMIT ?
                """,
                (work_dir, *(f"%{term}%" for term in terms), limit),
            )
            rows.extend(self._decode_json_row(row) for row in cur.fetchall())
        if self._fts_enabled:
            try:
                cur = self._conn.execute(
                    """
                    SELECT
                        c.rowid as rowid, c.content, c.path, c.chunk_index,
                        a.artifact_type, a.title, a.summary, a.metadata_json
                    FROM artifact_chunks_fts f
                    JOIN artifact_chunks c ON c.rowid = f.rowid
                    JOIN sessions s ON c.session_id = s.id
                    JOIN artifacts a ON a.id = c.artifact_id
                    WHERE s.work_dir = ? AND artifact_chunks_fts MATCH ?
                    LIMIT ?
                    """,
                    (work_dir, query, limit),
                )
                rows.extend(self._decode_json_row(row) for row in cur.fetchall())
            except sqlite3.OperationalError:
                pass
        rows = self._dedupe_rows(rows)
        if rows:
            return rows[:limit]
        cur = self._conn.execute(
            """
            SELECT
                c.rowid as rowid, c.content, c.path, c.chunk_index,
                a.artifact_type, a.title, a.summary, a.metadata_json
            FROM artifact_chunks c
            JOIN sessions s ON c.session_id = s.id
            JOIN artifacts a ON a.id = c.artifact_id
            WHERE s.work_dir = ? AND c.content LIKE ?
            ORDER BY c.updated_at DESC LIMIT ?
            """,
            (work_dir, f"%{query}%", limit),
        )
        return [self._decode_json_row(row) for row in cur.fetchall()]

    def get_all_chunk_embeddings(self, session_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT c.content, c.path, c.chunk_index, c.embedding_json,
                   a.artifact_type, a.title, a.summary, a.metadata_json
            FROM artifact_chunks c
            JOIN artifacts a ON a.id = c.artifact_id
            WHERE c.session_id = ? AND c.embedding_json IS NOT NULL
            """,
            (session_id,),
        )
        return [self._decode_json_row(row) for row in cur.fetchall()]

    def get_all_chunk_embeddings_by_work_dir(self, work_dir: str) -> list[dict[str, Any]]:
        """All chunk embeddings for all sessions sharing the same work_dir."""
        cur = self._conn.execute(
            """
            SELECT c.content, c.path, c.chunk_index, c.embedding_json,
                   a.artifact_type, a.title, a.summary, a.metadata_json
            FROM artifact_chunks c
            JOIN artifacts a ON a.id = c.artifact_id
            JOIN sessions s ON c.session_id = s.id
            WHERE s.work_dir = ? AND c.embedding_json IS NOT NULL
            """,
            (work_dir,),
        )
        return [self._decode_json_row(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def row_to_json(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        for key, value in list(data.items()):
            if key.endswith("_json") and isinstance(value, str):
                data[key[:-5]] = json.loads(value) if value else None
                del data[key]
        return data

    @staticmethod
    def _decode_json_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key, value in list(data.items()):
            if key.endswith("_json") and isinstance(value, str):
                data[key[:-5]] = json.loads(value) if value else None
                del data[key]
        return data

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        value = str(query or "").strip()
        if not value:
            return []
        return [term for term in value.split() if term]

    @staticmethod
    def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[Any] = set()
        deduped: list[dict[str, Any]] = []
        for row in rows:
            key = row.get("rowid") or (row.get("path"), row.get("chunk_index"), row.get("content"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped
