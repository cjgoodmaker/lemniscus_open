"""Oura Ring data parser.

Handles Oura JSON exports and API responses.
Oura exports daily summaries for sleep, activity, and readiness,
plus intraday heart rate, HRV, temperature, and SpO2.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, BinaryIO

logger = logging.getLogger(__name__)

# Oura data type → (modality, short_name)
OURA_TYPE_MAP: dict[str, tuple[str, str]] = {
    "sleep": ("sleep", "Sleep"),
    "daily_activity": ("activity", "DailyActivity"),
    "daily_readiness": ("fitness", "Readiness"),
    "heart_rate": ("vitals", "HeartRate"),
    "hrv": ("vitals", "HRV"),
    "spo2": ("vitals", "SpO2"),
    "temperature": ("vitals", "SkinTemp"),
    "workout": ("workout", "Workout"),
    "session": ("mindfulness", "Session"),
}


def parse_oura_export(file: BinaryIO) -> list[dict[str, Any]]:
    """Parse Oura JSON export or API response.

    Expected format (Oura API v2 style):
    {
        "sleep": [...],
        "daily_activity": [...],
        "daily_readiness": [...],
        "heart_rate": [...],
        ...
    }

    Or a flat list of records with a "type" field.
    """
    raw = json.load(file)
    records: list[dict[str, Any]] = []

    if isinstance(raw, list):
        for item in raw:
            record = _parse_oura_record(item)
            if record:
                records.append(record)
    elif isinstance(raw, dict):
        for data_type, items in raw.items():
            if not isinstance(items, list):
                continue
            type_info = OURA_TYPE_MAP.get(data_type)
            if not type_info:
                continue
            modality, short_name = type_info
            for item in items:
                record = _parse_oura_typed_record(item, data_type, modality, short_name)
                if record:
                    records.append(record)

    logger.info(f"Parsed {len(records)} records from Oura export")
    return records


def _parse_oura_typed_record(
    item: dict[str, Any], data_type: str, modality: str, short_name: str
) -> dict[str, Any] | None:
    """Parse a single Oura record of known type."""
    day = item.get("day") or item.get("summary_date")
    timestamp_str = item.get("timestamp") or item.get("bedtime_start")

    if timestamp_str:
        try:
            ts = datetime.fromisoformat(timestamp_str)
        except ValueError:
            return None
    elif day:
        try:
            ts = datetime.fromisoformat(f"{day}T00:00:00")
        except ValueError:
            return None
    else:
        return None

    # Extract primary value based on type
    value, unit = _extract_oura_value(item, data_type)
    summary = _build_oura_summary(item, data_type, short_name, value, unit)

    return {
        "source_type": "oura",
        "record_type": f"oura_{data_type}",
        "modality": modality,
        "short_name": short_name,
        "value": value,
        "unit": unit,
        "timestamp": ts,
        "end_timestamp": _parse_end(item),
        "metadata": {k: v for k, v in item.items() if k not in ("day", "timestamp")},
    }


def _parse_oura_record(item: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a single Oura record with a type field."""
    data_type = item.get("type", "").lower()
    type_info = OURA_TYPE_MAP.get(data_type)
    if not type_info:
        return None
    modality, short_name = type_info
    return _parse_oura_typed_record(item, data_type, modality, short_name)


def _extract_oura_value(item: dict[str, Any], data_type: str) -> tuple[float | None, str | None]:
    """Extract the primary numeric value and unit from an Oura record."""
    if data_type == "sleep":
        total = item.get("total_sleep_duration") or item.get("duration")
        if total:
            return round(float(total) / 3600, 1), "hours"
        return item.get("score"), "score"
    elif data_type == "daily_activity":
        return item.get("score") or item.get("steps"), "score" if item.get("score") else "steps"
    elif data_type == "daily_readiness":
        return item.get("score"), "score"
    elif data_type == "heart_rate":
        return item.get("bpm"), "bpm"
    elif data_type == "hrv":
        return item.get("rmssd"), "ms"
    elif data_type == "spo2":
        return item.get("spo2_percentage") or item.get("average"), "%"
    elif data_type == "temperature":
        return item.get("deviation") or item.get("delta"), "°C"
    elif data_type == "workout":
        return item.get("calories"), "kcal"
    return None, None


def _build_oura_summary(
    item: dict[str, Any], data_type: str, short_name: str,
    value: float | None, unit: str | None,
) -> str:
    if value is not None and unit:
        return f"{short_name}: {value} {unit}"
    return short_name


def _parse_end(item: dict[str, Any]) -> datetime | None:
    end_str = item.get("bedtime_end") or item.get("end_datetime")
    if end_str:
        try:
            return datetime.fromisoformat(end_str)
        except ValueError:
            return None
    return None
