"""Lemniscus Open — MCP-only health data server for Claude Code.

Usage:
    python server.py              Run MCP stdio server
    python server.py index        Index files in data/ (visible progress)
    python server.py status       Show indexing status
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
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
DATA_DIR = Path(os.environ["LEMNISCUS_DATA_DIR"]) if os.environ.get("LEMNISCUS_DATA_DIR") else BASE_DIR / "data"
MANIFEST_PATH = DATA_DIR / ".indexed_files.json"
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
    cleared = False

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

        # On first file that needs indexing, clear all old data + manifest
        # to prevent duplicates (all files share one source_id)
        if not cleared:
            logger.info("Clearing old data for clean re-index...")
            db.clear_source(SOURCE_ID)
            manifest = {}
            cleared = True

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
            records_count = result.get("records_ingested", 0)
            manifest[rel] = {
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "source_type": source_type,
                "records": records_count,
                "indexed_at": datetime.now().isoformat(),
            }
            indexed += 1
            logger.info(f"  -> {records_count} records")
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
    def get_health_summary(
        period: str = "month",
        year: int | None = None,
        metric: str | None = None,
        source_id: str = SOURCE_ID,
    ) -> str:
        """Pre-aggregated health statistics grouped by month or year.

        Returns avg/min/max/total for each metric — no individual readings.
        Best for broad questions about trends, yearly overviews, or comparing
        metrics over time. Claude can then drill into specific dates with
        browse_timeline or query_health_readings.

        Args:
            period: Grouping period — 'month' or 'year' (default: 'month')
            year: Optional year filter (e.g. 2024). Omit for all years.
            metric: Optional metric filter — matches short_name or record_type (e.g. 'Heart Rate', 'steps', 'HRV'). Omit for all metrics.
            source_id: Data source identifier (default: 'local')
        """
        if period not in ("month", "year"):
            period = "month"

        rows = db.aggregate_raw_readings(
            source_id=source_id,
            period=period,
            year=year,
            metric=metric,
        )

        # Group flat rows into {metric: {info, periods: [...]}} structure
        metrics: dict[str, dict] = {}
        for row in rows:
            key = row["short_name"]
            if key not in metrics:
                metrics[key] = {
                    "short_name": row["short_name"],
                    "record_type": row["record_type"],
                    "unit": row["unit"],
                    "periods": [],
                }
            metrics[key]["periods"].append({
                "period": row["period"],
                "avg": row["avg_value"],
                "min": row["min_value"],
                "max": row["max_value"],
                "total": row["total_value"],
                "count": row["reading_count"],
            })

        # Date range
        date_range = db.conn.execute(
            "SELECT MIN(timestamp) as earliest, MAX(timestamp) as latest FROM raw_readings WHERE source_id = ?",
            (source_id,),
        ).fetchone()

        result = {
            "period_type": period,
            "metrics": list(metrics.values()),
            "total_metrics": len(metrics),
            "date_range": {
                "earliest": date_range["earliest"] if date_range else None,
                "latest": date_range["latest"] if date_range else None,
            },
        }
        return json.dumps(result, default=str)

    @mcp.tool()
    def browse_timeline(
        start: str | None = None,
        end: str | None = None,
        modality: str | None = None,
        limit: int = 50,
        count_only: bool = False,
        source_id: str = SOURCE_ID,
    ) -> str:
        """Chronological daily summaries of health data and documents.

        Best for exploring what happened in a specific date range. Use
        count_only=true first to check how many entries exist before fetching.

        Args:
            start: Start of time range (ISO 8601, e.g. '2024-01-01T00:00:00')
            end: End of time range (ISO 8601)
            modality: Filter by: vitals, activity, sleep, body, nutrition, fitness, mindfulness, workout, other (documents/photos)
            limit: Maximum entries to return (1-500)
            count_only: If true, return only counts by modality — no entries. Use to check scope before fetching.
            source_id: Data source identifier (default: 'local')
        """
        if count_only:
            counts = db.count_timeline_by_modality(
                source_id=source_id,
                start=start,
                end=end,
            )
            counts_dict = {r["modality"]: r["count"] for r in counts}
            return json.dumps({
                "source_id": source_id,
                "counts": counts_dict,
                "total": sum(counts_dict.values()),
            }, default=str)

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
        """Individual sensor readings — the most granular level.

        Returns raw data points (e.g., every heart rate reading, every step
        count). Best for detailed drill-down into specific dates or metrics
        after identifying what to look at via get_health_summary or
        browse_timeline.

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

    @mcp.tool()
    def index_status() -> str:
        """Show what health data has been indexed and is available for search.

        Returns a summary of indexed files, record counts by type, and date
        ranges. Use this to confirm data was ingested correctly or to see
        what's available before searching.
        """
        manifest = _load_manifest()

        # DB-level stats
        sources = db.conn.execute("""
            SELECT source_type, COUNT(*) as record_count,
                   MIN(timestamp) as earliest, MAX(timestamp) as latest
            FROM records
            WHERE source_id = ?
            GROUP BY source_type
        """, (SOURCE_ID,)).fetchall()

        total_records = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM records WHERE source_id = ?",
            (SOURCE_ID,),
        ).fetchone()["cnt"]

        total_readings = db.conn.execute(
            "SELECT COUNT(*) as cnt FROM raw_readings WHERE source_id = ?",
            (SOURCE_ID,),
        ).fetchone()["cnt"]

        # Files in data/ not yet indexed
        unindexed = []
        for fp in sorted(DATA_DIR.rglob("*")):
            if fp.is_file() and fp.suffix.lower() in SUPPORTED_EXTENSIONS and not fp.name.startswith("."):
                rel = str(fp.relative_to(DATA_DIR))
                if rel not in manifest:
                    unindexed.append(rel)

        result = {
            "indexed_files": len(manifest),
            "total_records": total_records,
            "total_raw_readings": total_readings,
            "by_type": [
                {
                    "source_type": r["source_type"],
                    "record_count": r["record_count"],
                    "earliest": r["earliest"],
                    "latest": r["latest"],
                }
                for r in sources
            ],
            "files": [
                {"name": k, "type": v.get("source_type", "?"), "records": v.get("records", 0)}
                for k, v in manifest.items()
            ],
            "unindexed_files": unindexed,
        }
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

