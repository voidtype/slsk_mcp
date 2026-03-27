"""MCP server entry point for slsk-mcp."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP

from .models import (
    ErrorResponse,
    LoginResponse,
    LogoutResponse,
    SearchResponse,
    SearchResultItem,
    DownloadResponse,
    DownloadStatusResponse,
    CancelDownloadResponse,
)
from .slsk_client import SoulseekWrapper

logger = logging.getLogger("slsk_mcp")
logging.basicConfig(level=logging.INFO)

# Module-level client so both tools (via ctx) and resources can access it
_client = SoulseekWrapper()


@dataclass
class AppContext:
    client: SoulseekWrapper


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage the Soulseek client lifecycle."""
    global _client

    # Auto-login if env vars are set
    username = os.environ.get("SLSK_USERNAME")
    password = os.environ.get("SLSK_PASSWORD")
    if username and password:
        logger.info("Auto-login with SLSK_USERNAME=%s", username)
        asyncio.get_event_loop().create_task(_auto_login(_client, username, password))

    try:
        yield AppContext(client=_client)
    finally:
        await _client.logout()


async def _auto_login(client: SoulseekWrapper, username: str, password: str) -> None:
    """Fire-and-forget auto-login from env vars."""
    try:
        ok, msg, passive = await client.login(username, password)
        if ok:
            logger.info("Auto-login succeeded (passive=%s)", passive)
        else:
            logger.error("Auto-login failed: %s", msg)
    except Exception as exc:
        logger.error("Auto-login error: %s", exc)


mcp = FastMCP("slsk", lifespan=app_lifespan)


def _get_client(ctx: Context) -> SoulseekWrapper:
    return ctx.request_context.lifespan_context.client


def _require_auth(client: SoulseekWrapper) -> Optional[dict]:
    """Return an error dict if not connected, else None."""
    if not client.connected:
        return ErrorResponse(
            code="not_authenticated",
            message="Not authenticated. Call login first.",
        ).model_dump()
    return None


# ── Tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
async def login(username: str, password: str, ctx: Context) -> dict:
    """Authenticate to the Soulseek network."""
    client = _get_client(ctx)
    try:
        ok, message, passive = await client.login(username, password)
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
async def logout(ctx: Context) -> dict:
    """Disconnect from Soulseek. No parameters."""
    client = _get_client(ctx)
    await client.logout()
    return LogoutResponse(status="ok").model_dump()


@mcp.tool()
async def search(
    query: str,
    ctx: Context,
    timeout: int = 7,
    extensions: Optional[list[str]] = None,
    max_results: int = 50,
) -> dict:
    """Search the Soulseek network for files."""
    client = _get_client(ctx)
    err = _require_auth(client)
    if err:
        return err

    if timeout < 7:
        timeout = 7

    try:
        results = await client.search(
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
async def download(id: str, ctx: Context, output_dir: Optional[str] = None) -> dict:
    """Download a file from a Soulseek peer."""
    client = _get_client(ctx)
    err = _require_auth(client)
    if err:
        return err

    try:
        ok, message, local_path, filesize = await client.download(id, output_dir)
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
async def download_status(id: str, ctx: Context) -> dict:
    """Poll progress of an active or recent download."""
    client = _get_client(ctx)
    err = _require_auth(client)
    if err:
        return err

    return client.download_status(id).model_dump()


@mcp.tool()
async def cancel_download(id: str, ctx: Context) -> dict:
    """Abort an in-progress download."""
    client = _get_client(ctx)
    err = _require_auth(client)
    if err:
        return err

    status = await client.cancel_download(id)
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
