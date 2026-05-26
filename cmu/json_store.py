from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


_THREAD_LOCKS: dict[Path, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def read_json(path: Path, default: dict) -> dict:
    with locked_json_file(path, default):
        return read_json_unlocked(path)


def update_json(path: Path, default: dict, update):
    with locked_json_file(path, default):
        data = read_json_unlocked(path)
        result = update(data)
        write_json_unlocked(path, data)
        return result


@contextmanager
def locked_json_file(path: Path, default: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    thread_lock = thread_lock_for(lock_path)
    with thread_lock:
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0)
            lock_file.write(b"0")
            lock_file.flush()
            acquire_process_lock(lock_file)
            try:
                if not path.exists():
                    write_json_unlocked(path, default)
                yield
            finally:
                release_process_lock(lock_file)


def thread_lock_for(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(resolved)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[resolved] = lock
        return lock


def read_json_unlocked(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_unlocked(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid4().hex}.tmp")
    try:
        with temp_file.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
        temp_file.replace(path)
    finally:
        if temp_file.exists():
            temp_file.unlink()


if os.name == "nt":
    import msvcrt

    def acquire_process_lock(lock_file) -> None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)

    def release_process_lock(lock_file) -> None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def acquire_process_lock(lock_file) -> None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

    def release_process_lock(lock_file) -> None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
