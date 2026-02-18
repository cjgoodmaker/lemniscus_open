# Lemniscus Bantom

MCP-only health data server for Claude Code power users. No UI, no HTTP server — just drop your health files in `data/` and query them through Claude.

## Quick Start

```bash
# 1. Clone and setup
git clone <repo-url>
cd lemniscus_server_bantom
bash setup.sh

# 2. Create an account (or login if you already have one)
.venv/bin/python server.py signup
# or
.venv/bin/python server.py login

# 3. Add your health data
cp ~/path/to/export.xml data/
cp ~/path/to/bloodwork.pdf data/

# 4. Open Claude Code in this directory — done!
#    The MCP server starts automatically via .mcp.json
```

## Requirements

- Python 3.11+
- ~300MB disk space (model + dependencies)

## CLI Commands

| Command | Description |
|---------|-------------|
| `python server.py signup` | Create a new account |
| `python server.py login` | Sign in with existing account |
| `python server.py logout` | Remove stored credentials |
| `python server.py status` | Show auth status and indexed file count |
| `python server.py` | Start MCP stdio server (used by Claude Code automatically) |

## Supported File Types

| Type | Extension | Description |
|------|-----------|-------------|
| Apple Health | `.xml` | Export from iPhone Health app (auto-aggregates daily) |
| Oura | `.json` | Oura Ring export (detected by filename or content) |
| Garmin | `.json` | Garmin Connect export (detected by filename or content) |
| PDF | `.pdf` | Lab reports, medical records (text extracted per page) |
| Images | `.png` `.jpg` `.jpeg` `.heic` | Clinical photos, screenshots |
| Text | `.txt` `.md` `.csv` | Notes, logs, any text content |
| JSON | `.json` | Generic health data (auto-detects provider) |

## MCP Tools

Once connected, Claude Code has access to these tools:

| Tool | Description |
|------|-------------|
| `retrieve_health_context` | Semantic search over all health data and documents |
| `browse_timeline` | Browse entries chronologically by date range |
| `query_health_readings` | Drill down into individual sensor readings |
| `list_sources` | List indexed data sources with record counts |
| `get_vault_file` | Get original file content (images, text) |
| `reindex` | Re-scan `data/` for new files without restarting |

## Example Queries

Once configured, ask Claude things like:

- "What were my heart rate trends last month?"
- "Summarize my sleep data from January"
- "What did my blood work lab results show?"
- "How many steps did I average per day in 2024?"
- "Show me my resting heart rate over time"

## Adding Files Mid-Session

You can drop new files into `data/` while Claude Code is running. Then ask Claude to "reindex my health data" — it will call the `reindex` tool to pick up new files without restarting.

## Using From Another Project

To access your health data from a different project, add this to that project's `.mcp.json`:

```json
{
  "mcpServers": {
    "lemniscus": {
      "command": "/absolute/path/to/lemniscus_server_bantom/.venv/bin/python",
      "args": ["/absolute/path/to/lemniscus_server_bantom/server.py"]
    }
  }
}
```

## How It Works

1. On startup, the server authenticates with your stored credentials
2. It scans `data/` and indexes any new or modified files
3. Files are parsed, chunked into segments, embedded (384-dim MiniLM vectors), and stored in SQLite
4. A `.indexed_files.json` manifest tracks what's been indexed — subsequent startups skip already-indexed files
5. The MCP server runs on stdio, responding to Claude Code's tool calls
6. Search combines semantic (vector) + keyword (FTS5) matching with temporal decay
