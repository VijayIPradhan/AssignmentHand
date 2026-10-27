"""Object storage abstraction.

Keys are opaque, server-generated paths of the form ``users/<uid>/<area>/<id>/<name>``. Files are
never served directly; downloads go through authenticated API endpoints.
"""

import re
import shutil
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from app.config import get_settings

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]*$")


class StorageError(Exception):
    pass


def safe_filename(name: str, default: str = "file") -> str:
    """Reduce an untrusted filename to a harmless display name."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9._ \-]", "_", name).strip(" .")
    return (name or default)[:120]


def validate_key(key: str) -> str:
    if not _SAFE_KEY.match(key) or ".." in key.split("/") or "//" in key:
        raise StorageError("invalid storage key")
    return key


class Storage(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None: ...

    @abstractmethod
    def local_path(self, key: str) -> Path | None:
        """Filesystem path if the backend is local (lets FileResponse stream large files)."""


class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / validate_key(key)).resolve()
        if self.root not in p.parents:
            raise StorageError("invalid storage key")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.is_file():
            raise StorageError("object not found")
        return p.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.is_file():
            p.unlink()

    def delete_prefix(self, prefix: str) -> None:
        p = self._path(prefix.rstrip("/"))
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            p.unlink()

    def local_path(self, key: str) -> Path | None:
        return self._path(key)


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend == "local":
        return LocalStorage(settings.storage_root)
    raise RuntimeError(f"Unsupported STORAGE_BACKEND: {settings.storage_backend}")
