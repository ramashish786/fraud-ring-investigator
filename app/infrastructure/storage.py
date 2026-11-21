"""Content-addressed local file storage for uploaded documents (D-031).

Files are stored as <root>/<sha[:2]>/<sha><ext> and written atomically (temp file +
rename). In compose this is a named volume shared by api (writes) and worker (reads).
Behind this small interface, it can later be swapped for S3/GCS without touching services.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path


class LocalFileStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, sha256: str, ext: str) -> Path:
        if not all(c in "0123456789abcdef" for c in sha256) or len(sha256) != 64:
            raise ValueError("invalid content hash")
        return self.root / sha256[:2] / f"{sha256}{ext}"

    def _write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    async def put(self, sha256: str, ext: str, data: bytes) -> str:
        path = self._path(sha256, ext)
        if not path.exists():
            await asyncio.to_thread(self._write, path, data)
        return str(path.relative_to(self.root))

    async def get(self, relative_path: str) -> bytes:
        path = (self.root / relative_path).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("path escapes storage root")
        return await asyncio.to_thread(path.read_bytes)
