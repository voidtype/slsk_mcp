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
| `SLSK_MAX_CONCURRENT_SEARCH` | `4` | Parallel search ticket limit |
| `SLSK_MAX_CONCURRENT_OPS` | `1` | Max simultaneous socket operations |

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
| `search` | Search the network for files |
| `download` | Download a file from a peer |
| `download_status` | Poll download progress |
| `cancel_download` | Abort an in-progress download |
| `peer_status` | Check a peer's online status, speed, queue, and free slots |

## Resources

| URI | Description |
|-----|-------------|
| `slsk://status` | Connection state, username, passive mode |
| `slsk://downloads` | All tracked downloads with progress |

## Tips & Tricks

Soulseek is a peer-to-peer network — there is no central file server. Every file lives on someone else's computer. These tips (drawn from how clients like Nicotine+, SoulseekQt, and Museek operate) will help you get reliable results.

### Searching

- **Be specific with queries.** `"Aphex Twin Drukqs flac"` will return far better results than `"electronic music"`. Include the artist, album, and format when you know them.
- **Use the `extensions` filter.** Pass `extensions: ["flac", "mp3"]` to skip unrelated file types (images, playlists, NFO files) that clutter results.
- **Increase `timeout` for rare content.** The default 7 seconds is fine for popular music but niche or obscure files need 15–30 seconds for peers to respond.
- **Search results are a snapshot.** They reflect who was online during that timeout window. If you don't find something, try again later — different peers will be online.
- **Look at `has_free_slots` first.** Results are sorted with free-slot peers on top. A peer with `has_free_slots: true` and high `avg_speed` is your best bet for an immediate download.

### Choosing Peers

- **Use `peer_status` before downloading.** Just because a peer appeared in search results doesn't mean they're still online or accepting transfers. Check `peer_status(username)` first.
- **Prefer peers with free upload slots.** `has_slots_free: true` means they can start sending immediately. `false` means you'll sit in their queue — sometimes for hours.
- **Check `queue_size`.** A peer with 200 files queued will take much longer to get to yours than one with 2.
- **Higher `avg_speed` ≠ faster for you.** Speed depends on the slowest link between you and the peer. But it's still a useful tiebreaker.
- **Try multiple peers for the same file.** If one is slow or stuck at 0%, cancel and grab it from someone else. The same file is usually shared by dozens of users.

### Downloading

- **Stuck at "queued" 0%?** The peer is online but hasn't gotten to your request yet (or can't connect to you). Wait a few minutes, then cancel and pick another peer.
- **Passive mode is normal.** If you're behind a NAT/firewall, the server falls back to passive mode. Downloads still work but are initiated differently — some very old clients may not support it.
- **Download from folders, not just singles.** On Soulseek, albums are typically shared as full folders. If you're grabbing a full album, look for files from the same user with the same parent directory.
- **Respect the network.** Share files back when you can. Soulseek is a community — users who only leech often get deprioritized in other users' queues.

### Troubleshooting

- **"Login failed" on startup?** Your credentials may be wrong, or the Soulseek server (`server.slsknet.org`) may be temporarily down. Check your `SLSK_USERNAME`/`SLSK_PASSWORD` env vars.
- **Empty search results?** Either the query is too specific, the timeout was too short, or very few peers share that content. Broaden the query or increase the timeout.
- **Downloads fail immediately?** The peer may have gone offline between your search and your download request. Use `peer_status` to verify they're still online.
- **Everything is slow?** Increase `SLSK_MAX_CONCURRENT_DL` (default 3) to download from more peers in parallel. Increase `SLSK_MAX_CONCURRENT_OPS` if searches feel serialized.

## Development

```bash
uv sync --extra dev
uv run pytest
```
