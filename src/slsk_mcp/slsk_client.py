"""aioslsk wrapper — login, search, download, progress tracking."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aioslsk.client import SoulSeekClient
from aioslsk.settings import Settings, CredentialsSettings, ListeningConnectionErrorMode
from aioslsk.search.model import SearchRequest as SlskSearchRequest
from aioslsk.transfer.model import Transfer

from .models import (
    SearchResultItem,
    DownloadStatusResponse,
)

logger = logging.getLogger("slsk_mcp")

# aioslsk FileData attribute keys
ATTR_AUDIO_QUALITY = 0
ATTR_DURATION = 1
ATTR_SAMPLE_RATE = 4
ATTR_BIT_DEPTH = 5

# How long to keep finished download records before cleanup (seconds)
_FINISHED_TTL = 60

# Transfer state name mapping
_STATE_MAP: Dict[str, str] = {
    "VIRGIN": "queued",
    "QUEUED": "queued",
    "INITIALIZING": "queued",
    "NEGOTIATING": "queued",
    "DOWNLOADING": "downloading",
    "UPLOADING": "downloading",
    "INCOMPLETE": "downloading",
    "COMPLETE": "finished",
    "FAILED": "failed",
    "ABORTED": "cancelled",
    "PAUSED": "queued",
}


def _parse_id(file_id: str) -> Tuple[str, str]:
    """Split 'username:/remote/path' into (username, remote_path)."""
    sep = file_id.index(":")
    return file_id[:sep], file_id[sep + 1 :]


def _file_extension(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    return ext.lower()


def _extract_attrs(attributes: Any) -> Dict[str, Optional[int]]:
    """Extract audio attributes from aioslsk FileData attributes list."""
    result: Dict[str, Optional[int]] = {
        "bitrate": None,
        "sample_rate": None,
        "bit_depth": None,
        "duration_sec": None,
        "audio_quality": None,
    }
    if not attributes:
        return result

    attr_map: Dict[int, Any] = {}
    for attr in attributes:
        try:
            attr_map[attr.key] = attr.value
        except AttributeError:
            continue

    result["audio_quality"] = attr_map.get(ATTR_AUDIO_QUALITY)
    result["duration_sec"] = attr_map.get(ATTR_DURATION)
    result["sample_rate"] = attr_map.get(ATTR_SAMPLE_RATE)
    result["bit_depth"] = attr_map.get(ATTR_BIT_DEPTH)
    # Use audio_quality as bitrate proxy for lossy formats (< 1000 kbps)
    if result["audio_quality"] and result["audio_quality"] < 1000:
        result["bitrate"] = result["audio_quality"]

    return result


class SoulseekWrapper:
    """Thin wrapper around aioslsk providing the operations the MCP server needs."""

    def __init__(self) -> None:
        self._client: Optional[SoulSeekClient] = None
        self._connected: bool = False
        self._passive_mode: bool = False
        self._username: Optional[str] = None

        # id → {transfer, local_path, filesize, finished_at}
        self._downloads: Dict[str, Dict[str, Any]] = {}

        # Concurrency controls
        self._download_sem: Optional[asyncio.Semaphore] = None
        self._search_sem = asyncio.Semaphore(4)

        self._max_concurrent_dl = int(os.environ.get("SLSK_MAX_CONCURRENT_DL", "3"))

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def passive_mode(self) -> bool:
        return self._passive_mode

    @property
    def username(self) -> Optional[str]:
        return self._username

    # ── Login / Logout ───────────────────────────────────────────────────

    async def login(self, username: str, password: str) -> Tuple[bool, str, bool]:
        """Connect and authenticate. Returns (success, message, passive_mode)."""
        # Tear down any existing session
        await self.logout()

        listen_port = int(os.environ.get("SLSK_LISTEN_PORT", "0")) or None
        obfuscated_port = int(os.environ.get("SLSK_OBFUSCATED_PORT", "0")) or None

        settings = Settings(
            credentials=CredentialsSettings(
                username=username,
                password=password,
            ),
        )
        # Always use CLEAR mode so port-bind failures are ignored (passive mode)
        settings.network.listening.error_mode = ListeningConnectionErrorMode.CLEAR
        if listen_port:
            settings.network.listening.port = listen_port
        if obfuscated_port:
            settings.network.listening.obfuscated_port = obfuscated_port

        download_dir = os.environ.get("SLSK_DOWNLOAD_DIR", "./downloads")
        settings.shares.download = download_dir

        self._client = SoulSeekClient(settings)

        try:
            await self._client.start()
            await self._client.login()
        except Exception as exc:
            logger.error("Login failed: %s", exc)
            try:
                await self._client.stop()
            except Exception:
                pass
            self._client = None
            return False, f"Login failed: {exc}", False

        # Detect passive mode: if no listening ports were bound
        self._passive_mode = not self._has_listening_ports()
        self._connected = True
        self._username = username
        self._download_sem = asyncio.Semaphore(self._max_concurrent_dl)
        logger.info("Login complete (passive=%s)", self._passive_mode)
        return True, "Logged in successfully", self._passive_mode

    def _has_listening_ports(self) -> bool:
        """Check if the client has active listening connections."""
        try:
            network = self._client._network  # type: ignore[union-attr]
            listeners = getattr(network, "listening_connections", [])
            return len(listeners) > 0
        except Exception:
            return False

    async def logout(self) -> None:
        """Disconnect from Soulseek."""
        if self._client:
            try:
                await self._client.stop()
            except Exception:
                pass
            self._client = None
        self._connected = False
        self._username = None

    # ── Search ───────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        timeout: Optional[int] = None,
        extensions: Optional[List[str]] = None,
        max_results: int = 50,
    ) -> List[SearchResultItem]:
        """Run a network search and return sorted results."""
        assert self._client is not None

        if timeout is None:
            timeout = int(os.environ.get("SLSK_SEARCH_TIMEOUT", "7"))
        timeout = max(timeout, 7)  # enforce floor

        logger.info("Starting search query=%r timeout=%d", query, timeout)
        async with self._search_sem:
            request: SlskSearchRequest = await self._client.searches.search(query)
            await asyncio.sleep(timeout)

        logger.info("Search complete: %d raw results for query=%r", len(request.results), query)
        items: List[SearchResultItem] = []
        for result in request.results:
            username = result.username
            for shared_item in result.shared_items:
                filename = shared_item.filename
                ext = _file_extension(filename)

                if extensions and ext not in [e.lower() for e in extensions]:
                    continue

                attrs = _extract_attrs(shared_item.attributes)
                file_id = f"{username}:{filename}"

                items.append(
                    SearchResultItem(
                        id=file_id,
                        username=username,
                        filename=filename,
                        filesize=shared_item.filesize,
                        extension=ext,
                        **attrs,
                    )
                )

        # Sort: lossless (high audio_quality) first, then by audio_quality desc
        items.sort(key=lambda r: r.audio_quality or 0, reverse=True)

        return items[:max_results]

    # ── Download ─────────────────────────────────────────────────────────

    async def download(
        self, file_id: str, output_dir: Optional[str] = None
    ) -> Tuple[bool, str, Optional[str], Optional[int]]:
        """Start a download. Returns (success, message, local_path, filesize)."""
        assert self._client is not None

        username, remote_path = _parse_id(file_id)
        filename = remote_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]

        dl_dir = output_dir or os.environ.get("SLSK_DOWNLOAD_DIR", "./downloads")
        dl_path = Path(dl_dir)
        dl_path.mkdir(parents=True, exist_ok=True)

        # Resolve collision
        local_file = dl_path / filename
        counter = 1
        stem = local_file.stem
        suffix = local_file.suffix
        while local_file.exists():
            local_file = dl_path / f"{stem}_{counter}{suffix}"
            counter += 1

        try:
            assert self._download_sem is not None
            await self._download_sem.acquire()
            transfer: Transfer = await self._client.transfers.download(
                username, remote_path
            )
            self._downloads[file_id] = {
                "transfer": transfer,
                "local_path": str(local_file),
                "filesize": transfer.filesize if hasattr(transfer, 'filesize') else None,
                "finished_at": None,
            }
            # Release semaphore when transfer completes (fire-and-forget)
            asyncio.get_event_loop().create_task(
                self._watch_transfer(file_id)
            )
            filesize = transfer.filesize if hasattr(transfer, 'filesize') else None
            return True, "Download started", str(local_file), filesize
        except Exception as exc:
            self._download_sem.release()
            return False, f"Download failed: {exc}", None, None

    async def _watch_transfer(self, file_id: str) -> None:
        """Wait for a transfer to finish, then release the semaphore and mark time."""
        entry = self._downloads.get(file_id)
        if not entry:
            return
        transfer: Transfer = entry["transfer"]
        try:
            while True:
                state = self._transfer_state(transfer)
                if state in ("finished", "failed", "cancelled"):
                    break
                await asyncio.sleep(1)
        finally:
            if self._download_sem:
                self._download_sem.release()
            entry["finished_at"] = time.time()

    def _transfer_state(self, transfer: Transfer) -> str:
        """Map aioslsk transfer state to our status string."""
        state_name = transfer.state.name if hasattr(transfer.state, 'name') else str(transfer.state)
        return _STATE_MAP.get(state_name.upper(), "queued")

    # ── Download Status ──────────────────────────────────────────────────

    def download_status(self, file_id: str) -> DownloadStatusResponse:
        """Get current download progress."""
        self._cleanup_finished()

        entry = self._downloads.get(file_id)
        if not entry:
            return DownloadStatusResponse(status="not_found")

        transfer: Transfer = entry["transfer"]
        status = self._transfer_state(transfer)

        total = getattr(transfer, "filesize", None) or 0
        received = getattr(transfer, "bytes_transfered", None)
        if received is None:
            received = getattr(transfer, "bytes_transferred", 0)
        speed = getattr(transfer, "speed", None) or 0

        progress_pct = (received / total * 100) if total else 0.0

        return DownloadStatusResponse(
            status=status,
            progress_pct=round(progress_pct, 1),
            received_bytes=received,
            total_bytes=total,
            speed_bps=speed,
            local_path=entry.get("local_path"),
        )

    # ── Cancel Download ──────────────────────────────────────────────────

    async def cancel_download(self, file_id: str) -> str:
        """Abort a download. Returns 'cancelled' or 'not_found'."""
        entry = self._downloads.get(file_id)
        if not entry:
            return "not_found"

        transfer: Transfer = entry["transfer"]
        try:
            assert self._client is not None
            await self._client.transfers.abort(transfer)
            return "cancelled"
        except Exception:
            return "not_found"

    # ── Status snapshot (for resources) ──────────────────────────────────

    def connection_status(self) -> Dict[str, Any]:
        return {
            "connected": self._connected,
            "username": self._username,
            "passive_mode": self._passive_mode,
        }

    def all_downloads(self) -> List[Dict[str, Any]]:
        self._cleanup_finished()
        results = []
        for file_id, entry in self._downloads.items():
            ds = self.download_status(file_id)
            results.append({"id": file_id, **ds.model_dump()})
        return results

    # ── Internal ─────────────────────────────────────────────────────────

    def _cleanup_finished(self) -> None:
        now = time.time()
        expired = [
            fid
            for fid, e in self._downloads.items()
            if e.get("finished_at") and now - e["finished_at"] > _FINISHED_TTL
        ]
        for fid in expired:
            del self._downloads[fid]
