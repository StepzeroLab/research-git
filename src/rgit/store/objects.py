import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import uuid4


_T = TypeVar("_T")


class ObjectStore:
    """Immutable sha256-addressed blob store under a directory."""

    _DIGEST_LENGTH = hashlib.sha256().digest_size * 2
    _DIGEST_CHARS = frozenset("0123456789abcdef")
    _ACCESS_RETRY_DELAYS = (0.001, 0.002, 0.004, 0.008, 0.016, 0.032)

    def __init__(self, root: Path, create: bool = True):
        self.root = Path(root)
        if create:
            self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, digest: str) -> Path:
        """On-disk location of a digest — the single source of truth for the
        store layout, so read-only consumers (doctor) can't drift from it."""
        if not self.is_valid_digest(digest):
            raise ValueError(f"invalid sha256 digest: {digest!r}")
        return self.root / digest[:2] / digest[2:]

    def _path(self, digest: str) -> Path:
        return self.path_for(digest)

    @classmethod
    def is_valid_digest(cls, digest: object) -> bool:
        """Return whether *digest* is a canonical lowercase sha256 hex value."""
        return (
            isinstance(digest, str)
            and len(digest) == cls._DIGEST_LENGTH
            and all(char in cls._DIGEST_CHARS for char in digest)
        )

    def verify(self, digest: str) -> bool:
        """Return whether the stored bytes hash to *digest*.

        Missing objects still raise ``FileNotFoundError`` so callers such as
        doctor can distinguish an absent object from a corrupt one.
        """
        path = self.path_for(digest)
        with path.open("rb") as blob:
            actual = hashlib.file_digest(blob, "sha256").hexdigest()
        return actual == digest

    @classmethod
    def _retry_permission_error(cls, operation: Callable[[], _T]) -> _T:
        """Retry brief sharing violations without hiding persistent errors."""
        for delay in cls._ACCESS_RETRY_DELAYS:
            try:
                return operation()
            except PermissionError:
                time.sleep(delay)
        return operation()

    @classmethod
    def _write_atomic(cls, path: Path, data: bytes) -> None:
        """Durably write *data* before atomically publishing it at *path*."""
        # Path.open("x") preserves the mode/umask behavior of the previous
        # direct write while guaranteeing that a stale temp file is not reused.
        temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temp_path.open("xb") as blob:
                blob.write(data)
                blob.flush()
                os.fsync(blob.fileno())
            cls._retry_permission_error(lambda: os.replace(temp_path, path))
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        p = self._path(digest)
        try:
            if self._retry_permission_error(lambda: self.verify(digest)):
                return digest
        except FileNotFoundError:
            pass

        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._write_atomic(p, data)
        except OSError:
            # Another writer may have won the race. This is success only when
            # it published the exact object we were trying to store.
            try:
                if self._retry_permission_error(lambda: self.verify(digest)):
                    return digest
            except OSError:
                pass
            raise
        return digest

    def get(self, digest: str) -> bytes:
        return self._path(digest).read_bytes()

    def put_json(self, obj: Any) -> str:
        return self.put(json.dumps(obj, sort_keys=True).encode())

    def get_json(self, digest: str) -> Any:
        return json.loads(self.get(digest))
