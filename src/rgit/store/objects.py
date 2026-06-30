import hashlib
import json
from pathlib import Path
from typing import Any


class ObjectStore:
    """Immutable sha256-addressed blob store under a directory."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / digest[2:]

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        p = self._path(digest)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return digest

    def get(self, digest: str) -> bytes:
        return self._path(digest).read_bytes()

    def put_json(self, obj: Any) -> str:
        return self.put(json.dumps(obj, sort_keys=True).encode())

    def get_json(self, digest: str) -> Any:
        return json.loads(self.get(digest))
