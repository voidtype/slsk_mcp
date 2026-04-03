"""MCP server entry point for slsk-mcp."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .models import (
    ErrorResponse,
    SearchResponse,
    DownloadResponse,
    DownloadStatusResponse,
    CancelDownloadResponse,
    PeerStatusResponse,
)
from .slsk_client import SoulseekWrapper

logger = logging.getLogger("slsk_mcp")
logging.basicConfig(level=logging.INFO, stream=sys.stderr)

# ── Singleton client ─────────────────────────────────────────────────────────
_W = SoulseekWrapper()
_connect_lock = asyncio.Lock()


async def _connect() -> None:
    """Ensure the singleton is connected. Serialized via lock so parallel tool
    calls queue on connect instead of fighting over the port."""
    async with _connect_lock:
        if _W.connected:
            return

        username = os.environ.get("SLSK_USERNAME", "")
        password = os.environ.get("SLSK_PASSWORD", "")
        if not username or not password:
            raise RuntimeError("SLSK_USERNAME and SLSK_PASSWORD env vars are required")

        logger.info("_connect: logging in as %s", username)
        ok, msg, passive = await _W.login(username, password)
        if not ok:
            raise RuntimeError(f"Login failed: {msg}")
        logger.info("_connect: ok (passive=%s)", passive)


async def _with_retry(coro_factory):
    """Run an async operation; on failure, reconnect once and retry.

    coro_factory is a zero-arg callable that returns a new coroutine each call.
    """
    try:
        return await coro_factory()
    except Exception as first_err:
        logger.warning("Operation failed (%s), attempting reconnect…", first_err)
        if await _W.reconnect():
            return await coro_factory()
        raise RuntimeError(
            f"Session lost and reconnect failed. Original error: {first_err}"
        ) from first_err


# ── MCP server (no lifespan) ─────────────────────────────────────────────────
mcp = FastMCP("slsk")


# ── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
async def search(
    query: str,
    timeout: int = 7,
    extensions: Optional[list[str]] = None,
    max_results: int = 50,
    min_bitrate: Optional[int] = None,
    min_filesize: Optional[int] = None,
    max_filesize: Optional[int] = None,
    free_slots_only: bool = False,
    max_queue_size: Optional[int] = None,
    min_speed: Optional[int] = None,
) -> dict:
    """Search the Soulseek network for files.

    Filters (applied post-search, like Nicotine+/sldl/Soularr):
      - extensions: only return files with these extensions (e.g. ["flac","mp3"])
      - min_bitrate: minimum bitrate in kbps (files with unknown bitrate are kept)
      - min_filesize / max_filesize: file size bounds in bytes
      - free_slots_only: only return results from peers with free upload slots
      - max_queue_size: skip peers with queue larger than this (Soularr default: 50)
      - min_speed: minimum peer upload speed in bytes/sec
    """
    try:
        await _connect()
    except RuntimeError as exc:
        return ErrorResponse(code="not_authenticated", message=str(exc)).model_dump()

    if timeout < 7:
        timeout = 7

    try:
        results = await _with_retry(lambda: _W.search(
            query=query,
            timeout=timeout,
            extensions=extensions,
            max_results=max_results,
            min_bitrate=min_bitrate,
            min_filesize=min_filesize,
            max_filesize=max_filesize,
            free_slots_only=free_slots_only,
            max_queue_size=max_queue_size,
            min_speed=min_speed,
        ))
        return SearchResponse(count=len(results), results=results).model_dump()
    except Exception as exc:
        return ErrorResponse(code="network_error", message=str(exc)).model_dump()


@mcp.tool()
async def download(id: str, output_dir: Optional[str] = None) -> dict:
    """Download a file from a Soulseek peer.

    IMPORTANT: After calling this, SLEEP for at least 30 seconds (or do other
    productive work) before calling download_status. P2P connections take time
    to establish — the peer must accept, negotiate, and begin sending data.
    Checking immediately wastes tokens and will always show 0%.

    The response includes wait_before_poll_secs — you MUST sleep/wait that many
    seconds before your first status poll. Do not live-poll in a loop.
    """
    try:
        await _connect()
    except RuntimeError as exc:
        return ErrorResponse(code="not_authenticated", message=str(exc)).model_dump()

    try:
        ok, message, local_path, filesize = await _with_retry(
            lambda: _W.download(id, output_dir)
        )
        if ok:
            return DownloadResponse(
                status="started",
                local_path=local_path,
                filesize=filesize,
                message=message,
            ).model_dump()
        return ErrorResponse(code="peer_timeout", message=message).model_dump()
    except Exception as exc:
        return ErrorResponse(code="network_error", message=str(exc)).model_dump()


@mcp.tool()
async def download_status(id: str) -> dict:
    """Poll progress of an active or recent download.

    CRITICAL TIMING RULES:
    - Do NOT call this within 30 seconds of starting a download.
    - status='queued' at 0% is NORMAL for the first 1-2 minutes. P2P takes time.
    - Do NOT cancel a download just because it shows queued/0%. Check age_seconds.
    - Only consider cancelling if age_seconds > 180 (3 minutes) with no progress.
    - The 'message' field contains contextual guidance — READ IT and follow its
      sleep recommendation before polling again. Do NOT live-poll in a tight loop.
    - If status='not_found' or status='session_expired', the download ID is stale
      (server restarted). You must re-search and re-download.
    """
    try:
        await _connect()
    except RuntimeError as exc:
        return ErrorResponse(code="not_authenticated", message=str(exc)).model_dump()

    return _W.download_status(id).model_dump()


@mcp.tool()
async def cancel_download(id: str) -> dict:
    """Abort an in-progress download.

    ONLY cancel a download if:
    - download_status shows age_seconds > 180 AND status is still 'queued' (stuck)
    - download_status shows status='failed'
    - You explicitly want to switch to a different peer

    Do NOT cancel just because progress is 0% — P2P connections take 1-3 minutes.
    """
    try:
        await _connect()
    except RuntimeError as exc:
        return ErrorResponse(code="not_authenticated", message=str(exc)).model_dump()

    result = await _W.cancel_download(id)
    return CancelDownloadResponse(
        status=result["status"],
        received_bytes=result.get("received_bytes"),
    ).model_dump()


@mcp.tool()
async def list_downloads() -> dict:
    """List all active/queued/recent downloads in the current session.

    Use this to recover state after context compaction or session resume.
    Returns all tracked downloads with their current status, progress,
    age_seconds, username, connection_state, and local_path.

    Each entry includes the download ID so you can call download_status
    or cancel_download on specific items.
    """
    try:
        await _connect()
    except RuntimeError as exc:
        return ErrorResponse(code="not_authenticated", message=str(exc)).model_dump()

    return {"downloads": _W.all_downloads()}


@mcp.tool()
async def connection_health() -> dict:
    """Check the MCP's Soulseek connection health before starting work.

    Call this at the start of every session and after any suspected restart.
    Returns: connected, passive_mode, session_id, listening_port, p2p_reachable,
    session_uptime_secs, active_downloads, and a note if something needs attention.

    Key fields:
    - session_id: increments on each login. If it changed since your last check,
      all previous download IDs are invalid — re-search and re-download.
    - p2p_reachable: false means downloads will likely fail (double-NAT).
      Warn the user and suggest configuring SLSK_LISTEN_PORT + port forwarding.
    - passive_mode: true means no listening port bound. Same implication as above.
    """
    try:
        await _connect()
    except RuntimeError as exc:
        return ErrorResponse(code="not_authenticated", message=str(exc)).model_dump()

    return _W.connection_status()


@mcp.tool()
async def peer_status(username: str) -> dict:
    """Check a peer's online status, speed, queue, and free slots before downloading."""
    try:
        await _connect()
    except RuntimeError as exc:
        return ErrorResponse(code="not_authenticated", message=str(exc)).model_dump()

    try:
        result = await _with_retry(lambda: _W.peer_status(username))
        return result.model_dump()
    except Exception as exc:
        return ErrorResponse(code="network_error", message=str(exc)).model_dump()


