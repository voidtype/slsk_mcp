# slsk-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server that gives any MCP-capable AI assistant the ability to **log in to Soulseek, search the network, and download files** — all through simple tool calls.

Built on [aioslsk](https://pypi.org/project/aioslsk/) 1.4.x.

## Quick Start

```bash
# Install dependencies
uv sync

# Run the server (stdio transport)
uv run python -m slsk_mcp.server
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SLSK_USERNAME` | — | Auto-login username |
| `SLSK_PASSWORD` | — | Auto-login password |
| `SLSK_DOWNLOAD_DIR` | `./downloads` | Default download directory |
| `SLSK_LISTEN_PORT` | aioslsk default | Soulseek listening port |
| `SLSK_OBFUSCATED_PORT` | aioslsk default | Obfuscated port |
| `SLSK_SEARCH_TIMEOUT` | `7` | Seconds to wait for search results |
| `SLSK_MAX_CONCURRENT_DL` | `3` | Parallel download limit |

## MCP Integration

Add to your MCP config (e.g. Windsurf global settings or `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "slsk": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/slsk-mcp", "python", "-m", "slsk_mcp.server"],
      "env": {
        "SLSK_USERNAME": "your_username",
        "SLSK_PASSWORD": "your_password",
        "SLSK_DOWNLOAD_DIR": "/home/you/music"
      }
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `login` | Authenticate to Soulseek |
| `logout` | Disconnect |
| `search` | Search the network for files |
| `download` | Download a file from a peer |
| `download_status` | Poll download progress |
| `cancel_download` | Abort an in-progress download |

## Resources

| URI | Description |
|-----|-------------|
| `slsk://status` | Connection state, username, passive mode |
| `slsk://downloads` | All tracked downloads with progress |

## Development

```bash
uv sync --extra dev
uv run pytest
```
