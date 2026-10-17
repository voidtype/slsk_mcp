"""Tests for the SoulseekWrapper client."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slsk_mcp.slsk_client import (
    SoulseekWrapper,
    _parse_id,
    _file_extension,
    _extract_attrs,
    _parse_share_dirs,
)


# ── Unit helpers ─────────────────────────────────────────────────────────────


def test_parse_id_basic():
    user, path = _parse_id("alice:/Music/Song.flac")
    assert user == "alice"
    assert path == "/Music/Song.flac"


def test_parse_id_colon_in_path():
    user, path = _parse_id("bob:C:\\Music\\Track.mp3")
    assert user == "bob"
    assert path == "C:\\Music\\Track.mp3"


def test_file_extension():
    assert _file_extension("Song.flac") == "flac"
    assert _file_extension("archive.tar.gz") == "gz"
    assert _file_extension("noext") == ""


def test_extract_attrs_none():
    attrs = _extract_attrs(None)
    assert attrs["bitrate"] is None
    assert attrs["audio_quality"] is None


def test_extract_attrs_populated():
    class FakeAttr:
        def __init__(self, key, value):
            self.key = key
            self.value = value

    attrs = _extract_attrs([
        FakeAttr(0, 1411),  # audio_quality
        FakeAttr(1, 300),   # duration_sec
        FakeAttr(4, 44100), # sample_rate
        FakeAttr(5, 16),    # bit_depth
    ])
    assert attrs["audio_quality"] == 1411
    assert attrs["duration_sec"] == 300
    assert attrs["sample_rate"] == 44100
    assert attrs["bit_depth"] == 16
    assert attrs["bitrate"] is None  # >=1500 means lossless, no bitrate proxy


def test_extract_attrs_lossy():
    class FakeAttr:
        def __init__(self, key, value):
            self.key = key
            self.value = value

    attrs = _extract_attrs([FakeAttr(0, 320)])
    assert attrs["audio_quality"] == 320
    assert attrs["bitrate"] == 320  # lossy → bitrate proxy


# ── Wrapper state ────────────────────────────────────────────────────────────


def test_initial_state():
    w = SoulseekWrapper()
    assert w.connected is False
    assert w.passive_mode is False
    assert w.username is None


def test_download_status_not_found():
    w = SoulseekWrapper()
    resp = w.download_status("nobody:/nothing.mp3")
    assert resp.status == "not_found"
    assert resp.username == "nobody"
    assert resp.connection_state is None
    assert resp.message is not None
    assert "re-search" in resp.message.lower() or "not found" in resp.message.lower()


def test_connection_status():
    w = SoulseekWrapper()
    s = w.connection_status()
    assert s["connected"] is False
    assert s["username"] is None
    assert s["passive_mode"] is False
    assert s["session_id"] == 0
    assert s["session_uptime_secs"] is None
    assert s["listening_port"] is None
    assert s["active_downloads"] == 0
    assert s["max_concurrent_downloads"] == 3
    assert s["shared_files"] == 0
    assert s["shared_folders"] == 0
    assert s["share_dirs"] == []
    assert s["p2p_reachable"] is False


def test_all_downloads_empty():
    w = SoulseekWrapper()
    assert w.all_downloads() == []


# ── Share directory tests ───────────────────────────────────────────────────


def test_parse_share_dirs_none_and_empty():
    assert _parse_share_dirs(None) == []
    assert _parse_share_dirs("") == []
    assert _parse_share_dirs("   ") == []


def test_parse_share_dirs_valid_and_missing(tmp_path):
    d1 = tmp_path / "music1"
    d2 = tmp_path / "music2"
    d1.mkdir()
    d2.mkdir()
    missing = tmp_path / "missing_dir"
    regular_file = tmp_path / "regular.txt"
    regular_file.write_text("not a directory")

    raw = f"{d1}, {missing}, {regular_file}, {d2}"
    parsed = _parse_share_dirs(raw)
    assert parsed == [str(d1.resolve()), str(d2.resolve())]


def test_parse_share_dirs_delimiters_and_dedup(tmp_path):
    d1 = tmp_path / "music1"
    d2 = tmp_path / "music2"
    d1.mkdir()
    d2.mkdir()

    # Semicolons and duplicates
    raw = f"{d1};{d2}; {d1}"
    parsed = _parse_share_dirs(raw)
    assert parsed == [str(d1.resolve()), str(d2.resolve())]


def test_parse_share_dirs_env_and_tilde(tmp_path, monkeypatch):
    d = tmp_path / "mymusic"
    d.mkdir()
    monkeypatch.setenv("TEST_SHARED_VAR", str(d))
    parsed = _parse_share_dirs("$TEST_SHARED_VAR")
    assert parsed == [str(d.resolve())]


def test_share_dirs_property():
    w = SoulseekWrapper()
    assert w.share_dirs == []
    w._share_dirs = ["/tmp"]
    assert w.share_dirs == ["/tmp"]
    # Ensure defensive copy
    w.share_dirs.append("/other")
    assert w.share_dirs == ["/tmp"]


@pytest.mark.asyncio
async def test_rescan_shares_not_connected():
    w = SoulseekWrapper()
    ok, msg, files, folders = await w.rescan_shares()
    assert ok is False
    assert "Not connected" in msg
    assert files == 0
    assert folders == 0


@pytest.mark.asyncio
async def test_rescan_shares_no_dirs():
    w = SoulseekWrapper()
    w._connected = True
    w._client = MagicMock()
    ok, msg, files, folders = await w.rescan_shares()
    assert ok is True
    assert "No shared directories" in msg
    assert files == 0
    assert folders == 0


@pytest.mark.asyncio
async def test_rescan_shares_success(tmp_path):
    w = SoulseekWrapper()
    w._connected = True
    w._share_dirs = [str(tmp_path)]
    mock_client = MagicMock()
    mock_client.shares.scan = AsyncMock()
    mock_client.shares.get_stats.return_value = (8, 120)  # folders, files
    w._client = mock_client

    ok, msg, files, folders = await w.rescan_shares()
    assert ok is True
    assert files == 120
    assert folders == 8
    assert "120 files across 8 folders" in msg
    mock_client.shares.load_from_settings.assert_called_once()
    mock_client.shares.scan.assert_called_once()

