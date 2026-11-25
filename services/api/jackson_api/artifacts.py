"""Artifact byte storage (phase 11, ADR-021).

Local filesystem store under ARTIFACT_DIR (content-addressed: <tenant>/<sha256>). The
`artifacts` table is the canonical record. An S3-compatible store with the same two
methods replaces this when hosting is chosen (the API and worker must share the store).
"""

import hashlib
import os
import uuid
from pathlib import Path


class LocalArtifactStore:
    def __init__(self, root: str | None = None):
        self.root = Path(root or os.environ.get("ARTIFACT_DIR", "./var/artifacts")).resolve()

    def put(self, tenant_id: uuid.UUID, data: bytes) -> tuple[str, str, int]:
        sha = hashlib.sha256(data).hexdigest()
        key = f"{tenant_id}/{sha}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return key, sha, len(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:  # no path traversal out of the store
            raise ValueError("invalid artifact key")
        return p
