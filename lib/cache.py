import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class ScanCache:
    """
    SQLite-backed cache of files already verified as Sonos-compliant.
    Keyed on (path, mtime, size) — any change to the file invalidates the entry.
    """

    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_cache (
                path      TEXT    NOT NULL PRIMARY KEY,
                mtime     REAL    NOT NULL,
                size      INTEGER NOT NULL,
                cached_at TEXT    NOT NULL
            )
            """
        )
        self._conn.commit()

    def is_clean(self, path: str, mtime: float, size: int) -> bool:
        """Return True if this exact version of the file is cached as compliant."""
        row = self._conn.execute(
            "SELECT 1 FROM file_cache WHERE path = ? AND mtime = ? AND size = ?",
            (path, mtime, size),
        ).fetchone()
        return row is not None

    def mark_clean(self, path: str, mtime: float, size: int) -> None:
        """Record that this file is within spec and does not need conversion."""
        self._conn.execute(
            """
            INSERT INTO file_cache (path, mtime, size, cached_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE
              SET mtime     = excluded.mtime,
                  size      = excluded.size,
                  cached_at = excluded.cached_at
            """,
            (path, mtime, size, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
