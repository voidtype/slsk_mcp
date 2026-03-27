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

Add to your MCP config (e.g. Windsurf global settings or `~/.cursor/mcp.json`).

### Option A — Install from GitHub (no clone needed)

```json
{
  "mcpServers": {
    "slsk": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/voidtype/slsk_mcp.git", "slsk-mcp"],
      "env": {
        "SLSK_USERNAME": "your_username",
        "SLSK_PASSWORD": "your_password",
        "SLSK_DOWNLOAD_DIR": "/home/you/music"
      }
    }
  }
}
```

### Option B — Local clone

```bash
git clone https://github.com/voidtype/slsk_mcp.git
```

```json
{
  "mcpServers": {
    "slsk": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/slsk_mcp", "slsk-mcp"],
      "env": {
        "SLSK_USERNAME": "your_username",
        "SLSK_PASSWORD": "your_password",
        "SLSK_DOWNLOAD_DIR": "/home/you/music"
      }
    }
  }
}
```

### Option C — pip install from GitHub

```bash
pip install git+https://github.com/voidtype/slsk_mcp.git
```

```json
{
  "mcpServers": {
    "slsk": {
      "command": "slsk-mcp",
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