def cmd_index() -> None:
    """Index files in data/ with visible terminal progress."""
    from db import Database
    from embedder import Embedder

    # Check for files first
    DATA_DIR.mkdir(exist_ok=True)
    files = [
        f for f in sorted(DATA_DIR.rglob("*"))
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS and not f.name.startswith(".")
    ]
    if not files:
        print("No supported files found in data/")
        print("Drop your health files there first (XML, PDF, JSON, etc.)")
        return

    manifest = _load_manifest()
    new_files = [
        f for f in files
        if str(f.relative_to(DATA_DIR)) not in manifest
        or manifest[str(f.relative_to(DATA_DIR))].get("size") != f.stat().st_size
        or manifest[str(f.relative_to(DATA_DIR))].get("mtime") != f.stat().st_mtime
    ]

    if not new_files:
        print(f"All {len(files)} files already indexed. Nothing to do.")
        return

    print(f"Found {len(new_files)} file(s) to index...")
    print()

    # Init DB + embedder with visible progress
    print("Loading AI model...", end=" ", flush=True)
    db = Database(str(DATA_DIR / "lemniscus.db"))
    db.connect()
    embedder = Embedder(
        model_path=str(BASE_DIR / "minilm.onnx"),
        tokenizer_path=str(BASE_DIR / "tokenizer.json"),
    )
    embedder.load()
    print("done")
    print()

    # Index with progress
    result = _scan_and_index(db, embedder)
    db.close()

    print()
    print(f"Indexed: {result['indexed']} file(s)")
    if result.get("skipped"):
        print(f"Skipped: {result['skipped']} (already indexed)")
    if result.get("errors"):
        print(f"Errors:  {result['errors']}")
    print()
    print("Ready! Run 'claude' to start querying your health data.")


def cmd_status() -> None:
    manifest = _load_manifest()
    if manifest:
        total_records = sum(e.get("records", 0) for e in manifest.values())
        print(f"Indexed files: {len(manifest)} ({total_records} total records)")
        for name, info in manifest.items():
            print(f"  {name} ({info.get('source_type', '?')}) — {info.get('records', 0)} records")
    else:
        print("No files indexed yet. Drop files in data/ and run: python server.py index")


async def cmd_serve() -> None:
    """Start the MCP stdio server."""
    from db import Database
    from embedder import Embedder

    # Verify model files exist
    model_path = BASE_DIR / "minilm.onnx"
    tokenizer_path = BASE_DIR / "tokenizer.json"
    if not model_path.exists() or not tokenizer_path.exists():
        print(
            "Model files not found. Run: python download_model.py",
            file=sys.stderr,
        )
        sys.exit(1)

    # Init DB + embedder — store DB in data dir so extension dir stays read-only
    db = Database(str(DATA_DIR / "lemniscus.db"))
    db.connect()

    embedder = Embedder(model_path=str(model_path), tokenizer_path=str(tokenizer_path))
    embedder.load()

    DATA_DIR.mkdir(exist_ok=True)

    # Run MCP stdio server
    mcp = create_server(db=db, embedder=embedder, data_dir=str(DATA_DIR))
    await mcp.run_stdio_async()


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else None

    if cmd == "index":
        cmd_index()
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
