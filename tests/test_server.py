"""Tests for the MCP server tool definitions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from slsk_mcp.models import (
    LoginResponse,
    LogoutResponse,
    SearchResponse,
    SearchResultItem,
    DownloadResponse,
    DownloadStatusResponse,
    CancelDownloadResponse,
    ErrorResponse,
)


# ── Model schema tests ──────────────────────────────────────────────────────


def test_login_response_schema():
    r = LoginResponse(status="ok", message="Logged in", passive_mode=False)
    d = r.model_dump()
    assert d["status"] == "ok"
    assert d["passive_mode"] is False


def test_error_response_schema():
    r = ErrorResponse(code="not_authenticated", message="Not logged in")
    d = r.model_dump()
    assert d["status"] == "error"
    assert d["code"] == "not_authenticated"


def test_search_result_item_schema():
    item = SearchResultItem(
        id="user:/path/Song.flac",
        username="user",
        filename="/path/Song.flac",
        filesize=30_000_000,
        extension="flac",
        audio_quality=1411,
        duration_sec=240,
        sample_rate=44100,
        bit_depth=16,
    )
    d = item.model_dump()
    assert d["id"] == "user:/path/Song.flac"
    assert d["extension"] == "flac"
    assert d["bitrate"] is None


def test_download_response_schema():
    r = DownloadResponse(
        status="started",
        local_path="/tmp/Song.flac",
        filesize=30_000_000,
        message="Download started",
    )
    d = r.model_dump()
    assert d["status"] == "started"


def test_download_status_response_schema():
    r = DownloadStatusResponse(
        status="downloading",
        progress_pct=50.0,
        received_bytes=15_000_000,
        total_bytes=30_000_000,
        speed_bps=1_000_000,
        local_path="/tmp/Song.flac",
    )
    d = r.model_dump()
    assert d["progress_pct"] == 50.0


def test_cancel_download_response_schema():
    r = CancelDownloadResponse(status="cancelled")
    assert r.model_dump()["status"] == "cancelled"


def test_search_response_schema():
    r = SearchResponse(count=0, results=[])
    assert r.model_dump()["count"] == 0


def test_logout_response_schema():
    r = LogoutResponse(status="ok")
    assert r.model_dump()["status"] == "ok"
