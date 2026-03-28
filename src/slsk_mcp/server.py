"""MCP server entry point for slsk-mcp."""

from __future__ import annotations

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
)
from .slsk_client import SoulseekWrapper

logger = logging.getLogger("slsk_mcp")
logging.basicConfig(level=logging.INFO, stream=sys.stderr)

# ── Singleton client ─────────────────────────────────────────────────────────
_W = SoulseekWrapper()


async def _connect() -> None:
    """Ensure the singleton is connected. Idempotent — safe to call many times."""
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


# ── MCP server (no lifespan) ─────────────────────────────────────────────────
mcp = FastMCP("slsk")


# ── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
async def search(
    query: str,
    timeout: int = 7,
    extensions: Optional[list[str]] = None,
    max_results: int = 50,
) -> dict:
    """Search the Soulseek network for files."""
    try:
        await _connect()
    except RuntimeError as exc:
        return ErrorResponse(code="not_authenticated", message=str(exc)).model_dump()

    if timeout < 7:
        timeout = 7

    try:
        results = await _W.search(
            query=query,
            timeout=timeout,
            extensions=extensions,
            max_results=max_results,
        )
        return SearchResponse(count=len(results), results=results).model_dump()
    except Exception as exc:
        return ErrorResponse(code="network_error", message=str(exc)).model_dump()


@mcp.tool()
async def download(id: str, output_dir: Optional[str] = None) -> dict:
    """Download a file from a Soulseek peer."""
    try:
        await _connect()
    except RuntimeError as exc:
        return ErrorResponse(code="not_authenticated", message=str(exc)).model_dump()

    try:
        ok, message, local_path, filesize = await _W.download(id, output_dir)
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
    """Poll progress of an active or recent download."""
    try:
        await _connect()
    except RuntimeError as exc:
        return ErrorResponse(code="not_authenticated", message=str(exc)).model_dump()

    return _W.download_status(id).model_dump()


@mcp.tool()
async def cancel_download(id: str) -> dict:
    """Abort an in-progress download."""
    try:
        await _connect()
    except RuntimeError as exc:
        return ErrorResponse(code="not_authenticated", message=str(exc)).model_dump()

    status = await _W.cancel_download(id)
    return CancelDownloadResponse(status=status).model_dump()


# ── Resources ────────────────────────────────────────────────────────────────


@mcp.resource("slsk://status")
def get_status() -> str:
    """Current connection state, username, passive mode flag."""
    return json.dumps(_W.connection_status())


@mcp.resource("slsk://downloads")
def get_downloads() -> str:
    """List of all tracked downloads with status/progress."""
    return json.dumps(_W.all_downloads())


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
