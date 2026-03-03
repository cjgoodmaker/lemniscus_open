#!/usr/bin/env bash
# Build lemniscus.mcpb — a drag-and-drop Desktop Extension for Claude Desktop.
# Usage: ./build_mcpb.sh
set -euo pipefail

BUNDLE_NAME="lemniscus.mcpb"
STAGING_DIR=$(mktemp -d)

echo "Staging bundle in $STAGING_DIR ..."

# Copy manifest
cp manifest.json "$STAGING_DIR/"

# Copy pyproject.toml (uv reads deps from here)
cp pyproject.toml "$STAGING_DIR/"

# Copy server source
cp server.py "$STAGING_DIR/"
cp db.py "$STAGING_DIR/"
cp embedder.py "$STAGING_DIR/"
cp models.py "$STAGING_DIR/"
cp retrieval.py "$STAGING_DIR/"
cp pipeline.py "$STAGING_DIR/"
cp chunker.py "$STAGING_DIR/"
cp download_model.py "$STAGING_DIR/"

# Copy parsers package
mkdir -p "$STAGING_DIR/parsers"
cp parsers/*.py "$STAGING_DIR/parsers/"

# Copy icon
cp logo_1.png "$STAGING_DIR/icon.png"

# Copy embedding model
cp minilm.onnx "$STAGING_DIR/"
cp tokenizer.json "$STAGING_DIR/"

# Build the zip (mcpb is just a zip)
rm -f "$BUNDLE_NAME"
(cd "$STAGING_DIR" && zip -r - .) > "$BUNDLE_NAME"

rm -rf "$STAGING_DIR"

echo ""
echo "Built $BUNDLE_NAME ($(du -h "$BUNDLE_NAME" | cut -f1))"
echo "Drag this file into Claude Desktop → Settings → Extensions to install."