# ── Search Tips (served via slsk://search_tips resource) ─────────────────────

SEARCH_TIPS = {
    "sources": [
        "Nicotine+ (https://nicotine-plus.org)",
        "sldl / slsk-batchdl (https://github.com/fiso64/sldl)",
        "Soularr (https://github.com/mrusse/soularr)",
        "SoulSync (https://github.com/Nezreka/SoulSync)",
        "Soulseek FAQ (https://www.slsknet.org/news/faq-page)",
        "r/Soulseek community",
    ],
    "query_craft": [
        {
            "tip": "Provide the least input that uniquely identifies the file",
            "detail": "Soulseek matches every word against the full file path. 'Miles Davis Kind of Blue flac' beats 'jazz trumpet classic'. Include artist + album + format.",
            "source": "sldl docs",
            "mcp_action": "Pass a focused query string to the search tool.",
        },
        {
            "tip": "Exclude unwanted terms with a minus sign",
            "detail": "The Soulseek protocol supports '-word' to exclude results. 'flac -live' excludes live recordings. 'jazz -compilation' skips compilations.",
            "source": "Nicotine+ search syntax",
            "mcp_action": "Include '-term' directly in the query string, e.g. query='aphex twin flac -live'.",
        },
        {
            "tip": "Search matches folder names and full file paths, not just filenames",
            "detail": "Searching 'experimental' returns all files inside folders named 'experimental'. You can search by genre folder names, label names, or any part of the directory structure.",
            "source": "Soulseek Wikipedia / official FAQ",
            "mcp_action": "Use folder-level terms in query to find albums by genre or label.",
        },
        {
            "tip": "Drop 'feat.' and featured artists from queries",
            "detail": "Featured artist credits vary wildly across file names ('ft.', 'feat.', 'featuring', parenthesized). Including them causes missed matches.",
            "source": "sldl --remove-ft flag",
            "mcp_action": "Strip featured artist text before passing the query.",
        },
        {
            "tip": "For 'Various Artists' compilations, search by track name only",
            "detail": "The artist field on compilations is unreliable. sldl recommends removing the artist entirely for VA releases and searching by track title + album name.",
            "source": "sldl tips",
            "mcp_action": "Omit 'Various Artists' from query; use track title + album name instead.",
        },
    ],
    "filters": [
        {
            "tip": "Filter by file extension to cut non-audio clutter",
            "detail": "Search results include images, .nfo, .txt, .m3u, .cue files. Filter to audio only.",
            "source": "Nicotine+ file type filter, Soularr allowed_filetypes",
            "mcp_action": "Pass extensions=['flac','mp3'] (or your preferred formats) to the search tool.",
        },
        {
            "tip": "Set a minimum bitrate to skip low-quality encodes",
            "detail": "sldl defaults to preferring >= 200 kbps. Files with unknown bitrate are kept (SoulseekQt doesn't broadcast bitrate).",
            "source": "sldl pref-min-bitrate=200, Nicotine+ bitrate filter",
            "mcp_action": "Pass min_bitrate=200 (or 320 for high quality) to the search tool.",
        },
        {
            "tip": "Filter by file size to skip tiny or suspiciously large files",
            "detail": "A 3-minute MP3 at 320kbps is ~7MB. A 3-minute FLAC is ~25-35MB. Files under 1MB are likely corrupt or incomplete.",
            "source": "Nicotine+ min/max file size filter",
            "mcp_action": "Pass min_filesize=1000000 (1MB) to the search tool. Use max_filesize to cap.",
        },
        {
            "tip": "Only show peers with free upload slots",
            "detail": "If has_free_slots is false, you'll sit in their queue — potentially for hours. This is the single most impactful filter for download reliability.",
            "source": "Nicotine+ 'Free Slot' filter (one of its most popular features)",
            "mcp_action": "Pass free_slots_only=true to the search tool.",
        },
        {
            "tip": "Cap peer queue size to avoid long waits",
            "detail": "A peer with 200 files queued will take much longer to serve you than one with 5.",
            "source": "Soularr maximum_peer_queue=50 default",
            "mcp_action": "Pass max_queue_size=50 to the search tool.",
        },
        {
            "tip": "Set a minimum peer upload speed",
            "detail": "Peers with very low upload speeds will take forever. Filter them out.",
            "source": "Soularr minimum_peer_upload_speed, Nicotine+ upload speed filter",
            "mcp_action": "Pass min_speed=50000 (50 KB/s) or higher to the search tool.",
        },
    ],
    "timing": [
        {
            "tip": "Increase timeout for rare or obscure content",
            "detail": "7 seconds is the minimum. Popular music is fine at 7-10s. Niche/obscure content needs 15-30s for slower peers to respond.",
            "source": "sldl --search-timeout, general community advice",
            "mcp_action": "Pass timeout=20 or timeout=30 for rare searches.",
        },
        {
            "tip": "Search at different times for different results",
            "detail": "Results are a snapshot of who's online. Nicotine+ wishlists re-run searches every 90-120 minutes. Different peers are on at different times of day.",
            "source": "Nicotine+ wishlist interval, Soulseek FAQ",
            "mcp_action": "If first search yields nothing, try again later. Peak hours (US/EU evenings) have more peers online.",
        },
    ],
    "peer_selection": [
        {
            "tip": "Use peer_status before committing to a download",
            "detail": "The peer was online during search but may have gone offline. Check their current state before downloading.",
            "source": "General best practice across all clients",
            "mcp_action": "Call peer_status(username) — check for status='online' and has_slots_free=true.",
        },
        {
            "tip": "For full albums, download all tracks from one peer",
            "detail": "Downloading from one user ensures consistent encoding, tagging, and folder structure. Mixing peers produces inconsistent albums.",
            "source": "SoulSync 'source reuse for album consistency'",
            "mcp_action": "Filter search results by username to find a single peer sharing all album tracks.",
        },
        {
            "tip": "If a peer can't connect, try the next one",
            "detail": "If both you and the peer are behind NAT, neither can initiate a connection. This causes permanent 'queued' with no position number.",
            "source": "Soulseek FAQ on listening ports, r/Soulseek community",
            "mcp_action": "Cancel stalled download, pick another peer from search results sharing the same file.",
        },
    ],
    "downloading": [
        {
            "tip": "Set a stale timeout and cancel stuck downloads",
            "detail": "sldl waits 30s max; Soularr waits 1 hour. If a download hasn't progressed, cancel and try another peer.",
            "source": "sldl --max-stale-time=30000, Soularr stalled_timeout=3600",
            "mcp_action": "Poll download_status periodically. If progress_pct hasn't moved, call cancel_download and retry from next peer.",
        },
        {
            "tip": "Prefer FLAC but accept fallbacks",
            "detail": "sldl's default: prefer lossless (flac, wav) but still accept lossy if unavailable, with min preferred bitrate of 200 kbps. Don't get stuck hunting for a FLAC that doesn't exist.",
            "source": "sldl pref-format=flac,wav with fallback",
            "mcp_action": "First search with extensions=['flac']. If count=0, retry with extensions=['flac','mp3'].",
        },
        {
            "tip": "Don't queue tons of files from one user",
            "detail": "Soulseek etiquette: stick to 1-2 albums at a time per user. Queuing too much may get you banned.",
            "source": "WikiHow Soulseek ban avoidance guide, community norms",
            "mcp_action": "Spread downloads across multiple peers when grabbing large amounts.",
        },
        {
            "tip": "Files with unknown bitrate may still be high quality",
            "detail": "The standard SoulseekQt client doesn't broadcast bitrate. Don't reject files solely because metadata is null.",
            "source": "sldl docs on --strict-conditions caveat",
            "mcp_action": "The min_bitrate filter keeps files with unknown bitrate by design (same as sldl behavior).",
        },
    ],
    "troubleshooting": [
        {
            "tip": "Empty search results?",
            "detail": "Check: (1) query too specific — try fewer words, (2) timeout too short — increase to 15-20s, (3) very few peers share this — try again at peak hours, (4) search during off-peak may miss users.",
            "source": "Nicotine+ troubleshooting docs",
            "mcp_action": "Broaden query, increase timeout, retry later.",
        },
        {
            "tip": "Stuck at 'queued' with no position number?",
            "detail": "Almost always a connectivity issue — NAT on both sides. The peer can't reach you and you can't reach them.",
            "source": "r/Soulseek (common across dozens of threads), Soulseek FAQ on listening ports",
            "mcp_action": "Cancel and download from a different peer.",
        },
        {
            "tip": "Soulseek recycles usernames after 30 days of inactivity",
            "detail": "If you can't log in, your username may have been recycled. Register again.",
            "source": "Soulseek FAQ",
            "mcp_action": "Set correct SLSK_USERNAME/SLSK_PASSWORD env vars.",
        },
    ],
}


# ── Resources ────────────────────────────────────────────────────────────────


@mcp.resource("slsk://status")
def get_status() -> str:
    """Current connection state, username, passive mode flag."""
    return json.dumps(_W.connection_status())


@mcp.resource("slsk://downloads")
def get_downloads() -> str:
    """List of all tracked downloads with status/progress."""
    return json.dumps(_W.all_downloads())


@mcp.resource("slsk://search_tips")
def get_search_tips() -> str:
    """Actionable search and download strategies for the Soulseek network.

    Sourced from Nicotine+, sldl, Soularr, SoulSync, and the Soulseek community.
    Every tip maps to a feature available in this MCP server.
    """
    return json.dumps(SEARCH_TIPS)


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
