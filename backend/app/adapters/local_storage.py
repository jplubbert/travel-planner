"""DocumentStorage on a local directory (a Docker volume in Compose).

Writes go to a temporary file in the same directory, are flushed and fsynced, then
renamed over the final name (atomic on POSIX) and the directory is fsynced, so a crash
leaves either the previous file or the complete new one, plus at most a temp file that
the sweep removes.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

TEMP_MARKER = ".tmp-"


class LocalDocumentStorage:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError(f"invalid storage key {key!r}")
        return path

    async def write(self, key: str, content: bytes) -> None:
        await asyncio.to_thread(self._write, self._path(key), content)

    @staticmethod
    def _write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}{TEMP_MARKER}{uuid.uuid4().hex}")
        with open(tmp, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    async def read(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            return None

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)

    async def list_keys(self) -> list[str]:
        return await asyncio.to_thread(self._list, False)

    def _list(self, temp: bool) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")
                      if p.is_file() and (TEMP_MARKER in p.name) == temp)

    async def remove_temp_files(self, older_than: datetime) -> int:
        def remove() -> int:
            removed = 0
            for key in self._list(True):
                path = self.root / key
                modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
                if modified < older_than:
                    path.unlink(missing_ok=True)
                    removed += 1
            return removed
        return await asyncio.to_thread(remove)
