"""Tests for the Uploads API module.

Run: pytest tests/test_openai_uploads.py -x -v
"""
import os
import sys
import types
import tempfile
import logging
import shutil

import pytest


# ── Mock modules before any imports ──────────────────────────────────────────
DATA_DIR = tempfile.mkdtemp(prefix="upload_test_")

config_mock = types.ModuleType("config")
config_mock.logger = logging.getLogger("test_uploads")
config_mock.logger.addHandler(logging.NullHandler())
config_mock.DATA_DIR = DATA_DIR
sys.modules["config"] = config_mock

# Mock openai.state
_openai_pkg = types.ModuleType("openai")
_openai_pkg.__path__ = []
sys.modules["openai"] = _openai_pkg

_openai_state = types.ModuleType("openai.state")
_openai_state.OpenAIDatabase = object
_openai_state.get_db = lambda: None
sys.modules["openai.state"] = _openai_state

# Now import uploads module
from jarvis.openai import uploads


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_uploads():
    """Reset in-memory state and temp dirs between tests."""
    uploads._uploads.clear()
    yield
    uploads._uploads.clear()
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)


# ── Tests ────────────────────────────────────────────────────────────────────

class TestUploadRegistry:
    """Tests for the in-memory upload registry."""

    def test_starts_empty(self):
        assert uploads._uploads == {}

    def test_dir_config(self):
        assert uploads.UPLOADS_DIR.startswith(tempfile.gettempdir())

    def test_create_upload_dict_structure(self):
        upload_id = "test-upload-1"
        upload_dir = os.path.join(uploads.UPLOADS_DIR, upload_id)
        os.makedirs(upload_dir, exist_ok=True)

        uploads._uploads[upload_id] = {
            "id": upload_id,
            "filename": "test.txt",
            "purpose": "assistants",
            "bytes": 500,
            "status": "pending",
            "parts": [],
            "dir": upload_dir,
        }

        entry = uploads._uploads[upload_id]
        assert entry["filename"] == "test.txt"
        assert entry["purpose"] == "assistants"
        assert entry["status"] == "pending"
        assert entry["parts"] == []
        assert os.path.isdir(entry["dir"])

        shutil.rmtree(upload_dir, ignore_errors=True)
        del uploads._uploads[upload_id]

    def test_upload_part_appended(self):
        upload_id = "test-upload-2"
        upload_dir = os.path.join(uploads.UPLOADS_DIR, upload_id)
        os.makedirs(upload_dir, exist_ok=True)

        uploads._uploads[upload_id] = {
            "id": upload_id,
            "filename": "data.bin",
            "purpose": "assistants",
            "bytes": 2048,
            "status": "pending",
            "parts": [],
            "dir": upload_dir,
        }

        import time
        part = {
            "id": "part-1",
            "object": "upload.part",
            "created_at": int(time.time()),
            "upload_id": upload_id,
            "size": 1024,
        }
        uploads._uploads[upload_id]["parts"].append(part)

        assert len(uploads._uploads[upload_id]["parts"]) == 1
        p = uploads._uploads[upload_id]["parts"][0]
        assert p["object"] == "upload.part"
        assert p["size"] == 1024
        assert p["id"] == "part-1"

        shutil.rmtree(upload_dir, ignore_errors=True)
        del uploads._uploads[upload_id]

    def test_cancel_removes_registry_entry(self):
        upload_id = "test-upload-3"
        upload_dir = os.path.join(uploads.UPLOADS_DIR, upload_id)
        os.makedirs(upload_dir, exist_ok=True)

        uploads._uploads[upload_id] = {
            "id": upload_id,
            "filename": "cancel.txt",
            "purpose": "assistants",
            "bytes": 100,
            "status": "pending",
            "parts": [],
            "dir": upload_dir,
        }

        assert upload_id in uploads._uploads
        assert os.path.isdir(upload_dir)

        # Simulate cancel
        shutil.rmtree(upload_dir, ignore_errors=True)
        del uploads._uploads[upload_id]

        assert upload_id not in uploads._uploads
        assert not os.path.exists(upload_dir)

    def test_complete_multiple_parts(self):
        upload_id = "test-upload-4"
        upload_dir = os.path.join(uploads.UPLOADS_DIR, upload_id)
        os.makedirs(upload_dir, exist_ok=True)

        part1_id = "part-a"
        part2_id = "part-b"
        with open(os.path.join(upload_dir, part1_id), "wb") as f:
            f.write(b"Hello, ")
        with open(os.path.join(upload_dir, part2_id), "wb") as f:
            f.write(b"World!")

        import time
        now = int(time.time())
        uploads._uploads[upload_id] = {
            "id": upload_id,
            "filename": "merged.txt",
            "purpose": "assistants",
            "bytes": 13,
            "status": "pending",
            "parts": [
                {"id": part1_id, "size": 7, "created_at": now, "upload_id": upload_id},
                {"id": part2_id, "size": 6, "created_at": now + 1, "upload_id": upload_id},
            ],
            "dir": upload_dir,
        }

        # Simulate assembly
        file_id = "file-merged-1"
        file_dir = os.path.join(uploads.FILES_DIR, file_id)
        os.makedirs(file_dir, exist_ok=True)
        dest_path = os.path.join(file_dir, "merged.txt")

        parts = sorted(uploads._uploads[upload_id]["parts"], key=lambda p: p["created_at"])
        total = 0
        with open(dest_path, "wb") as dest:
            for part in parts:
                p_path = os.path.join(upload_dir, part["id"])
                if os.path.exists(p_path):
                    with open(p_path, "rb") as src:
                        chunk = src.read()
                        dest.write(chunk)
                        total += len(chunk)

        assert total == 13
        with open(dest_path) as f:
            assert f.read() == "Hello, World!"

        shutil.rmtree(upload_dir, ignore_errors=True)
        shutil.rmtree(file_dir, ignore_errors=True)
        del uploads._uploads[upload_id]

    def test_get_nonexistent_returns_none(self):
        assert uploads._uploads.get("i-dont-exist") is None
