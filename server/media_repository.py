import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    creator     TEXT,
    source_url  TEXT    NOT NULL,
    duration    INTEGER,           -- seconds
    thumbnail   TEXT,              -- remote image URL
    file_path  TEXT    NOT NULL UNIQUE,  -- relative to the media directory
    file_size   INTEGER NOT NULL,  -- bytes
    mime_type   TEXT    NOT NULL,
    created_at  TEXT    NOT NULL   -- ISO 8601, UTC
)
"""


@dataclass
class Media:
    id: int
    title: str
    creator: str | None
    source_url: str
    duration: int | None
    thumbnail: str | None
    file_path: str
    file_size: int
    mime_type: str
    created_at: str


class MediaRepository:
    """Stores metadata about media files. The files themselves live on disk."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        with self._connect() as conn:
            conn.execute(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # A fresh connection per call: FastAPI runs sync routes on worker
        # threads, and sqlite3 connections shouldn't be shared across them.
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # commits on success, rolls back on error
                yield conn
        finally:
            conn.close()

    def add(
        self,
        *,
        title: str,
        creator: str | None,
        source_url: str,
        duration: int | None,
        thumbnail: str | None,
        file_path: str,
        file_size: int,
        mime_type: str,
    ) -> Media:
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        with self._connect() as conn:
            # Re-downloading overwrites the same file on disk, so update the
            # existing row (keeping its id and created_at) instead of adding
            # a duplicate that points at the same file.
            row = conn.execute(
                """
                INSERT INTO media (title, creator, source_url, duration, thumbnail,
                                   file_path, file_size, mime_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (file_path) DO UPDATE SET
                    title      = excluded.title,
                    creator    = excluded.creator,
                    source_url = excluded.source_url,
                    duration   = excluded.duration,
                    thumbnail  = excluded.thumbnail,
                    file_size  = excluded.file_size,
                    mime_type  = excluded.mime_type
                RETURNING *
                """,
                (title, creator, source_url, duration, thumbnail,
                 file_path, file_size, mime_type, created_at),
            ).fetchone()

        return Media(**row)

    def get(self, media_id: int) -> Media | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM media WHERE id = ?", (media_id,)
            ).fetchone()
        return Media(**row) if row else None

    def list(self) -> list[Media]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM media ORDER BY created_at DESC"
            ).fetchall()
        return [Media(**row) for row in rows]
