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
    PeerStatusResponse,
)

logger = logging.getLogger("slsk_mcp")

# aioslsk FileData attribute keys
ATTR_AUDIO_QUALITY = 0
ATTR_DURATION = 1
ATTR_SAMPLE_RATE = 4
ATTR_BIT_DEPTH = 5

# How long to keep finished download records before cleanup (seconds)
_FINISHED_TTL = 60

# Transfer state name mapping (coarse status for simple decisions)
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

# Detailed connection state (diagnostic — tells you WHERE it's stuck)
_CONN_STATE_MAP: Dict[str, str] = {
    "VIRGIN": "pending",
    "QUEUED": "waiting_for_peer",
    "INITIALIZING": "connecting",
    "NEGOTIATING": "negotiating",
    "DOWNLOADING": "transferring",
    "UPLOADING": "transferring",
    "INCOMPLETE": "transferring",
    "COMPLETE": "complete",
    "FAILED": "failed",
    "ABORTED": "aborted",
    "PAUSED": "paused",
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

    # Max retries on session drop before returning an error
    _MAX_RETRIES = 1
    _RETRY_BACKOFF = 2.0  # seconds

    def __init__(self) -> None:
        self._client: Optional[SoulSeekClient] = None
        self._connected: bool = False
        self._passive_mode: bool = False
        self._username: Optional[str] = None
        self._password: Optional[str] = None

        # Session tracking — increments on each successful login
        self._session_id: int = 0
        self._session_start: Optional[float] = None

        # id → {transfer, local_path, filesize, finished_at, session_id, started_at}
        self._downloads: Dict[str, Dict[str, Any]] = {}

        # Connection mutex — prevents concurrent login stomps
        self._conn_lock = asyncio.Lock()
        # Socket-level concurrency: how many operations may hit the wire at once
        _max_ops = int(os.environ.get("SLSK_MAX_CONCURRENT_OPS", "1"))
        self._op_lock = asyncio.Semaphore(_max_ops)

        # Concurrency controls
        self._download_sem: Optional[asyncio.Semaphore] = None
        self._max_concurrent_dl = int(os.environ.get("SLSK_MAX_CONCURRENT_DL", "3"))
        _max_search = int(os.environ.get("SLSK_MAX_CONCURRENT_SEARCH", "4"))
        self._search_sem = asyncio.Semaphore(_max_search)

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
        """Connect and authenticate. Returns (success, message, passive_mode).

        Thread-safe: uses _conn_lock so concurrent callers queue instead of
        stomping each other's connections.
        """
        async with self._conn_lock:
            return await self._login_inner(username, password)

    async def _login_inner(self, username: str, password: str) -> Tuple[bool, str, bool]:
        # If already connected with same creds, return immediately
        if self._connected and self._username == username:
            logger.info("Already connected as %s, skipping re-login", username)
            return True, "Already connected", self._passive_mode

        # Tear down any existing session
        await self.logout()

        listen_port = int(os.environ.get("SLSK_LISTEN_PORT", "0")) or None
        obfuscated_port = int(os.environ.get("SLSK_OBFUSCATED_PORT", "0")) or None
        logger.info("Listen port config: SLSK_LISTEN_PORT=%s, parsed=%s", os.environ.get("SLSK_LISTEN_PORT"), listen_port)

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
        logger.info("Listening settings: port=%s, error_mode=%s", settings.network.listening.port, settings.network.listening.error_mode)

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
        self._password = password
        self._session_id += 1
        self._session_start = time.time()
        self._download_sem = asyncio.Semaphore(self._max_concurrent_dl)
        # Invalidate downloads from previous sessions (transfer objects are dead)
        self._invalidate_stale_downloads()
        logger.info("Login complete (session=%d, passive=%s)", self._session_id, self._passive_mode)
        return True, "Logged in successfully", self._passive_mode

    def _has_listening_ports(self) -> bool:
        """Check if the client has active listening connections."""
        try:
            from aioslsk.network.connection import ConnectionState
            network = self._client.network  # type: ignore[union-attr]
            listeners = getattr(network, "listening_connections", ())
            return any(
                conn is not None and conn.state == ConnectionState.CONNECTED
                for conn in listeners
            )
        except Exception:
            return False

    def _get_listening_port(self) -> Optional[int]:
        """Return the listening port number, or None if not bound."""
        try:
            from aioslsk.network.connection import ConnectionState
            network = self._client.network  # type: ignore[union-attr]
            listeners = getattr(network, "listening_connections", ())
            # First element is the non-obfuscated listener
            conn = listeners[0] if listeners else None
            if conn is not None and conn.state == ConnectionState.CONNECTED:
                if conn._server is not None:
                    sockets = conn._server.sockets
                    if sockets:
                        return sockets[0].getsockname()[1]
                return conn.port
        except Exception:
            pass
        return None

    def _invalidate_stale_downloads(self) -> None:
        """Mark all downloads from previous sessions as expired."""
        stale_ids = [
            fid for fid, entry in self._downloads.items()
            if entry.get("session_id", 0) < self._session_id
        ]
        for fid in stale_ids:
            del self._downloads[fid]
        if stale_ids:
            logger.info("Cleared %d stale downloads from previous session", len(stale_ids))

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
        self._password = None

    async def reconnect(self) -> bool:
        """Attempt to re-establish the session from stored credentials.

        Returns True if reconnect succeeded.
        """
        if not self._username or not self._password:
            return False
        user, pw = self._username, self._password
        logger.info("Reconnecting as %s (backoff=%.1fs)", user, self._RETRY_BACKOFF)
        await asyncio.sleep(self._RETRY_BACKOFF)
        # logout clears _username/_password, so capture first
        ok, msg, _ = await self.login(user, pw)
        if ok:
            logger.info("Reconnect succeeded")
        else:
            logger.error("Reconnect failed: %s", msg)
        return ok

    # ── Search ───────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        timeout: Optional[int] = None,
        extensions: Optional[List[str]] = None,
        max_results: int = 50,
        min_bitrate: Optional[int] = None,
        min_filesize: Optional[int] = None,
        max_filesize: Optional[int] = None,
        free_slots_only: bool = False,
        max_queue_size: Optional[int] = None,
        min_speed: Optional[int] = None,
    ) -> List[SearchResultItem]:
        """Run a network search and return sorted, filtered results."""
        assert self._client is not None

        if timeout is None:
            timeout = int(os.environ.get("SLSK_SEARCH_TIMEOUT", "7"))
        timeout = max(timeout, 7)  # enforce floor

        logger.info("Starting search query=%r timeout=%d", query, timeout)
        async with self._op_lock:
            async with self._search_sem:
                request: SlskSearchRequest = await self._client.searches.search(query)
                await asyncio.sleep(timeout)

        logger.info("Search complete: %d raw results for query=%r", len(request.results), query)

        # Build a cached user status lookup for online_now field
        _user_online: Dict[str, Optional[bool]] = {}
        def _check_online(uname: str) -> Optional[bool]:
            if uname in _user_online:
                return _user_online[uname]
            try:
                user_obj = self._client.users.get_user_object(uname)  # type: ignore[union-attr]
                status_val = getattr(getattr(user_obj, 'status', None), 'value', None)
                # 0=offline, 1=away, 2=online; away counts as online (they appeared in results)
                online = status_val is not None and status_val > 0 if status_val is not None else None
                _user_online[uname] = online
            except Exception:
                _user_online[uname] = None
            return _user_online[uname]

        items: List[SearchResultItem] = []
        for result in request.results:
            username = result.username

            # Peer-level filters (Nicotine+ free-slot filter, Soularr max_peer_queue / min_peer_upload_speed)
            if free_slots_only and not result.has_free_slots:
                continue
            if max_queue_size is not None and result.queue_size > max_queue_size:
                continue
            if min_speed is not None and result.avg_speed < min_speed:
                continue

            for shared_item in result.shared_items:
                filename = shared_item.filename
                ext = _file_extension(filename)

                if extensions and ext not in [e.lower() for e in extensions]:
                    continue

                # File-level filters (Nicotine+ size filter, sldl min-bitrate)
                if min_filesize is not None and shared_item.filesize < min_filesize:
                    continue
                if max_filesize is not None and shared_item.filesize > max_filesize:
                    continue

                attrs = _extract_attrs(shared_item.attributes)

                if min_bitrate is not None:
                    br = attrs.get("bitrate")
                    if br is not None and br < min_bitrate:
                        continue

                file_id = f"{username}:{filename}"

                items.append(
                    SearchResultItem(
                        id=file_id,
                        username=username,
                        filename=filename,
                        filesize=shared_item.filesize,
                        extension=ext,
                        has_free_slots=result.has_free_slots,
                        avg_speed=result.avg_speed,
                        queue_size=result.queue_size,
                        online_now=_check_online(username),
                        **attrs,
                    )
                )

        # Sort: free slots first, then by audio_quality desc, then speed desc
        items.sort(
            key=lambda r: (
                1 if r.has_free_slots else 0,
                r.audio_quality or 0,
                r.avg_speed or 0,
            ),
            reverse=True,
        )

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

        # Duplicate detection: if already tracked and transfer is alive, return existing
        existing = self._downloads.get(file_id)
        if existing and existing.get("session_id") == self._session_id:
            transfer_existing: Transfer = existing["transfer"]
            state = self._transfer_state(transfer_existing)
            if state in ("queued", "downloading"):
                return True, "Download already in progress (duplicate request ignored)", existing.get("local_path"), existing.get("filesize")

        try:
            assert self._download_sem is not None
            await self._download_sem.acquire()
            async with self._op_lock:
                transfer: Transfer = await self._client.transfers.download(
                    username, remote_path
                )
            self._downloads[file_id] = {
                "transfer": transfer,
                "local_path": str(local_file),
                "filesize": transfer.filesize if hasattr(transfer, 'filesize') else None,
                "finished_at": None,
                "session_id": self._session_id,
                "started_at": time.time(),
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

    def _connection_state(self, transfer: Transfer) -> str:
        """Map aioslsk transfer state to detailed connection state."""
        state_name = transfer.state.name if hasattr(transfer.state, 'name') else str(transfer.state)
        return _CONN_STATE_MAP.get(state_name.upper(), "pending")

    def download_status(self, file_id: str) -> DownloadStatusResponse:
        """Get current download progress."""
        self._cleanup_finished()

        # Parse username from file_id for all responses
        try:
            username, _ = _parse_id(file_id)
        except (ValueError, IndexError):
            username = None

        entry = self._downloads.get(file_id)
        if not entry:
            return DownloadStatusResponse(
                status="not_found",
                username=username,
                message="Download ID not found. If the MCP server restarted, all previous download state is lost. You must re-search and re-download.",
            )

        # Check session validity
        if entry.get("session_id", 0) < self._session_id:
            return DownloadStatusResponse(
                status="session_expired",
                username=username,
                message="This download was started in a previous session that no longer exists. The transfer object is dead. Re-search and re-download from a fresh search.",
            )

        transfer: Transfer = entry["transfer"]
        status = self._transfer_state(transfer)
        conn_state = self._connection_state(transfer)

        total = getattr(transfer, "filesize", None) or 0
        received = getattr(transfer, "bytes_transfered", None)
        if received is None:
            received = getattr(transfer, "bytes_transferred", 0)
        speed = getattr(transfer, "speed", None) or 0

        progress_pct = (received / total * 100) if total else 0.0

        started_at = entry.get("started_at", time.time())
        age = round(time.time() - started_at, 1)

        # Contextual message to help AI make good decisions
        msg = None
        if status == "queued" and age < 60:
            msg = f"NORMAL: download is queued ({age:.0f}s old, state={conn_state}). P2P connections take time to establish. Do NOT cancel yet — wait at least 60 seconds."
        elif status == "queued" and age < 180:
            msg = f"Download has been queued for {age:.0f}s (state={conn_state}). The peer may be busy or behind NAT. Consider waiting up to 3 minutes before cancelling."
        elif status == "queued" and age >= 180:
            if conn_state == "waiting_for_peer":
                msg = f"Download stuck at waiting_for_peer for {age:.0f}s. The peer is not responding — likely offline or blocking. Cancel and try the next peer."
            elif conn_state == "connecting":
                msg = f"Download stuck at connecting for {age:.0f}s. Cannot establish P2P connection — likely a NAT/firewall issue on the peer's side. Cancel and try another peer."
            else:
                msg = f"Download stuck queued for {age:.0f}s (state={conn_state}). Likely a connectivity issue. Cancel and try the next peer sharing this file."
        elif status == "downloading":
            msg = f"Transfer active at {speed} bytes/sec. Do not cancel."

        return DownloadStatusResponse(
            status=status,
            username=username,
            connection_state=conn_state,
            progress_pct=round(progress_pct, 1),
            received_bytes=received,
            total_bytes=total,
            speed_bps=speed,
            local_path=entry.get("local_path"),
            age_seconds=age,
            message=msg,
        )

    # ── Cancel Download ──────────────────────────────────────────────────

    async def cancel_download(self, file_id: str) -> Dict[str, Any]:
        """Abort a download. Returns dict with status and received_bytes."""
        entry = self._downloads.get(file_id)
        if not entry:
            return {"status": "not_found", "received_bytes": None}

        transfer: Transfer = entry["transfer"]
        # Capture bytes before aborting
        received = getattr(transfer, "bytes_transfered", None)
        if received is None:
            received = getattr(transfer, "bytes_transferred", 0)

        try:
            assert self._client is not None
            await self._client.transfers.abort(transfer)
            return {"status": "cancelled", "received_bytes": received}
        except Exception:
            return {"status": "not_found", "received_bytes": received}

    # ── Peer Status ───────────────────────────────────────────────────────

    async def peer_status(self, username: str) -> PeerStatusResponse:
        """Query a peer's online status and stats from the server."""
        assert self._client is not None

        # Track the user so aioslsk fetches status + stats from server
        await self._client.users.track_user(username)
        # Give the server a moment to respond
        await asyncio.sleep(1.5)

        user = self._client.users.get_user_object(username)

        status_map = {0: "offline", 1: "away", 2: "online"}
        status_val = getattr(user.status, 'value', None)
        status_str = status_map.get(status_val, "unknown") if status_val is not None else "unknown"

        return PeerStatusResponse(
            username=username,
            status=status_str,
            avg_speed=getattr(user, 'avg_speed', None),
            uploads=getattr(user, 'uploads', None),
            shared_files=getattr(user, 'shared_file_count', None),
            shared_folders=getattr(user, 'shared_folder_count', None),
            has_slots_free=getattr(user, 'has_slots_free', None),
            queue_length=getattr(user, 'queue_length', None),
        )

    # ── Status snapshot (for resources) ──────────────────────────────────

    def connection_status(self) -> Dict[str, Any]:
        session_uptime = None
        if self._session_start:
            session_uptime = round(time.time() - self._session_start, 1)

        listening_port = self._get_listening_port() if self._connected else None
        active_downloads = sum(
            1 for e in self._downloads.values()
            if e.get("session_id") == self._session_id and not e.get("finished_at")
        )

        return {
            "connected": self._connected,
            "username": self._username,
            "passive_mode": self._passive_mode,
            "session_id": self._session_id,
            "session_uptime_secs": session_uptime,
            "listening_port": listening_port,
            "active_downloads": active_downloads,
            "max_concurrent_downloads": self._max_concurrent_dl,
            "p2p_reachable": not self._passive_mode and listening_port is not None,
            "note": (
                "passive_mode=true means no listening port is bound. "
                "P2P transfers may fail if both you and the peer are behind NAT. "
                "Configure SLSK_LISTEN_PORT and forward that port on your router."
            ) if self._passive_mode else None,
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
