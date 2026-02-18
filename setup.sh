#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Lemniscus Bantom Setup ==="
echo ""

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
else
    echo "Virtual environment already exists."
fi

echo "Installing dependencies..."
.venv/bin/pip install -q -r requirements.txt

# Download ONNX model if not present
if [ ! -f "minilm.onnx" ] || [ ! -f "tokenizer.json" ]; then
    echo "Downloading ONNX model and tokenizer (~90MB)..."
    .venv/bin/python download_model.py
else
    echo "Model files already present."
fi

# Create data directory
mkdir -p data

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Create an account or sign in:"
echo "     .venv/bin/python server.py signup"
echo "     .venv/bin/python server.py login"
echo ""
echo "  2. Drop your health data files into data/"
echo "     cp ~/path/to/export.xml data/"
echo "     cp ~/path/to/bloodwork.pdf data/"
echo ""
echo "  3. Open Claude Code in this directory — the MCP server starts automatically"
echo "     (configured via .mcp.json)"
