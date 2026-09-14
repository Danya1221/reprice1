"""Atomic durable state and a process lock for one worker per volume."""
import copy
import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class StateStore:
    def __init__(self, path):
        self.path = Path(path)
        self.data = {}
        self._lock_file = None
        self.load()

    def load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Ожидался JSON-объект")
        except (OSError, ValueError) as exc:
            # Never silently forget message IDs and start posting duplicate prices.
            raise RuntimeError(f"Не удалось прочитать {self.path}; восстанови state.json из копии") from exc
        self.data = raw

    def acquire(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(str(self.path) + ".lock", "a+", encoding="utf-8")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("На этом диске уже запущен экземпляр reprice1") from exc
        self._lock_file = handle

    def close(self):
        if self._lock_file:
            self._lock_file.close()
            self._lock_file = None

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            # Persist the rename as well as the file contents on Linux/Railway.
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def get(self, key, default=None):
        return copy.deepcopy(self.data.get(key, default))

    def update(self, values):
        previous = copy.deepcopy(self.data)
        self.data.update(copy.deepcopy(values))
        try:
            self.save()
        except Exception:
            self.data = previous
            raise

    def set(self, key, value):
        self.update({key: value})
