"""Pydantic schemas for slsk-mcp tool I/O."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ── Login ────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    status: str  # "ok" | "error"
    message: str
    passive_mode: bool = False


# ── Logout ───────────────────────────────────────────────────────────────────

class LogoutResponse(BaseModel):
    status: str


# ── Search ───────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    timeout: int = Field(default=7, ge=7)
    extensions: Optional[List[str]] = None
    max_results: int = Field(default=50, ge=1)


class SearchResultItem(BaseModel):
    id: str  # "username:/remote/path"
    username: str
    filename: str
    filesize: int
    extension: str
    bitrate: Optional[int] = None
    sample_rate: Optional[int] = None
    bit_depth: Optional[int] = None
    duration_sec: Optional[int] = None
    audio_quality: Optional[int] = None
    has_free_slots: Optional[bool] = None
    avg_speed: Optional[int] = None  # bytes/sec
    queue_size: Optional[int] = None


class PeerStatusResponse(BaseModel):
    username: str
    status: str  # online | away | offline | unknown
    avg_speed: Optional[int] = None  # bytes/sec
    uploads: Optional[int] = None
    shared_files: Optional[int] = None
    shared_folders: Optional[int] = None
    has_slots_free: Optional[bool] = None
    queue_length: Optional[int] = None


class SearchResponse(BaseModel):
    count: int
    results: List[SearchResultItem]


# ── Download ─────────────────────────────────────────────────────────────────

class DownloadRequest(BaseModel):
    id: str
    output_dir: Optional[str] = None


class DownloadResponse(BaseModel):
    status: str  # "started" | "error"
    local_path: Optional[str] = None
    filesize: Optional[int] = None
    message: str


# ── Download Status ──────────────────────────────────────────────────────────

class DownloadStatusRequest(BaseModel):
    id: str


class DownloadStatusResponse(BaseModel):
    status: str  # queued | downloading | finished | failed | cancelled | not_found
    progress_pct: Optional[float] = None
    received_bytes: Optional[int] = None
    total_bytes: Optional[int] = None
    speed_bps: Optional[int] = None
    local_path: Optional[str] = None


# ── Cancel Download ──────────────────────────────────────────────────────────

class CancelDownloadRequest(BaseModel):
    id: str


class CancelDownloadResponse(BaseModel):
    status: str  # "cancelled" | "not_found"


# ── Error ────────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    status: str = "error"
    code: str  # not_authenticated | network_error | peer_timeout | invalid_params
    message: str
