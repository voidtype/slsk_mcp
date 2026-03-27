# slsk-mcp — Soulseek MCP Server Specification
 
A standalone [Model Context Protocol](https://modelcontextprotocol.io) server that gives any MCP-capable AI assistant (Claude, Windsurf, etc.) the ability to **log in to Soulseek, search the network, and download files** — all through simple tool calls.
 
Built on [`aioslsk`](https://pypi.org/project/aioslsk/) 1.4.x.
 
---
 
## 1  Transport
 
| Property | Value |
|----------|-------|
| Transport | `stdio` (default) or `sse` |
| Runtime | Python ≥ 3.10, asyncio |
| Key dependency | `aioslsk ==1.4.1` |
| Package manager | `uv` or `pip` — ship a `pyproject.toml` |
 
The server is a single long-lived process. The Soulseek TCP session stays open between tool calls so searches and downloads share one authenticated connection.
 
---
 
## 2  Auth Model
 
Soulseek credentials are **never hardcoded**. Two options, checked in order:
 
| Priority | Method | Details |
|----------|--------|---------|
| 1 | Environment variables | `SLSK_USERNAME`, `SLSK_PASSWORD` |
| 2 | Explicit `login` tool call | Agent supplies credentials at runtime |
 
### Lifecycle
 
[not connected] ──login──▶ [connected] ──logout──▶ [not connected] │ search / download

 
- On startup, if env vars are set, the server **auto-connects** (fire-and-forget; first tool call blocks until ready).
- If env vars are absent, any tool call other than `login` returns an error: `"Not authenticated. Call login first."`
- `logout` tears down the TCP session. A new `login` can follow.
- The server falls back to **passive mode** (skip listening ports) if the initial connection attempt fails due to firewall/NAT, matching the existing SpotSeek behaviour.
 
### Network tuning (optional env vars)
 
| Var | Default | Purpose |
|-----|---------|---------|
| `SLSK_LISTEN_PORT` | aioslsk default | Soulseek listening port |
| `SLSK_OBFUSCATED_PORT` | aioslsk default | Obfuscated port |
| `SLSK_SEARCH_TIMEOUT` | `7` | Seconds to wait for search results |
| `SLSK_DOWNLOAD_DIR` | `./downloads` | Default download output directory |
| `SLSK_MAX_CONCURRENT_DL` | `3` | Parallel download limit |
 
---
 
## 3  Tools
 
### 3.1  `login`
 
Authenticate to the Soulseek network.
 
```jsonc
// request
{
  "username": "string",   // required
  "password": "string"    // required
}
 
// response
{
  "status": "ok" | "error",
  "message": "string",          // human-readable
  "passive_mode": false          // true if listening ports were skipped
}
Tears down any existing session first (safe to call repeatedly).
Returns structured error on bad credentials or network failure.
3.2 logout
Disconnect from Soulseek. No parameters.

jsonc
// response
{ "status": "ok" }
3.3 search
Search the Soulseek network for files.

jsonc
// request
{
  "query": "string",                       // required — free-text search
  "timeout": 7,                            // optional — seconds to collect results (min 7)
  "extensions": ["flac", "mp3"],           // optional — only return these file types
  "max_results": 50                        // optional — cap returned results (default 50)
}
 
// response
{
  "count": 42,
  "results": [
    {
      "id": "user123:/path/to/Song.flac",  // opaque handle, pass to download
      "username": "user123",
      "filename": "Music/Artist/Song.flac",
      "filesize": 34567890,
      "extension": "flac",
      "bitrate": null,
      "sample_rate": 44100,
      "bit_depth": 16,
      "duration_sec": 243,
      "audio_quality": 1411
    }
    // …
  ]
}
Results are sorted by audio quality descending (lossless first, then by bitrate).
Each result carries an id that encodes username:remote_path — this is the handle for download.
3.4 download
Download a file from a Soulseek peer.

jsonc
// request
{
  "id": "user123:/path/to/Song.flac",     // required — from search result
  "output_dir": "/home/me/music"           // optional — defaults to SLSK_DOWNLOAD_DIR or cwd
}
 
// response (immediate — transfer starts in background)
{
  "status": "started" | "error",
  "local_path": "/home/me/music/Song.flac",
  "filesize": 34567890,
  "message": "string"
}
output_dir is created if it doesn't exist.
If output_dir is omitted: uses SLSK_DOWNLOAD_DIR env var, or the server process's working directory.
Filename collisions are resolved by appending _1, _2, etc.
The file is written to disk by aioslsk; the tool returns as soon as the transfer is initiated (not completed).
3.5 download_status
Poll progress of an active or recent download.

jsonc
// request
{
  "id": "user123:/path/to/Song.flac"      // required — same id used in download
}
 
// response
{
  "status": "queued" | "downloading" | "finished" | "failed" | "cancelled" | "not_found",
  "progress_pct": 73.2,
  "received_bytes": 25300000,
  "total_bytes": 34567890,
  "speed_bps": 1048576,
  "local_path": "/home/me/music/Song.flac"
}
3.6 cancel_download
Abort an in-progress download.

jsonc
// request
{
  "id": "user123:/path/to/Song.flac"
}
 
// response
{
  "status": "cancelled" | "not_found"
}
4 Resources (read-only context)
URI	Description
slsk://status	Current connection state, username, passive mode flag
slsk://downloads	List of all tracked downloads with status/progress
Resources let the agent inspect state without a tool call side-effect.

5 Error Handling
All tools return structured JSON. Errors use this shape:

jsonc
{
  "status": "error",
  "code": "not_authenticated" | "network_error" | "peer_timeout" | "invalid_params",
  "message": "Human-readable explanation"
}
The server never throws unhandled exceptions to the transport layer.

6 Typical Agent Workflow
Agent                          slsk-mcp
  │                               │
  ├── login(user, pass) ─────────▶│  ← or auto-login via env
  │◀── { status: "ok" } ─────────┤
  │                               │
  ├── search("Radiohead OK Computer FLAC") ──▶│
  │◀── { results: [...] } ────────┤
  │                               │
  │  (agent picks best result)    │
  │                               │
  ├── download(id, output_dir) ──▶│
  │◀── { status: "started" } ─────┤
  │                               │
  ├── download_status(id) ────────▶│  ← poll until finished
  │◀── { status: "finished" } ────┤
  │                               │
  ├── logout ─────────────────────▶│
  │◀── { status: "ok" } ──────────┤
7 Project Structure
slsk-mcp/
├── pyproject.toml          # deps: aioslsk, mcp-sdk, pydantic
├── README.md
├── src/
│   └── slsk_mcp/
│       ├── __init__.py
│       ├── server.py       # MCP server entry point
│       ├── slsk_client.py  # aioslsk wrapper (login, search, download, progress)
│       └── models.py       # Pydantic schemas for tool I/O
└── tests/
    ├── test_client.py
    └── test_server.py
8 Claude / Windsurf Integration
Add to MCP config (e.g. ~/.cursor/mcp.json or Windsurf global settings):

jsonc
{
  "mcpServers": {
    "slsk": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/slsk-mcp", "src/slsk_mcp/server.py"],
      "env": {
        "SLSK_USERNAME": "your_username",
        "SLSK_PASSWORD": "your_password",
        "SLSK_DOWNLOAD_DIR": "/home/you/music"
      }
    }
  }
}
With env vars set, the agent can immediately search and download without an explicit login call.

9 Implementation Notes
Single session: One SoulSeekClient instance lives for the server's lifetime. login/logout cycle it.
Passive fallback: If listening port bind fails (firewall/NAT), retry with ListeningConnectionErrorMode.CLEAR and patched connect_listening_ports (proven pattern from SpotSeek).
Search wait floor: Enforce minimum 7s wait for results — SLSK network is slow; shorter waits return empty.
Progress tracking: Store (username, remote_path) → progress_dict in memory. Clean up finished entries after 60s.
File attributes: Map aioslsk FileData.attributes keys: 0=audio_quality, 1=duration_sec, 4=sample_rate, 5=bit_depth.
No shares required: Share scanning is optional. The MCP is primarily a consumer, not a sharer. Can be added later.
Concurrency: Semaphore-limited parallel downloads (default 3). Searches limited to 4 concurrent.