"""Lemniscus — Apple Health MCP server for Claude.

Usage:
    python server.py              Run MCP stdio server
    python server.py index        Index files in data/ (visible progress)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("lemniscus")

BASE_DIR = Path(__file__).parent
DATA_DIR = (
    Path(os.environ["LEMNISCUS_DATA_DIR"])
    if os.environ.get("LEMNISCUS_DATA_DIR")
    else BASE_DIR / "data"
)
MANIFEST_PATH = DATA_DIR / ".indexed_files.json"
SOURCE_ID = "local"


# ---------------------------------------------------------------------------
# Indexing
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


def _scan_and_index(db) -> dict:
    """Scan data/ for Apple Health XMLs and index new/modified files."""
    from parsers.apple_health import stream_raw_readings

    DATA_DIR.mkdir(exist_ok=True)
    manifest = _load_manifest()
    indexed = 0
    skipped = 0
    errors = []
    cleared = False

    for file_path in sorted(DATA_DIR.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() != ".xml":
            continue
        if file_path.name.startswith("."):
            continue

        rel = str(file_path.relative_to(DATA_DIR))
        stat = file_path.stat()
        entry = manifest.get(rel)

        if entry and entry.get("size") == stat.st_size and entry.get("mtime") == stat.st_mtime:
            skipped += 1
            continue

        if not cleared:
            logger.info("Clearing old data for clean re-index...")
            db.clear_source(SOURCE_ID)
            manifest = {}
            cleared = True

        logger.info(f"Indexing {rel}...")
        try:
            with open(file_path, "rb") as f:
                rows, count = stream_raw_readings(f, SOURCE_ID)
            if rows:
                db.bulk_insert_raw_readings(rows)
            manifest[rel] = {
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "readings": count,
                "indexed_at": datetime.now().isoformat(),
            }
            indexed += 1
            logger.info(f"  -> {count} readings")
        except Exception as e:
            logger.error(f"  -> Failed: {e}")
            errors.append({"file": rel, "error": str(e)})

    if indexed > 0:
        logger.info("Building metric stats cache...")
        db.rebuild_metric_stats(SOURCE_ID)

    _save_manifest(manifest)
    return {"indexed": indexed, "skipped": skipped, "errors": len(errors)}


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

def create_server(db) -> object:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("lemniscus")

    @mcp.tool()
    def list_metrics(source_id: str = SOURCE_ID) -> str:
        """List all available health metrics with counts and date ranges.

        Call this first to see what data is available. Returns each metric's
        short name, Apple Health type, unit, reading count, and date range.

        Args:
            source_id: Data source identifier (default: 'local')
        """
        metrics = db.list_metrics(source_id)
        total = sum(m["reading_count"] for m in metrics)
        return json.dumps({
            "total_readings": total,
            "metrics": metrics,
        }, default=str)

    @mcp.tool()
    def query_readings(
        metric: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 500,
        source_id: str = SOURCE_ID,
    ) -> str:
        """Get individual health readings filtered by metric and date range.

        Returns raw data points (e.g. every heart rate reading, every step count).
        Use the metric parameter to filter by short_name (e.g. 'HeartRate', 'Steps',
        'HRV', 'Weight'). Call list_metrics first to see available metric names.

        Args:
            metric: Filter by short_name (e.g. 'HeartRate', 'Steps', 'HRV'). Partial match supported.
            start: Start of time range (ISO 8601, e.g. '2024-01-01')
            end: End of time range (ISO 8601, e.g. '2024-01-31')
            limit: Maximum readings to return (1-1000, default 500)
            source_id: Data source identifier (default: 'local')
        """
        limit = max(1, min(limit, 1000))
        rows = db.query_readings(
            source_id=source_id,
            short_name=metric,
            start=start,
            end=end,
            limit=limit,
        )
        return json.dumps({"count": len(rows), "readings": rows}, default=str)

    @mcp.tool()
    def get_summary(
        period: str = "month",
        year: int | None = None,
        metric: str | None = None,
        source_id: str = SOURCE_ID,
    ) -> str:
        """Aggregated health statistics grouped by month or year.

        Returns avg/min/max/total for each metric. Best for trends, comparisons,
        and overviews. Use query_readings to drill into specific dates after.

        Args:
            period: 'month' or 'year' (default: 'month')
            year: Optional year filter (e.g. 2024)
            metric: Optional metric filter (e.g. 'HeartRate', 'Steps'). Partial match.
            source_id: Data source identifier (default: 'local')
        """
        if period not in ("month", "year"):
            period = "month"

        rows = db.aggregate_readings(
            source_id=source_id,
            period=period,
            year=year,
            metric=metric,
        )

        # Group by metric
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

        return json.dumps({
            "period_type": period,
            "total_metrics": len(metrics),
            "metrics": list(metrics.values()),
        }, default=str)

    @mcp.tool()
    def reindex() -> str:
        """Re-scan the data folder and index any new or modified Apple Health exports.

        Call this after adding new XML files to the data folder.
        """
        result = _scan_and_index(db)
        return json.dumps(result, default=str)

    return mcp


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_index() -> None:
    from db import Database

    DATA_DIR.mkdir(exist_ok=True)
    files = [f for f in sorted(DATA_DIR.rglob("*.xml")) if f.is_file() and not f.name.startswith(".")]

    if not files:
        print("No XML files found in data/")
        print("Export from Apple Health and drop export.xml here.")
        return

    manifest = _load_manifest()
    new_files = [
        f for f in files
        if str(f.relative_to(DATA_DIR)) not in manifest
        or manifest[str(f.relative_to(DATA_DIR))].get("size") != f.stat().st_size
    ]

    if not new_files:
        print(f"All {len(files)} files already indexed.")
        return

    print(f"Found {len(new_files)} file(s) to index...")

    db = Database(str(DATA_DIR / "lemniscus.db"))
    db.connect()

    result = _scan_and_index(db)
    db.close()

    print(f"\nIndexed: {result['indexed']} file(s)")
    if result.get("skipped"):
        print(f"Skipped: {result['skipped']} (already indexed)")
    if result.get("errors"):
        print(f"Errors:  {result['errors']}")


async def cmd_serve() -> None:
    import asyncio
    from db import Database

    db = Database(str(DATA_DIR / "lemniscus.db"))
    db.connect()
    DATA_DIR.mkdir(exist_ok=True)

    # Auto-index in background so server starts immediately
    async def _bg_index():
        await asyncio.to_thread(_scan_and_index, db)

    asyncio.create_task(_bg_index())

    mcp = create_server(db=db)
    await mcp.run_stdio_async()


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else None

    if cmd == "index":
        cmd_index()
    elif cmd is None:
        import asyncio
        asyncio.run(cmd_serve())
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
