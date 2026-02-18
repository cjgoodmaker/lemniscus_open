# Lemniscus Bantom

MCP-only health data server for Claude Code. No UI, no HTTP server — just drop your health files in `data/` and query them through Claude.

## Prerequisites

- **macOS** (Apple Silicon or Intel)
- **Python 3.11+** — check with `python3 --version`. If missing: `brew install python@3.12`
- **Claude Code** — install from https://docs.anthropic.com/en/docs/claude-code

## Quick Start

### 1. Clone and run setup

```bash
git clone https://github.com/cjgoodmaker/lemniscus_server_bantom.git
cd lemniscus_server_bantom
bash setup.sh
```

This creates a virtual environment, installs dependencies, and downloads the AI embedding model (~90MB).

### 2. Create your account

```bash
.venv/bin/python server.py signup
```

Enter your email and password. You'll receive a confirmation email — click the link, then sign in:

```bash
.venv/bin/python server.py login
```

### 3. Add your health data

Copy your health files into the `data/` folder:

```bash
# Apple Health export (from iPhone: Health app → Profile → Export All Health Data)
cp ~/Downloads/export.xml data/

# Lab reports, medical records
cp ~/Documents/bloodwork.pdf data/

# Wearable exports
cp ~/Downloads/oura_export.json data/

# Clinical photos, notes, etc.
cp ~/Pictures/mole_check.jpg data/
cp ~/Documents/health_notes.txt data/
```

### 4. Index your data

```bash
.venv/bin/python server.py index
```

You'll see progress in the terminal as each file is parsed and embedded. Large Apple Health exports (~600MB XML) take about 2 minutes.

### 5. Start Claude Code

```bash
claude
```

That's it. The MCP server starts automatically (configured via `.mcp.json` in this directory). Ask Claude anything about your health data:

- "What were my heart rate trends last month?"
- "Summarize my lab results"
- "How was my sleep in January?"
- "How many steps did I average per day in 2024?"

## CLI Commands

All commands are run from the `lemniscus_server_bantom` directory:

| Command | Description |
|---------|-------------|
| `.venv/bin/python server.py signup` | Create a new account |
| `.venv/bin/python server.py login` | Sign in with existing account |
| `.venv/bin/python server.py index` | Index files in `data/` with visible progress |
| `.venv/bin/python server.py status` | Show auth status and what's been indexed |
| `.venv/bin/python server.py logout` | Remove stored credentials |

## Supported File Types

| Type | Extension | Description |
|------|-----------|-------------|
| Apple Health | `.xml` | iPhone Health export (auto-aggregates 1M+ readings into daily summaries) |
| Oura | `.json` | Oura Ring export (detected by filename or content) |
| Garmin | `.json` | Garmin Connect export (detected by filename or content) |
| PDF | `.pdf` | Lab reports, medical records (text extracted per page) |
| Images | `.png` `.jpg` `.jpeg` `.heic` | Clinical photos, screenshots |
| Text | `.txt` `.md` `.csv` | Notes, logs, any text content |
| JSON | `.json` | Generic health data (auto-detects provider format) |

## Adding Files Mid-Session

Drop new files into `data/` while Claude Code is running, then ask Claude to "reindex my health data" — it picks up new files without restarting.

## Using From Another Project

To access your health data from any other project directory, add this to that project's `.mcp.json` (replace the path with your actual install location):

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
3. Files are parsed, chunked, embedded (384-dim MiniLM vectors), and stored in SQLite
4. Search combines semantic (vector) + keyword (FTS5) matching with temporal decay
5. The MCP server communicates with Claude Code via stdio — all data stays local on your machine
