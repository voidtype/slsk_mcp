"""MCP server entry point for slsk-mcp."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .models import (
    ErrorResponse,
    LoginResponse,
    LogoutResponse,
    SearchResponse,
    DownloadResponse,
    DownloadStatusResponse,
    CancelDownloadResponse,
)
from .slsk_client import SoulseekWrapper

logger = logging.getLogger("slsk_mcp")
logging.basicConfig(level=logging.INFO)

# Single module-level client — used directly by ALL tools and resources
_client = SoulseekWrapper()


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Manage the Soulseek client lifecycle."""
    # Block on auto-login so server is ready before accepting tool calls
    username = os.environ.get("SLSK_USERNAME")
    password = os.environ.get("SLSK_PASSWORD")
    if username and password:
        logger.info("Auto-login with SLSK_USERNAME=%s", username)
        try:
            ok, msg, passive = await _client.login(username, password)
            if ok:
                logger.info("Auto-login succeeded (passive=%s)", passive)
            else:
                logger.error("Auto-login failed: %s", msg)
        except Exception as exc:
            logger.error("Auto-login error: %s", exc)

    logger.info("Lifespan ready: _client.connected=%s id=%s", _client.connected, id(_client))
    try:
        yield
    finally:
        await _client.logout()


mcp = FastMCP("slsk", lifespan=app_lifespan)


async def _require_auth() -> Optional[dict]:
    """Check connection state. No destructive retry — just reports status."""
    if _client.connected:
        return None
    return ErrorResponse(
        code="not_authenticated",
        message="Not authenticated. Call login first or set SLSK_USERNAME/SLSK_PASSWORD env vars.",
    ).model_dump()


# ── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
async def login(username: str, password: str) -> dict:
    """Authenticate to the Soulseek network."""
    try:
        ok, message, passive = await _client.login(username, password)
        if ok:
            return LoginResponse(
                status="ok", message=message, passive_mode=passive
            ).model_dump()
        else:
            return ErrorResponse(
                code="network_error", message=message
            ).model_dump()
    except Exception as exc:
        return ErrorResponse(
            code="network_error", message=str(exc)
        ).model_dump()


@mcp.tool()
async def logout() -> dict:
    """Disconnect from Soulseek. No parameters."""
    await _client.logout()
    return LogoutResponse(status="ok").model_dump()


@mcp.tool()
async def search(
    query: str,
    timeout: int = 7,
    extensions: Optional[list[str]] = None,
    max_results: int = 50,
) -> dict:
    """Search the Soulseek network for files."""
    err = await _require_auth()
    if err:
        return err

    if timeout < 7:
        timeout = 7

    try:
        results = await _client.search(
            query=query,
            timeout=timeout,
            extensions=extensions,
            max_results=max_results,
        )
        return SearchResponse(
            count=len(results),
            results=results,
        ).model_dump()
    except Exception as exc:
        return ErrorResponse(
            code="network_error", message=str(exc)
        ).model_dump()


@mcp.tool()
async def download(id: str, output_dir: Optional[str] = None) -> dict:
    """Download a file from a Soulseek peer."""
    err = await _require_auth()
    if err:
        return err

    try:
        ok, message, local_path, filesize = await _client.download(id, output_dir)
        if ok:
            return DownloadResponse(
                status="started",
                local_path=local_path,
                filesize=filesize,
                message=message,
            ).model_dump()
        else:
            return ErrorResponse(
                code="peer_timeout", message=message
            ).model_dump()
    except Exception as exc:
        return ErrorResponse(
            code="network_error", message=str(exc)
        ).model_dump()


@mcp.tool()
async def download_status(id: str) -> dict:
    """Poll progress of an active or recent download."""
    err = await _require_auth()
    if err:
        return err

    return _client.download_status(id).model_dump()


@mcp.tool()
async def cancel_download(id: str) -> dict:
    """Abort an in-progress download."""
    err = await _require_auth()
    if err:
        return err

    status = await _client.cancel_download(id)
    return CancelDownloadResponse(status=status).model_dump()


# ── Resources ────────────────────────────────────────────────────────────────


@mcp.resource("slsk://status")
def get_status() -> str:
    """Current connection state, username, passive mode flag."""
    return json.dumps(_client.connection_status())


@mcp.resource("slsk://downloads")
def get_downloads() -> str:
    """List of all tracked downloads with status/progress."""
    return json.dumps(_client.all_downloads())


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
