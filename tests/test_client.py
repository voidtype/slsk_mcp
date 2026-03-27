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


def test_connection_status():
    w = SoulseekWrapper()
    s = w.connection_status()
    assert s["connected"] is False
    assert s["username"] is None
    assert s["passive_mode"] is False


def test_all_downloads_empty():
    w = SoulseekWrapper()
    assert w.all_downloads() == []
