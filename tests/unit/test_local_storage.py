"""LocalDocumentStorage: atomic writes and housekeeping."""

import os
from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.local_storage import LocalDocumentStorage


async def test_write_read_list_delete(tmp_path):
    storage = LocalDocumentStorage(tmp_path)
    await storage.write("trip/doc.html", b"hello")
    assert await storage.read("trip/doc.html") == b"hello"
    assert await storage.exists("trip/doc.html")
    assert await storage.list_keys() == ["trip/doc.html"]
    await storage.delete("trip/doc.html")
    assert await storage.read("trip/doc.html") is None
    assert not await storage.exists("trip/doc.html")


@pytest.mark.parametrize("fails", ["fsync", "replace"])
async def test_a_crash_mid_write_leaves_the_previous_file_intact(tmp_path, monkeypatch, fails):
    storage = LocalDocumentStorage(tmp_path)
    await storage.write("trip/doc.html", b"version one")

    def crash(*args, **kwargs):
        raise OSError("disk gone / process killed")

    monkeypatch.setattr(os, fails, crash)
    with pytest.raises(OSError):
        await storage.write("trip/doc.html", b"version two, longer")
    monkeypatch.undo()

    assert await storage.read("trip/doc.html") == b"version one"  # never a partial file
    assert await storage.list_keys() == ["trip/doc.html"]  # temp files are not documents
    removed = await storage.remove_temp_files(datetime.now(UTC) + timedelta(seconds=1))
    assert removed == 1


async def test_keys_cannot_escape_the_storage_root(tmp_path):
    storage = LocalDocumentStorage(tmp_path / "docs")
    with pytest.raises(ValueError):
        await storage.write("../outside.html", b"x")
