"""Tiny thread-safe SQLite helper shared by the library/preset/export stores (one DB file)."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from typing import Any


class Db:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Commit on success, roll back on error, ALWAYS close (sqlite3's own `with` does not close)."""
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def script(self, sql: str) -> None:
        with self._lock, self._conn() as c:
            c.executescript(sql)

    def exec(self, sql: str, args: tuple = ()) -> int:
        with self._lock, self._conn() as c:
            return c.execute(sql, args).lastrowid or 0

    def all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock, self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        r = self.all(sql, args)
        return r[0] if r else None
