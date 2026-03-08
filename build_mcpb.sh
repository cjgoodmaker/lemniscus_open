#!/usr/bin/env bash
# Build lemniscus.mcpb — Node.js Apple Health extension for Claude Desktop.
# Uses Claude Desktop's built-in Node.js runtime — no bundled runtime needed.
# Usage: ./build_mcpb.sh
set -euo pipefail

BUNDLE_NAME="lemniscus.mcpb"
STAGING_DIR=$(mktemp -d)

echo "Staging bundle in $STAGING_DIR ..."

# Manifest
cp manifest.json "$STAGING_DIR/"

# Server source + dependencies
cp -R server "$STAGING_DIR/server"

# Icon
cp docs/img/logo_1.png "$STAGING_DIR/icon.png"

# Build zip
rm -f "$BUNDLE_NAME"
echo "Compressing bundle..."
(cd "$STAGING_DIR" && zip -r -q - .) > "$BUNDLE_NAME"

rm -rf "$STAGING_DIR"

echo ""
echo "Built $BUNDLE_NAME ($(du -h "$BUNDLE_NAME" | cut -f1))"
echo "Drag into Claude Desktop → Settings → Extensions to install."
