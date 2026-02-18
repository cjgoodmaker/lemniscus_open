"""Lemniscus Bantom — MCP-only health data server for Claude Code.

Usage:
    python server.py              Run MCP stdio server (requires auth)
    python server.py login        Sign in with email/password
    python server.py signup       Create a new account
    python server.py logout       Remove stored credentials
    python server.py status       Show auth and indexing status
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from getpass import getpass
from pathlib import Path
from typing import Any

# All logging to stderr — stdout is reserved for MCP JSON-RPC
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("lemniscus")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
MANIFEST_PATH = BASE_DIR / ".indexed_files.json"
SOURCE_ID = "local"

SUPPORTED_EXTENSIONS = {
    ".xml", ".json", ".pdf", ".png", ".jpg", ".jpeg", ".heic",
    ".txt", ".md", ".csv",
}


# ---------------------------------------------------------------------------
# File type detection (ported from lemniscus_server_light/server.py)
# ---------------------------------------------------------------------------

def _detect_source_type(file_path: Path) -> str:
    """Detect source type from file path."""
    name = file_path.name.lower()
    ext = file_path.suffix.lower()

    if ext == ".xml":
        return "apple_health"

    if ext == ".pdf":
        return "pdf"

    if ext in (".png", ".jpg", ".jpeg", ".heic"):
        return "image"

    if ext in (".txt", ".md"):
        return "text"

    if ext == ".json":
        if "oura" in name:
            return "oura"
        if "garmin" in name:
            return "garmin"
        try:
            with open(file_path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                if "sleep" in data and "daily_readiness" in data:
                    return "oura"
                if "activities" in data and "dailies" in data:
                    return "garmin"
        except Exception:
            pass
        return "generic_json"

    if ext == ".csv":
        return "text"

    if "oura" in name:
        return "oura"
    if "garmin" in name:
        return "garmin"

    return "text"


# ---------------------------------------------------------------------------
# Auto-indexing
# ---------------------------------------------------------------------------

def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, default=str))


def _scan_and_index(db, embedder) -> dict:
    """Scan data/ folder and index any new or modified files."""
    from pipeline import ingest_file

    DATA_DIR.mkdir(exist_ok=True)
    manifest = _load_manifest()
    indexed = 0
    skipped = 0
    errors = []

    for file_path in sorted(DATA_DIR.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if file_path.name.startswith("."):
            continue

        rel = str(file_path.relative_to(DATA_DIR))
        stat = file_path.stat()
        entry = manifest.get(rel)

        if entry and entry.get("size") == stat.st_size and entry.get("mtime") == stat.st_mtime:
            skipped += 1
            continue

        source_type = _detect_source_type(file_path)
        logger.info(f"Indexing {rel} as {source_type}...")

        try:
            with open(file_path, "rb") as f:
                result = ingest_file(
                    file=f,
                    source_type=source_type,
                    source_id=SOURCE_ID,
                    db=db,
                    embedder=embedder,
                    filename=file_path.name,
                )
            manifest[rel] = {
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "source_type": source_type,
                "records": result.get("records_stored", 0),
                "indexed_at": datetime.now().isoformat(),
            }
            indexed += 1
            logger.info(f"  -> {result.get('records_stored', 0)} records")
        except Exception as e:
            logger.error(f"  -> Failed: {e}")
            errors.append({"file": rel, "error": str(e)})

    _save_manifest(manifest)
    summary = {"indexed": indexed, "skipped": skipped, "errors": len(errors)}
    if errors:
        summary["error_details"] = errors
    return summary


# ---------------------------------------------------------------------------
# MCP server creation (adapted from lemniscus_server_light/mcp_server.py)
# ---------------------------------------------------------------------------

def create_server(db, embedder, data_dir: str) -> Any:
    """Create MCP server with health data tools."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.utilities.types import Image
    from mcp.types import TextContent
    from retrieval import search

    mcp = FastMCP("lemniscus-health-context")

    @mcp.tool()
    def retrieve_health_context(
        query: str,
        source_id: str = SOURCE_ID,
        top_k: int = 10,
        temporal_decay_halflife_days: int = 180,
        modalities: list[str] | None = None,
    ) -> str:
        """Search ALL health data, documents, and photos using natural language.

        Searches across ALL content types by default — do NOT filter by modality
        unless the user explicitly asks for a specific type. Lab reports, PDFs,
        and clinical documents are stored under the 'other' modality.

        Returns a context package with structured data grouped by modality,
        a temporal narrative, and provenance. Uses semantic + keyword search
        with RRF ranking and temporal decay.

        Args:
            query: Natural language query (e.g. 'recent heart rate trends', 'lab results', 'mole photo')
            source_id: Data source identifier (default: 'local')
            top_k: Number of results (default 10, max 100)
            temporal_decay_halflife_days: Recency bias half-life in days (default 180)
            modalities: Optional filter. Leave empty to search everything. Values: vitals, activity, sleep, body, nutrition, fitness, mindfulness, workout, other (documents/photos/PDFs)
        """
        result = search(
            query=query,
            source_id=source_id,
            db=db,
            embedder=embedder,
            top_k=min(max(1, top_k), 100),
            temporal_decay_halflife_days=temporal_decay_halflife_days,
            modalities=modalities,
        )
        return json.dumps(result, default=str)

    @mcp.tool()
    def browse_timeline(
        start: str | None = None,
        end: str | None = None,
        modality: str | None = None,
        limit: int = 50,
        source_id: str = SOURCE_ID,
    ) -> str:
        """Browse health and document timeline entries in chronological order.

        Use this for chronological exploration rather than semantic search.
        Includes all content types: health data, PDF pages, photos, text files.

        Args:
            start: Start of time range (ISO 8601, e.g. '2024-01-01T00:00:00')
            end: End of time range (ISO 8601)
            modality: Filter by: vitals, activity, sleep, body, nutrition, fitness, mindfulness, workout, other (documents/photos)
            limit: Maximum entries to return (1-500)
            source_id: Data source identifier (default: 'local')
        """
        start_dt = datetime.fromisoformat(start) if start else None
        end_dt = datetime.fromisoformat(end) if end else None
        limit = max(1, min(limit, 500))

        entries = db.list_timeline(
            source_id=source_id,
            start=start_dt,
            end=end_dt,
            modality=modality,
            limit=limit,
        )

        result = {
            "source_id": source_id,
            "count": len(entries),
            "entries": [
                {
                    "id": str(e.id),
                    "timestamp": e.timestamp.isoformat(),
                    "modality": e.modality.value,
                    "summary": e.summary,
                    "metadata": e.metadata,
                }
                for e in entries
            ],
        }
        return json.dumps(result, default=str)

    @mcp.tool()
    def query_health_readings(
        record_type: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 100,
        source_id: str = SOURCE_ID,
    ) -> str:
        """Query individual health readings for detailed drill-down.

        Use this when you need specific data points (e.g., individual heart rate
        readings, specific step counts). For broader questions, use
        retrieve_health_context instead.

        Args:
            record_type: HK type to filter (e.g. 'HKQuantityTypeIdentifierHeartRate')
            start: Start of time range (ISO 8601)
            end: End of time range (ISO 8601)
            limit: Maximum readings to return (1-1000, default 100)
            source_id: Data source identifier (default: 'local')
        """
        limit = max(1, min(limit, 1000))
        rows = db.query_raw_readings(
            source_id=source_id,
            record_type=record_type,
            start=start,
            end=end,
            limit=limit,
        )
        return json.dumps(
            {"source_id": source_id, "count": len(rows), "readings": rows},
            default=str,
        )

    @mcp.tool()
    def list_sources() -> str:
        """List all available health data sources with record counts.

        Call this first to discover what data is available.
        Returns source IDs, types, record counts, and date ranges.
        """
        rows = db.conn.execute("""
            SELECT source_id, source_type, COUNT(*) as record_count,
                   MIN(timestamp) as earliest, MAX(timestamp) as latest
            FROM records
            GROUP BY source_id, source_type
        """).fetchall()
        sources = [
            {
                "source_id": r["source_id"],
                "source_type": r["source_type"],
                "record_count": r["record_count"],
                "earliest": r["earliest"],
                "latest": r["latest"],
            }
            for r in rows
        ]
        return json.dumps({"sources": sources, "count": len(sources)}, default=str)

    @mcp.tool()
    def get_vault_file(source_id: str = SOURCE_ID) -> list | str:
        """Retrieve the original file content for a source (image, PDF, text).

        For images: returns the actual image so you can see it.
        For text/PDF: returns the extracted text content.
        Use this after retrieve_health_context finds a document or photo.

        Args:
            source_id: The source_id of the file (from list_sources or search results)
        """
        vault = Path(data_dir)

        row = db.conn.execute(
            "SELECT source_type, metadata FROM records WHERE source_id = ? LIMIT 1",
            (source_id,),
        ).fetchone()

        if not row:
            return f"No records found for source_id: {source_id}"

        source_type = row["source_type"]
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        stored_filename = metadata.get("filename")

        file_path = None
        if stored_filename:
            candidate = vault / stored_filename
            if candidate.exists():
                file_path = candidate

        if not file_path:
            ext_map = {
                "image": (".png", ".jpg", ".jpeg", ".heic"),
                "pdf": (".pdf",),
                "text": (".txt", ".md", ".csv"),
            }
            exts = ext_map.get(source_type, ())
            for f in vault.iterdir():
                if f.is_file() and f.suffix.lower() in exts:
                    file_path = f
                    break

        if not file_path or not file_path.exists():
            texts = db.conn.execute(
                "SELECT summary FROM timeline_entries WHERE source_id = ?",
                (source_id,),
            ).fetchall()
            content = "\n".join(r["summary"] for r in texts)
            return f"File not found in data/. Extracted text:\n{content}"

        if source_type == "image" or file_path.suffix.lower() in (
            ".png", ".jpg", ".jpeg", ".heic"
        ):
            return [
                TextContent(type="text", text=f"Image: {file_path.name} (source_id: {source_id})"),
                Image(path=file_path),
            ]

        texts = db.conn.execute(
            "SELECT summary FROM timeline_entries WHERE source_id = ?",
            (source_id,),
        ).fetchall()
        content = "\n".join(r["summary"] for r in texts)
        return f"File: {file_path.name} (source_id: {source_id})\n\n{content}"

    @mcp.tool()
    def reindex() -> str:
        """Re-scan the data/ folder and index any new or modified files.

        Call this after adding new files to the data/ folder during a session,
        so they become searchable without restarting the server.
        """
        result = _scan_and_index(db, embedder)
        return json.dumps(result, default=str)

    @mcp.resource("lemniscus://modalities")
    def list_modalities() -> str:
        """Available data modalities."""
        return json.dumps({
            "modalities": [
                {"name": "vitals", "description": "Heart rate, HRV, blood pressure, SpO2, respiratory rate"},
                {"name": "activity", "description": "Steps, distance, active energy, exercise time"},
                {"name": "sleep", "description": "Sleep analysis, sleep stages, sleep duration"},
                {"name": "body", "description": "Weight, height, BMI, body fat percentage"},
                {"name": "nutrition", "description": "Calories, protein, carbs, fat, water intake"},
                {"name": "fitness", "description": "VO2 Max, readiness scores"},
                {"name": "mindfulness", "description": "Mindful sessions, meditation"},
                {"name": "workout", "description": "Exercise sessions, workout details"},
                {"name": "other", "description": "Documents (PDF pages, text files), clinical photos, images"},
            ],
            "sources": ["apple_health", "oura", "garmin", "pdf", "image", "text"],
        })

    return mcp


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_login() -> None:
    email = input("Email: ").strip()
    password = getpass("Password: ")
    try:
        from auth import login
        session = login(email, password)
        print(f"Signed in as {session['email']} (tier: {session['tier']})")
    except Exception as e:
        print(f"Login failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_signup() -> None:
    email = input("Email: ").strip()
    password = getpass("Password: ")
    confirm = getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(1)
    try:
        from auth import signup
        result = signup(email, password)
        if result.get("needs_confirmation"):
            print(f"Check your email ({email}) to confirm your account, then run: python server.py login")
        else:
            print(f"Account created! Signed in as {result['email']} (tier: {result['tier']})")
    except Exception as e:
        print(f"Signup failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_logout() -> None:
    from auth import logout
    logout()
    print("Signed out.")


def cmd_status() -> None:
    from auth import check_auth
    session = check_auth()
    if session:
        print(f"Authenticated: {session['email']} (tier: {session['tier']})")
    else:
        print("Not authenticated. Run: python server.py login")

    manifest = _load_manifest()
    if manifest:
        total_records = sum(e.get("records", 0) for e in manifest.values())
        print(f"Indexed files: {len(manifest)} ({total_records} total records)")
    else:
        print("No files indexed yet. Drop files in data/ and start the server.")


async def cmd_serve() -> None:
    """Start the MCP stdio server."""
    from auth import check_auth
    from db import Database
    from embedder import Embedder

    # Check auth
    session = check_auth()
    if session is None:
        print(
            "Not authenticated. Please run:\n"
            "  python server.py signup   (new account)\n"
            "  python server.py login    (existing account)",
            file=sys.stderr,
        )
        sys.exit(1)

    logger.info(f"Authenticated as {session['email']} (tier: {session['tier']})")

    # Verify model files exist
    model_path = BASE_DIR / "minilm.onnx"
    tokenizer_path = BASE_DIR / "tokenizer.json"
    if not model_path.exists() or not tokenizer_path.exists():
        print(
            "Model files not found. Run: python download_model.py",
            file=sys.stderr,
        )
        sys.exit(1)

    # Init DB + embedder
    db = Database(str(BASE_DIR / "lemniscus.db"))
    db.connect()

    embedder = Embedder(model_path=str(model_path), tokenizer_path=str(tokenizer_path))
    embedder.load()

    # Auto-index data/
    DATA_DIR.mkdir(exist_ok=True)
    result = _scan_and_index(db, embedder)
    logger.info(f"Auto-index: {result}")

    # Run MCP stdio server
    mcp = create_server(db=db, embedder=embedder, data_dir=str(DATA_DIR))
    await mcp.run_stdio_async()


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else None

    if cmd == "login":
        cmd_login()
    elif cmd == "signup":
        cmd_signup()
    elif cmd == "logout":
        cmd_logout()
    elif cmd == "status":
        cmd_status()
    elif cmd is None:
        import asyncio
        asyncio.run(cmd_serve())
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
