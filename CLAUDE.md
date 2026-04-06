# slsk-mcp

Soulseek MCP server for Claude Desktop / Claude Code.

## Development workflow

- After any code change, you MUST: commit, push to GitHub, and update the pinned commit hash in `~/Library/Application Support/Claude/claude_desktop_config.json` under `mcpServers.local_music_finder.args` (the `git+https://...@<hash>` value). Claude Desktop must be restarted to pick up the new version.
- The MCP is installed via `uvx --from git+https://github.com/voidtype/slsk_mcp.git@<commit> slsk-mcp` — there is no PyPI release.

## Key files

- `src/slsk_mcp/server.py` — MCP tool definitions (search, download, download_status, etc.)
- `src/slsk_mcp/slsk_client.py` — Soulseek wrapper (login, download management, connection health)
- `src/slsk_mcp/models.py` — Pydantic response models

## MCP tools for the AI

### `get_config`
Returns runtime settings (download directory, listen port, concurrency limits, username). Call this when you need to know where files are saved or what the current configuration is. No arguments required. Does not require a connection.

### `.part` file convention
Downloads are written as `filename.flac.part` during transfer. The `.part` suffix is removed only on successful completion. If a file still has `.part`, the download failed or is still in progress — do not treat it as a finished file.

## Testing

- No test suite currently; verify by restarting Claude Desktop and calling `connection_health`.
