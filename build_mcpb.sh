#!/usr/bin/env bash
# Build lemniscus.mcpb — zero-dependency Apple Health extension for Claude Desktop.
# Bundles standalone Python + only mcp + lxml dependencies.
# Usage: ./build_mcpb.sh
set -euo pipefail

BUNDLE_NAME="lemniscus.mcpb"
STAGING_DIR=$(mktemp -d)
SITE_PACKAGES=".venv/lib/python3.13/site-packages"
STANDALONE_PYTHON="$HOME/.local/share/uv/python/cpython-3.13.12-macos-aarch64-none"

echo "Staging bundle in $STAGING_DIR ..."

# Manifest + metadata
cp manifest.json pyproject.toml "$STAGING_DIR/"

# Server source (just 3 files + parser)
cp server.py db.py "$STAGING_DIR/"
mkdir -p "$STAGING_DIR/parsers"
cp parsers/__init__.py parsers/apple_health.py "$STAGING_DIR/parsers/"

# Icon
cp logo_1.png "$STAGING_DIR/icon.png"

# Bundle standalone Python
echo "Bundling standalone Python..."
cp -R "$STANDALONE_PYTHON" "$STAGING_DIR/python"

# Bundle only needed site-packages (mcp + lxml + their deps)
echo "Bundling dependencies..."
LIB_DIR="$STAGING_DIR/lib"
mkdir -p "$LIB_DIR"

PACKAGES=(
    annotated_types anyio attr attrs
    certifi click dotenv
    h11 httpcore httpx httpx_sse
    idna
    jsonschema jsonschema_specifications
    lxml
    markdown_it_py mdurl
    mcp
    pygments
    pydantic pydantic_core pydantic_settings python_multipart
    referencing rich rpds
    sniffio sse_starlette starlette
    typing_extensions.py typing_inspection
    uvicorn
)

for pkg in "${PACKAGES[@]}"; do
    for match in "$SITE_PACKAGES"/$pkg; do
        if [ -e "$match" ]; then
            cp -R "$match" "$LIB_DIR/"
        fi
    done
done

# Copy .dist-info metadata (needed for importlib.metadata.version())
DIST_INFOS=(
    annotated_types anyio attrs
    certifi click dotenv python_dotenv
    h11 httpcore httpx httpx_sse
    idna
    jsonschema jsonschema_specifications
    lxml
    markdown_it_py mdurl
    mcp
    pygments
    pydantic pydantic_core pydantic_settings python_multipart
    referencing rich rpds
    sniffio sse_starlette starlette
    typing_extensions typing_inspection
    uvicorn
)
for pkg in "${DIST_INFOS[@]}"; do
    for match in "$SITE_PACKAGES"/${pkg}*.dist-info; do
        if [ -d "$match" ]; then
            cp -R "$match" "$LIB_DIR/"
        fi
    done
done

# Launcher script
cat > "$STAGING_DIR/run.sh" << 'LAUNCHER'
#!/usr/bin/env bash
DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$DIR/lib:$PYTHONPATH"
exec "$DIR/python/bin/python3.13" "$DIR/server.py" "$@"
LAUNCHER
chmod +x "$STAGING_DIR/run.sh"

# Build zip
rm -f "$BUNDLE_NAME"
echo "Compressing bundle..."
(cd "$STAGING_DIR" && zip -r -q - .) > "$BUNDLE_NAME"

rm -rf "$STAGING_DIR"

echo ""
echo "Built $BUNDLE_NAME ($(du -h "$BUNDLE_NAME" | cut -f1))"
echo "Drag into Claude Desktop → Settings → Extensions to install."
