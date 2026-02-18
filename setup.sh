#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  ╔═══════════════════════════════╗"
echo "  ║     Lemniscus Bantom Setup    ║"
echo "  ╚═══════════════════════════════╝"
echo ""

# ── Step 1: Python check ──────────────────────────────────────────────
echo "[1/4] Checking Python..."
if ! command -v python3 &>/dev/null; then
    echo ""
    echo "  ✗ Python 3 not found."
    echo "  Install it with:  brew install python@3.12"
    echo "  Then re-run:      bash setup.sh"
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo ""
    echo "  ✗ Python $PY_VERSION found, but 3.11+ is required."
    echo "  Install it with:  brew install python@3.12"
    exit 1
fi
echo "  ✓ Python $PY_VERSION"

# ── Step 2: Virtual environment + dependencies ────────────────────────
echo "[2/4] Setting up environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "  ✓ Created virtual environment"
else
    echo "  ✓ Virtual environment exists"
fi

.venv/bin/pip install -q -r requirements.txt
echo "  ✓ Dependencies installed"

# ── Step 3: Download AI model ─────────────────────────────────────────
echo "[3/4] Checking AI model..."
if [ ! -f "minilm.onnx" ] || [ ! -f "tokenizer.json" ]; then
    echo "  Downloading embedding model (~90MB)..."
    .venv/bin/python download_model.py
fi
echo "  ✓ Model ready"

# ── Step 4: Create data folder ────────────────────────────────────────
echo "[4/4] Preparing data folder..."
mkdir -p data
echo "  ✓ data/ folder ready"

echo ""
echo "  ╔═══════════════════════════════════════════════════════════╗"
echo "  ║                    Setup complete!                       ║"
echo "  ╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "  Follow these steps to get started:"
echo ""
echo "  ┌─────────────────────────────────────────────────────────┐"
echo "  │ STEP 1: Create your account                            │"
echo "  │                                                         │"
echo "  │   .venv/bin/python server.py signup                     │"
echo "  │                                                         │"
echo "  │ (or if you already have one: server.py login)           │"
echo "  └─────────────────────────────────────────────────────────┘"
echo ""
echo "  ┌─────────────────────────────────────────────────────────┐"
echo "  │ STEP 2: Add your health data                            │"
echo "  │                                                         │"
echo "  │   cp ~/path/to/export.xml data/                         │"
echo "  │   cp ~/path/to/labwork.pdf data/                        │"
echo "  │                                                         │"
echo "  │ Supported: Apple Health XML, PDFs, Oura/Garmin JSON,    │"
echo "  │            images (.png/.jpg), text files (.txt/.csv)   │"
echo "  └─────────────────────────────────────────────────────────┘"
echo ""
echo "  ┌─────────────────────────────────────────────────────────┐"
echo "  │ STEP 3: Open Claude Code in this directory              │"
echo "  │                                                         │"
echo "  │   claude                                                │"
echo "  │                                                         │"
echo "  │ (run from this folder — the MCP server starts           │"
echo "  │  automatically via .mcp.json)                           │"
echo "  │                                                         │"
echo "  │ Ask Claude things like:                                 │"
echo "  │   'What are my heart rate trends?'                      │"
echo "  │   'Summarize my lab results'                            │"
echo "  │   'How was my sleep last month?'                        │"
echo "  └─────────────────────────────────────────────────────────┘"
echo ""
echo "  To use from another project, copy this into that"
echo "  project's .mcp.json:"
echo ""
echo "  {"
echo "    \"mcpServers\": {"
echo "      \"lemniscus\": {"
echo "        \"command\": \"${SCRIPT_DIR}/.venv/bin/python\","
echo "        \"args\": [\"${SCRIPT_DIR}/server.py\"]"
echo "      }"
echo "    }"
echo "  }"
echo ""
