"""Generic JSON health data parser.

Handles JSON files containing health records in various formats:
- Apple Health data exported as JSON (by third-party tools)
- Generic lists of health records with type/value/unit/timestamp fields
- Dict-of-lists format (keyed by category)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, BinaryIO

from parsers.apple_health import HEALTH_TYPE_MAP

logger = logging.getLogger(__name__)


def parse_generic_json(file: BinaryIO) -> list[dict[str, Any]]:
    """Parse a generic JSON file containing health records.

    Tries to detect the structure and extract records with:
    - record_type, value, unit, timestamp, modality
    """
    raw = json.load(file)
    records: list[dict[str, Any]] = []

    if isinstance(raw, list):
        # Flat list of records
        for item in raw:
            record = _parse_record(item)
            if record:
                records.append(record)

    elif isinstance(raw, dict):
        # Could be: {"records": [...]}, {"data": [...]}, or dict-of-lists
        for key in ("records", "data", "items", "entries", "results"):
            if key in raw and isinstance(raw[key], list):
                for item in raw[key]:
                    record = _parse_record(item)
                    if record:
                        records.append(record)
                if records:
                    break

        if not records:
            # Dict-of-lists format: {"heart_rate": [...], "steps": [...], ...}
            for category, items in raw.items():
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            item.setdefault("type", category)
                            record = _parse_record(item)
                            if record:
                                records.append(record)

    logger.info(f"Generic JSON parser extracted {len(records)} records")
    return records


def _parse_record(item: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a single record from a JSON object."""
    if not isinstance(item, dict):
        return None

    # Extract record type
    record_type = (
        item.get("type")
        or item.get("record_type")
        or item.get("quantityType")
        or item.get("categoryType")
        or item.get("name")
    )
    if not record_type:
        return None

    # Extract timestamp
    timestamp = _parse_timestamp(item)
    if not timestamp:
        return None

    # Extract value and unit
    value = item.get("value") or item.get("qty") or item.get("quantity")
    unit = item.get("unit") or item.get("units") or ""

    # Try to convert numeric strings
    if isinstance(value, str):
        try:
            value = float(value)
            if value == int(value):
                value = int(value)
        except ValueError:
            pass

    # Resolve modality from HEALTH_TYPE_MAP if it's an HK type
    modality = item.get("modality") or item.get("category")
    short_name = record_type
    if record_type in HEALTH_TYPE_MAP:
        modality, short_name = HEALTH_TYPE_MAP[record_type]
    elif not modality:
        modality = _guess_modality(record_type)

    # Clean up HK prefixes for short_name
    for prefix in ("HKQuantityTypeIdentifier", "HKCategoryTypeIdentifier"):
        if short_name.startswith(prefix):
            short_name = short_name[len(prefix):]

    return {
        "record_type": record_type,
        "value": value,
        "unit": unit,
        "timestamp": timestamp,
        "end_timestamp": _parse_timestamp(item, end=True),
        "modality": modality,
        "short_name": short_name,
        "metadata": {k: v for k, v in item.items()
                     if k not in ("type", "record_type", "value", "unit", "timestamp",
                                  "startDate", "endDate", "date", "modality", "category")},
    }


def _parse_timestamp(item: dict, end: bool = False) -> datetime | None:
    """Extract and parse a timestamp from a record."""
    keys = ["endDate", "end_timestamp", "end"] if end else [
        "timestamp", "startDate", "date", "start", "day", "created_at",
    ]
    for key in keys:
        val = item.get(key)
        if val is None:
            continue
        if isinstance(val, datetime):
            return val
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except ValueError:
                pass
            # Try date-only format
            try:
                return datetime.strptime(val, "%Y-%m-%d")
            except ValueError:
                pass
    return None


def _guess_modality(record_type: str) -> str:
    """Guess modality from record type name."""
    rt = record_type.lower()
    if any(k in rt for k in ("heart", "hrv", "blood", "spo2", "oxygen", "respiratory", "temperature")):
        return "vitals"
    if any(k in rt for k in ("step", "distance", "energy", "active", "exercise", "flight")):
        return "activity"
    if any(k in rt for k in ("sleep", "bed", "awake", "rem", "deep")):
        return "sleep"
    if any(k in rt for k in ("weight", "bmi", "body", "height", "fat")):
        return "body"
    if any(k in rt for k in ("calori", "protein", "carb", "fat", "water", "caffeine")):
        return "nutrition"
    if any(k in rt for k in ("vo2", "readiness", "recovery")):
        return "fitness"
    if any(k in rt for k in ("mindful", "meditat")):
        return "mindfulness"
    if any(k in rt for k in ("workout", "run", "swim", "cycle", "walk")):
        return "workout"
    return "other"
