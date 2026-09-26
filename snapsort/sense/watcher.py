"""Notice files. Registration is idempotent: the same bytes at the same path are only processed once.

Tools update `files.current_path` *before* moving a file, so the delete/move events our own moves
cause never match a row and are ignored.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .. import db, events, taskq
from ..config import Settings
from .extract import should_ignore
from .fingerprint import sha256_file, wait_until_stable

log = logging.getLogger(__name__)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def register_file(conn, settings: Settings, path: Path) -> int | None:
    path = path.resolve()
    if not path.is_file() or should_ignore(path):
        return None
    if not any(_inside(path, d) for d in settings.watch_dirs):
        return None
    sha, size, t = sha256_file(path), path.stat().st_size, db.now()
    row = conn.execute("SELECT id, sha256 FROM files WHERE current_path=?", (str(path),)).fetchone()
    if row and row["sha256"] == sha:
        return None
    if row:
        conn.execute("UPDATE files SET sha256=?, size=?, status='queued', updated_at=? WHERE id=?", (sha, size, t, row["id"]))
        file_id, message = row["id"], f"{path.name} changed on disk; reading it again"
    else:
        file_id = conn.execute(
            "INSERT INTO files(original_path, current_path, sha256, size, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (str(path), str(path), sha, size, t, t),
        ).lastrowid
        message = f"Noticed {path.name}"
    taskq.enqueue(conn, file_id)
    events.emit(conn, "sense", message, file_id=file_id)
    return file_id


def mark_missing(conn, path: Path) -> None:
    row = conn.execute("SELECT id FROM files WHERE current_path=?", (str(path.resolve()),)).fetchone()
    if row is None:
        return
    conn.execute("UPDATE files SET status='missing', updated_at=? WHERE id=?", (db.now(), row["id"]))
    conn.execute("UPDATE tasks SET state='cancelled' WHERE file_id=? AND state='queued'", (row["id"],))
    events.emit(conn, "sense", f"{path.name} was removed before I finished with it", file_id=row["id"], level="warn")


def initial_scan(conn, settings: Settings) -> int:
    new = 0
    for d in settings.watch_dirs:
        for p in sorted(d.rglob("*")):
            if p.is_file() and register_file(conn, settings, p):
                new += 1
    return new


class _Handler(FileSystemEventHandler):
    def __init__(self, settings: Settings, conn):
        super().__init__()
        self.settings, self.conn = settings, conn
        self.lock = threading.Lock()

    def _later(self, path_str: str) -> None:
        threading.Thread(target=self._add, args=(Path(path_str),), daemon=True).start()

    def _add(self, path: Path) -> None:
        if should_ignore(path) or not wait_until_stable(path):
            return
        with self.lock:
            try:
                register_file(self.conn, self.settings, path)
            except OSError as e:
                log.warning("could not register %s: %s", path, e)

    def on_created(self, event):
        if not event.is_directory:
            self._later(event.src_path)

    on_modified = on_created

    def on_moved(self, event):
        if event.is_directory:
            return
        with self.lock:
            mark_missing(self.conn, Path(event.src_path))
        self._later(event.dest_path)

    def on_deleted(self, event):
        if not event.is_directory:
            with self.lock:
                mark_missing(self.conn, Path(event.src_path))


def start_watcher(settings: Settings) -> Observer:
    handler = _Handler(settings, db.connect(settings.db_path))
    observer = Observer()
    for d in settings.watch_dirs:
        observer.schedule(handler, str(d), recursive=True)
    observer.start()
    return observer
