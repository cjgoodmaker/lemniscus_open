"""Garmin Connect data parser.

Handles Garmin JSON exports (from Garmin Connect data export).
Garmin exports are organized by category in separate JSON files,
but we also support a combined format.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, BinaryIO

logger = logging.getLogger(__name__)

# Garmin data type → (modality, short_name)
GARMIN_TYPE_MAP: dict[str, tuple[str, str]] = {
    "activities": ("workout", "Activity"),
    "dailies": ("activity", "DailySummary"),
    "sleep": ("sleep", "Sleep"),
    "heart_rate": ("vitals", "HeartRate"),
    "stress": ("vitals", "Stress"),
    "body_battery": ("vitals", "BodyBattery"),
    "spo2": ("vitals", "SpO2"),
    "respiration": ("vitals", "Respiration"),
    "body_composition": ("body", "BodyComposition"),
    "hrv": ("vitals", "HRV"),
}


def parse_garmin_export(file: BinaryIO) -> list[dict[str, Any]]:
    """Parse Garmin JSON export.

    Expected format:
    {
        "activities": [...],
        "dailies": [...],
        "sleep": [...],
        ...
    }

    Or a flat list of records with a "type" or "activityType" field.
    """
    raw = json.load(file)
    records: list[dict[str, Any]] = []

    if isinstance(raw, list):
        for item in raw:
            record = _parse_garmin_record(item)
            if record:
                records.append(record)
    elif isinstance(raw, dict):
        for data_type, items in raw.items():
            if not isinstance(items, list):
                continue
            type_info = GARMIN_TYPE_MAP.get(data_type)
            if not type_info:
                continue
            modality, short_name = type_info
            for item in items:
                record = _parse_garmin_typed_record(item, data_type, modality, short_name)
                if record:
                    records.append(record)

    logger.info(f"Parsed {len(records)} records from Garmin export")
    return records


def _parse_garmin_typed_record(
    item: dict[str, Any], data_type: str, modality: str, short_name: str
) -> dict[str, Any] | None:
    """Parse a single Garmin record of known type."""
    ts = _parse_garmin_timestamp(item)
    if not ts:
        return None

    value, unit = _extract_garmin_value(item, data_type)

    return {
        "source_type": "garmin",
        "record_type": f"garmin_{data_type}",
        "modality": modality,
        "short_name": short_name,
        "value": value,
        "unit": unit,
        "timestamp": ts,
        "end_timestamp": _parse_garmin_end(item),
        "metadata": {k: v for k, v in item.items() if k not in ("startTimeGMT", "calendarDate")},
    }


def _parse_garmin_record(item: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a single Garmin record with a type field."""
    data_type = item.get("type", item.get("activityType", "")).lower()
    # Map common activity types
    if data_type in ("running", "cycling", "swimming", "walking", "hiking", "strength_training"):
        return _parse_garmin_typed_record(item, "activities", "workout", data_type.title())
    type_info = GARMIN_TYPE_MAP.get(data_type)
    if not type_info:
        return None
    modality, short_name = type_info
    return _parse_garmin_typed_record(item, data_type, modality, short_name)


def _parse_garmin_timestamp(item: dict[str, Any]) -> datetime | None:
    """Extract timestamp from Garmin record."""
    for field in ("startTimeGMT", "startTimeLocal", "calendarDate", "timestamp", "measurementDate"):
        val = item.get(field)
        if not val:
            continue
        if isinstance(val, (int, float)):
            # Garmin sometimes uses epoch millis
            return datetime.utcfromtimestamp(val / 1000 if val > 1e12 else val)
        try:
            return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _parse_garmin_end(item: dict[str, Any]) -> datetime | None:
    for field in ("endTimeGMT", "endTimeLocal"):
        val = item.get(field)
        if val:
            try:
                return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            except ValueError:
                continue
    # Compute from duration if available
    duration = item.get("duration") or item.get("durationInSeconds")
    if duration:
        ts = _parse_garmin_timestamp(item)
        if ts:
            from datetime import timedelta
            return ts + timedelta(seconds=float(duration))
    return None


def _extract_garmin_value(item: dict[str, Any], data_type: str) -> tuple[float | None, str | None]:
    """Extract the primary numeric value and unit."""
    if data_type == "activities":
        dist = item.get("distance") or item.get("distanceInMeters")
        if dist:
            return round(float(dist) / 1000, 2), "km"
        duration = item.get("duration") or item.get("durationInSeconds")
        if duration:
            return round(float(duration) / 60, 1), "min"
        return item.get("calories"), "kcal"
    elif data_type == "dailies":
        return item.get("totalSteps") or item.get("steps"), "steps"
    elif data_type == "sleep":
        duration = item.get("durationInSeconds") or item.get("sleepTimeSeconds")
        if duration:
            return round(float(duration) / 3600, 1), "hours"
        return item.get("overallScore"), "score"
    elif data_type == "heart_rate":
        return item.get("heartRate") or item.get("value"), "bpm"
    elif data_type == "stress":
        return item.get("overallStressLevel") or item.get("value"), "level"
    elif data_type == "body_battery":
        return item.get("charged") or item.get("value"), "level"
    elif data_type == "spo2":
        return item.get("averageSpo2") or item.get("value"), "%"
    elif data_type == "respiration":
        return item.get("avgWakingRespirationValue") or item.get("value"), "brpm"
    elif data_type == "body_composition":
        return item.get("weight") or item.get("weightInGrams"), "g"
    elif data_type == "hrv":
        return item.get("weeklyAvg") or item.get("hrvValue"), "ms"
    return None, None
