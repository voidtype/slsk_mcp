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
| `slsk://search_tips` | Actionable search & download strategies (sourced from Nicotine+, sldl, Soularr, SoulSync) |

## Tips & Tricks

Soulseek is a peer-to-peer network — every file lives on someone else's machine. These strategies are drawn from how [Nicotine+](https://nicotine-plus.org/), [sldl](https://github.com/fiso64/sldl), [Soularr](https://github.com/mrusse/soularr), and [SoulSync](https://github.com/Nezreka/SoulSync) handle search and download.

> **Tip:** These strategies are also available as structured data via the `slsk://search_tips` resource endpoint — designed for AI agents to read programmatically.

### Search Query Craft

Soulseek search works by tokenizing your query and matching against every shared filename and folder path on connected peers. The server-side algorithm is a simple word-intersection: **every word you send must appear somewhere in the file's path** for it to match.

- **Provide the least input that uniquely identifies the file.** `"Miles Davis Kind of Blue flac"` is better than `"jazz trumpet classic album"`. Include artist + album + format when known. ([sldl docs: "always best to provide the least input necessary to uniquely identify an album or song"](https://github.com/fiso64/sldl#tips))
- **Exclude junk with `-` (minus).** Nicotine+ supports `flac -live` to exclude live recordings, or `jazz -compilation` to skip compilations. The Soulseek protocol supports excluded terms — include `-term` directly in the query string. ([Nicotine+ search syntax docs](https://www.mintlify.com/nicotine-plus/nicotine-plus/features/search))
- **Search matches folder names and full file paths, not just filenames.** Searching `"experimental"` returns all files inside folders named "experimental". You can search by genre folder names, label names, or any part of the directory structure. ([Soulseek Wikipedia](https://en.wikipedia.org/wiki/Soulseek))
- **Drop "feat." and featured artists from queries.** Tools like sldl strip these with `--remove-ft` because featured artist credits vary wildly across file names and cause missed matches.
- **For "Various Artists" compilations, search by track name, not artist.** The artist field on compilations is unreliable. sldl recommends removing the artist entirely for VA releases.
- **Partial matching with `*` works only at the start of a word** in Nicotine+. `*trance` matches "psytrance" but `remix*` does not work. Keep this in mind if searches seem to miss results.

### Search Filters

All filters are applied post-search on the result set, the same way Nicotine+, sldl, and Soularr implement them. They're available as params on the `search` tool:

| Param | Type | Source | Example |
|-------|------|--------|---------|
| `extensions` | `list[str]` | Nicotine+ file type filter, Soularr `allowed_filetypes` | `["flac", "mp3"]` |
| `min_bitrate` | `int` (kbps) | sldl `pref-min-bitrate=200`, Nicotine+ bitrate filter | `200` or `320` |
| `min_filesize` | `int` (bytes) | Nicotine+ min file size filter | `1000000` (1 MB) |
| `max_filesize` | `int` (bytes) | Nicotine+ max file size filter | `500000000` (500 MB) |
| `free_slots_only` | `bool` | Nicotine+ "Free Slot" filter (most impactful for reliability) | `true` |
| `max_queue_size` | `int` | Soularr `maximum_peer_queue=50` | `50` |
| `min_speed` | `int` (bytes/s) | Soularr `minimum_peer_upload_speed`, Nicotine+ speed filter | `50000` (50 KB/s) |

**Note on `min_bitrate`:** Files with unknown bitrate are **kept**, not rejected. The standard SoulseekQt client does not broadcast bitrate info, so rejecting unknowns would exclude many valid files. This matches sldl's behavior. ([sldl docs on `--strict-conditions` caveat](https://github.com/fiso64/sldl#note-on-availability-of-metadata))

### Timeout & Timing

- **7 seconds is the minimum, not the ideal.** For popular music 7–10s is fine. For rare/obscure content, use `timeout=20` or `timeout=30`. sldl's `--fast-search` mode exits early when a good match is found, but for broad discovery you want a longer window.
- **Search results are a snapshot of who's online right now.** Different peers are online at different times of day. Nicotine+'s wishlist feature re-runs searches every 90–120 minutes for this reason. If you don't find something, try again later.
- **Peak hours yield more results.** The Nicotine+ docs note that searching during peak hours (evenings in the US/EU) means more peers online and more results. Off-peak searches for niche content may come back empty.

### Choosing Peers (the key to avoiding "stuck at queued")

This is the single most important part. A file appearing in search results **does not mean the peer will serve it to you**. Tools like Soularr and SoulSync score peers before downloading.

- **`has_free_slots: true` is the #1 signal.** Nicotine+ lets you filter results to only show users with free upload slots. Use `free_slots_only=true` on search, or check the field in results. If `has_free_slots` is `false`, you'll sit in their queue — potentially for hours.
- **Check `queue_size`.** Soularr rejects peers with queue sizes above a threshold (`maximum_peer_queue = 50` by default). Use `max_queue_size=50` on search to apply the same filter.
- **Check `avg_speed`.** Soularr sets a `minimum_peer_upload_speed` floor. Use `min_speed=50000` to filter. But remember: your actual download speed is the **slowest link** between you and the peer ([WikiHow Soulseek guide](https://www.wikihow.com/Optimize-Soulseek-for-Downloading-Music)). Use speed as a tiebreaker, not a guarantee.
- **Use `peer_status` before committing.** The peer was online during search but may have gone offline since. `peer_status(username)` queries their current state from the server — check for `status: "online"` and `has_slots_free: true` before downloading.
- **For full albums, pick one peer for all tracks.** SoulSync calls this "source reuse for album consistency" — downloading an entire album from the same user ensures consistent encoding, tagging, and folder structure rather than getting a Frankenstein album from 12 different rippers.
- **Some peers can't connect to you (and vice versa).** If you're behind NAT and so is the peer, neither side can initiate a direct connection. This is the most common cause of permanent "queued" with no position number. The only fix is to try a different peer. ([Soulseek FAQ](https://www.slsknet.org/news/faq-page))

### Downloading Strategies

- **Stuck at "queued" with no queue position?** This almost always means a connectivity issue — the peer can't reach you, or you can't reach them. Cancel and try the next peer sharing the same file. ([r/Soulseek: common advice across dozens of "stuck at queued" threads](https://www.reddit.com/r/Soulseek/))
- **Set a stale timeout.** Soularr uses `stalled_timeout = 3600` (1 hour) to abort downloads that aren't progressing. sldl uses `--max-stale-time 30` for faster iteration. Poll `download_status` periodically; if `progress_pct` hasn't moved, call `cancel_download` and retry from the next peer.
- **Don't be afraid to cancel and retry from a different peer.** The same file is usually shared by dozens of users. Canceling a stalled download and picking the next result is the standard workflow in every Soulseek client.
- **For quality, prefer FLAC but accept fallbacks.** sldl's default strategy is to *prefer* lossless (`pref-format = flac,wav`) but still accept lossy if lossless isn't available, with a minimum bitrate preference of 200 kbps. Search first with `extensions=["flac"]`; if `count=0`, retry with `extensions=["flac","mp3"]`.
- **Don't queue tons of files from one user.** Soulseek etiquette: stick to 1–2 albums at a time per user. Queuing too much may get you banned. Spread downloads across multiple peers. ([WikiHow ban avoidance guide](https://www.wikihow.com/Avoid-Being-Banned-on-Soulseek))
- **Passive mode is normal.** If you're behind NAT without port forwarding, the server falls back to passive mode. Downloads still work but are relay-negotiated. Some very old clients may not support it. UPnP can help if your router supports it ([WikiHow](https://www.wikihow.com/Optimize-Soulseek-for-Downloading-Music)).

### Troubleshooting

- **Empty search results?** Check: (1) query too specific — try fewer words, (2) timeout too short — increase to 15–20s, (3) search term matches an excluded phrase, (4) very few peers share this content — try again at peak hours. ([Nicotine+ troubleshooting](https://www.mintlify.com/nicotine-plus/nicotine-plus/features/search))
- **"Login failed" on startup?** Credentials wrong, or `server.slsknet.org` temporarily down. Verify `SLSK_USERNAME`/`SLSK_PASSWORD`. Note: Soulseek recycles usernames after 30 days of inactivity. ([Soulseek FAQ](https://www.slsknet.org/news/faq-page))
- **Everything is slow?** Increase `SLSK_MAX_CONCURRENT_DL` to download from more peers in parallel. Close other bandwidth-heavy apps. If your upload is saturated (someone downloading from you), it can throttle your downloads too.
- **Getting banned?** Some users ban leechers. The Soulseek community expects you to share files back. Users with no shared files or very slow upload speeds may be deprioritized or banned by individual peers. ([WikiHow](https://www.wikihow.com/Avoid-Being-Banned-on-Soulseek))

## Development

```bash
uv sync --extra dev
uv run pytest
```
