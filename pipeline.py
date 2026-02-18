"""Ingest pipeline: parse → chunk → embed → store in SQLite."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, BinaryIO

from chunker import chunk_text
from db import Database
from embedder import Embedder
from models import HealthRecord, Modality, TimelineEntry
from parsers.apple_health import HEALTH_TYPE_MAP, parse_apple_health_export, stream_raw_readings
from parsers.garmin import parse_garmin_export
from parsers.generic_json import parse_generic_json
from parsers.image import parse_image
from parsers.oura import parse_oura_export
from parsers.pdf import parse_pdf
from parsers.text import parse_text

logger = logging.getLogger(__name__)

PARSERS = {
    "apple_health": parse_apple_health_export,
    "oura": parse_oura_export,
    "garmin": parse_garmin_export,
    "generic_json": parse_generic_json,
    "pdf": parse_pdf,
    "image": parse_image,
    "text": parse_text,
}

COMMIT_BATCH_SIZE = 500


def ingest_file(
    file: BinaryIO,
    source_type: str,
    source_id: str,
    db: Database,
    embedder: Embedder,
    chunk_max_length: int = 512,
    chunk_overlap: int = 50,
    filename: str = "",
) -> dict[str, Any]:
    """Ingest a wearable data file end-to-end.

    1. Parse file → raw records
    2. Store as HealthRecords + TimelineEntries (batched commits)
    3. Chunk summaries → embed → store vectors + FTS (batched commits)

    Returns ingest summary.
    """
    parser = PARSERS.get(source_type)
    if not parser:
        raise ValueError(f"Unsupported source type: {source_type}. Use: {', '.join(PARSERS)}")

    # For Apple Health: bulk-store individual readings first, then aggregate for embedding
    raw_readings_count = 0
    if source_type == "apple_health":
        rows, raw_readings_count = stream_raw_readings(file, source_id)
        if rows:
            db.bulk_insert_raw_readings(rows)
            logger.info(f"Bulk stored {raw_readings_count} individual readings")
        file.seek(0)

    raw_records = parser(file)
    if not raw_records:
        return {
            "source_id": source_id, "source_type": source_type,
            "records_ingested": 0, "records_failed": 0,
            "raw_readings_stored": raw_readings_count,
        }

    ingested = 0
    failed = 0
    entries_to_embed: list[tuple[TimelineEntry, str]] = []

    for raw in raw_records:
        try:
            modality = _resolve_modality(raw.get("modality", "other"))
            timestamp = raw["timestamp"]
            value = raw.get("value")
            unit = raw.get("unit", "")
            short_name = raw.get("short_name", raw.get("record_type", "unknown"))

            meta = raw.get("metadata", {})
            if filename:
                meta["filename"] = filename

            record = HealthRecord(
                source_id=source_id,
                source_type=source_type,
                modality=modality,
                record_type=raw["record_type"],
                value=value,
                unit=unit,
                timestamp=timestamp,
                end_timestamp=raw.get("end_timestamp"),
                metadata=meta,
            )
            db.insert_record(record)

            meta = raw.get("metadata", {})

            # Document types: use text_content as summary for embedding
            text_content = meta.get("text_content")
            if text_content:
                summary = text_content
            elif (agg := meta.get("aggregation")) and agg == "daily_avg" and "min" in meta and "max" in meta:
                summary = f"{short_name}: avg {value} {unit} (min {meta['min']}, max {meta['max']}, {meta.get('sample_count', '')} samples)"
            elif agg == "daily_sum" and "sample_count" in meta:
                summary = f"{short_name}: {value} {unit} (daily total)"
            elif value is not None:
                summary = f"{short_name}: {value} {unit}"
            else:
                summary = short_name

            # Dedup key: day-level timestamp | type | value
            ts_key = timestamp.replace(second=0, microsecond=0).isoformat()
            dedup_key = f"{ts_key}|{short_name}|{value}"

            entry_meta = {
                    "signal_type": short_name,
                    "category": raw.get("modality", "other"),
                    "value": value,
                    "unit": unit,
                    "dedup_key": dedup_key,
            }
            if filename:
                entry_meta["filename"] = filename

            entry = TimelineEntry(
                source_id=source_id,
                record_id=record.id,
                timestamp=timestamp,
                modality=modality,
                summary=summary,
                metadata=entry_meta,
            )
            db.insert_timeline_entry(entry)
            entries_to_embed.append((entry, summary))
            ingested += 1

            # Batch commit every N records
            if ingested % COMMIT_BATCH_SIZE == 0:
                db.commit()

        except Exception as e:
            logger.error(f"Error ingesting record: {e}", exc_info=True)
            failed += 1

    # Final commit for records + timeline
    db.commit()
    logger.info(f"Stored {ingested} summary records and timeline entries")

    # Batch embed all entries
    embedded = _embed_entries(entries_to_embed, db, embedder, chunk_max_length, chunk_overlap)

    logger.info(f"Ingested {ingested} records, embedded {embedded} chunks, {failed} failures")

    return {
        "source_id": source_id,
        "source_type": source_type,
        "records_ingested": ingested,
        "records_failed": failed,
        "chunks_embedded": embedded,
        "raw_readings_stored": raw_readings_count,
    }


def ingest_records(
    records: list[dict[str, Any]],
    source_type: str,
    source_id: str,
    db: Database,
    embedder: Embedder,
) -> dict[str, Any]:
    """Ingest pre-parsed records (e.g., from MCP tool or API JSON body)."""
    ingested = 0
    failed = 0
    entries_to_embed: list[tuple[TimelineEntry, str]] = []

    for raw in records:
        try:
            # Infer modality from type maps if not explicitly set
            explicit_modality = raw.get("modality") or raw.get("category")
            record_type = raw.get("type", raw.get("record_type", "unknown"))
            if not explicit_modality and record_type in HEALTH_TYPE_MAP:
                explicit_modality = HEALTH_TYPE_MAP[record_type][0]
            modality = _resolve_modality(explicit_modality or "other")

            timestamp_str = raw.get("timestamp") or raw.get("startDate")
            if isinstance(timestamp_str, str):
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            elif isinstance(timestamp_str, datetime):
                timestamp = timestamp_str
            else:
                timestamp = datetime.utcnow()

            value = raw.get("value")
            unit = raw.get("unit", "")
            short_name = raw.get("short_name", record_type)
            if short_name.startswith("HKQuantityTypeIdentifier"):
                short_name = short_name.replace("HKQuantityTypeIdentifier", "")
            elif short_name.startswith("HKCategoryTypeIdentifier"):
                short_name = short_name.replace("HKCategoryTypeIdentifier", "")

            record = HealthRecord(
                source_id=source_id,
                source_type=source_type,
                modality=modality,
                record_type=record_type,
                value=value,
                unit=unit,
                timestamp=timestamp,
                end_timestamp=None,
                metadata=raw.get("metadata", {}),
            )
            db.insert_record(record)

            summary = f"{short_name}: {value} {unit}" if value is not None else short_name

            entry = TimelineEntry(
                source_id=source_id,
                record_id=record.id,
                timestamp=timestamp,
                modality=modality,
                summary=summary,
            )
            db.insert_timeline_entry(entry)
            entries_to_embed.append((entry, summary))
            ingested += 1

            if ingested % COMMIT_BATCH_SIZE == 0:
                db.commit()

        except Exception as e:
            logger.error(f"Error ingesting record: {e}", exc_info=True)
            failed += 1

    db.commit()

    embedded = _embed_entries(entries_to_embed, db, embedder, 512, 50)

    return {
        "source_id": source_id,
        "source_type": source_type,
        "records_ingested": ingested,
        "records_failed": failed,
        "chunks_embedded": embedded,
    }


def _embed_entries(
    entries: list[tuple[TimelineEntry, str]],
    db: Database,
    embedder: Embedder,
    chunk_max_length: int,
    chunk_overlap: int,
) -> int:
    """Chunk and embed timeline entries in batches with batched commits."""
    if not entries:
        return 0

    all_chunks: list[tuple[str, str, str, str]] = []  # (entry_id, source_id, modality, text)

    for entry, summary in entries:
        chunks = chunk_text(summary, max_length=chunk_max_length, overlap=chunk_overlap)
        for chunk_text_str in chunks:
            all_chunks.append((str(entry.id), entry.source_id, entry.modality.value, chunk_text_str))

    if not all_chunks:
        return 0

    # Batch embed
    texts = [c[3] for c in all_chunks]
    BATCH_SIZE = 256
    embedded_count = 0

    for i in range(0, len(texts), BATCH_SIZE):
        batch_texts = texts[i : i + BATCH_SIZE]
        batch_chunks = all_chunks[i : i + BATCH_SIZE]
        vectors = embedder.embed(batch_texts)

        for (entry_id, source_id, modality, text), vector in zip(batch_chunks, vectors):
            db.insert_embedding(entry_id, vector, text, source_id, modality)
            embedded_count += 1

        # Commit after each embedding batch (every 64 entries)
        db.commit()

    return embedded_count


def _resolve_modality(modality_str: str) -> Modality:
    """Resolve modality string to Modality enum."""
    try:
        return Modality(modality_str)
    except ValueError:
        return Modality.OTHER
