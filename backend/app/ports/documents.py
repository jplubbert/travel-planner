from datetime import datetime
from typing import Any, Protocol


class DocumentStorage(Protocol):
    """Durable file storage for generated documents. Keys are relative paths."""

    async def write(self, key: str, content: bytes) -> None:
        """Stores ``content`` atomically: readers see the old file or the complete new one,
        never a partial write, and the data is durable when this returns."""
        ...

    async def read(self, key: str) -> bytes | None:
        """The stored bytes, or None if there is no file."""
        ...

    async def exists(self, key: str) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def list_keys(self) -> list[str]: ...

    async def remove_temp_files(self, older_than: datetime) -> int:
        """Removes leftovers of interrupted writes; returns how many."""
        ...


class DocumentRenderer(Protocol):
    media_type: str

    def render(self, snapshot: dict[str, Any]) -> bytes:
        """Deterministic: the same snapshot always gives the same bytes."""
        ...
