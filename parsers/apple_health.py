"""Apple Health export.xml parser.

Streams individual readings from export.xml with O(1) memory usage.
A typical export contains 1M+ readings across 600MB+ of XML.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, BinaryIO

from lxml import etree

logger = logging.getLogger(__name__)

# Apple Health type → (modality, short_name)
HEALTH_TYPE_MAP: dict[str, tuple[str, str]] = {
    # Activity
    "HKQuantityTypeIdentifierStepCount": ("activity", "Steps"),
    "HKQuantityTypeIdentifierDistanceWalkingRunning": ("activity", "Distance"),
    "HKQuantityTypeIdentifierActiveEnergyBurned": ("activity", "ActiveEnergy"),
    "HKQuantityTypeIdentifierBasalEnergyBurned": ("activity", "BasalEnergy"),
    "HKQuantityTypeIdentifierFlightsClimbed": ("activity", "FlightsClimbed"),
    "HKQuantityTypeIdentifierAppleExerciseTime": ("activity", "ExerciseTime"),
    "HKQuantityTypeIdentifierAppleStandTime": ("activity", "StandTime"),
    # Vitals
    "HKQuantityTypeIdentifierHeartRate": ("vitals", "HeartRate"),
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": ("vitals", "HRV"),
    "HKQuantityTypeIdentifierRestingHeartRate": ("vitals", "RestingHR"),
    "HKQuantityTypeIdentifierWalkingHeartRateAverage": ("vitals", "WalkingHR"),
    "HKQuantityTypeIdentifierBloodPressureSystolic": ("vitals", "BPSystolic"),
    "HKQuantityTypeIdentifierBloodPressureDiastolic": ("vitals", "BPDiastolic"),
    "HKQuantityTypeIdentifierOxygenSaturation": ("vitals", "SpO2"),
    "HKQuantityTypeIdentifierBloodGlucose": ("vitals", "BloodGlucose"),
    "HKQuantityTypeIdentifierRespiratoryRate": ("vitals", "RespiratoryRate"),
    # Body
    "HKQuantityTypeIdentifierBodyMass": ("body", "Weight"),
    "HKQuantityTypeIdentifierHeight": ("body", "Height"),
    "HKQuantityTypeIdentifierBodyMassIndex": ("body", "BMI"),
    "HKQuantityTypeIdentifierBodyFatPercentage": ("body", "BodyFat"),
    "HKQuantityTypeIdentifierLeanBodyMass": ("body", "LeanMass"),
    # Sleep
    "HKCategoryTypeIdentifierSleepAnalysis": ("sleep", "SleepAnalysis"),
    # Nutrition
    "HKQuantityTypeIdentifierDietaryEnergyConsumed": ("nutrition", "Calories"),
    "HKQuantityTypeIdentifierDietaryProtein": ("nutrition", "Protein"),
    "HKQuantityTypeIdentifierDietaryCarbohydrates": ("nutrition", "Carbs"),
    "HKQuantityTypeIdentifierDietaryFatTotal": ("nutrition", "Fat"),
    "HKQuantityTypeIdentifierDietaryWater": ("nutrition", "Water"),
    # Fitness
    "HKQuantityTypeIdentifierVO2Max": ("fitness", "VO2Max"),
    # Mindfulness
    "HKCategoryTypeIdentifierMindfulSession": ("mindfulness", "MindfulSession"),
    # Workouts
    "HKWorkoutTypeIdentifier": ("workout", "Workout"),
}

# Types where values should be summed per day (cumulative metrics)
_SUM_TYPES = {
    "HKQuantityTypeIdentifierStepCount",
    "HKQuantityTypeIdentifierDistanceWalkingRunning",
    "HKQuantityTypeIdentifierActiveEnergyBurned",
    "HKQuantityTypeIdentifierBasalEnergyBurned",
    "HKQuantityTypeIdentifierFlightsClimbed",
    "HKQuantityTypeIdentifierAppleExerciseTime",
    "HKQuantityTypeIdentifierAppleStandTime",
    "HKQuantityTypeIdentifierDietaryEnergyConsumed",
    "HKQuantityTypeIdentifierDietaryProtein",
    "HKQuantityTypeIdentifierDietaryCarbohydrates",
    "HKQuantityTypeIdentifierDietaryFatTotal",
    "HKQuantityTypeIdentifierDietaryWater",
}

# Types where we just count occurrences (category types)
_COUNT_TYPES = {
    "HKCategoryTypeIdentifierSleepAnalysis",
    "HKCategoryTypeIdentifierMindfulSession",
}

_RECORD_TAGS = {"Record", "Workout"}


def _parse_value(value: str | None) -> float | str | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return value


def _parse_timestamp(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace(" +0000", "+00:00").replace(" -", "-").rstrip())
    except ValueError:
        # Apple Health uses format: 2024-01-15 08:30:00 -0700
        for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(ts.strip(), fmt)
            except ValueError:
                continue
        return None


def _clear_elem(elem):
    """Clear element and remove previous siblings to free memory."""
    elem.clear()
    while elem.getprevious() is not None:
        del elem.getparent()[0]


def parse_apple_health_export(file: BinaryIO) -> list[dict[str, Any]]:
    """Parse Apple Health export.xml, aggregating readings into daily summaries.

    Single-pass parsing with rolling aggregation — O(1) memory per metric per day.
    """
    # Rolling stats: (date_str, hk_type) → {sum, count, min, max}
    daily_stats: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"sum": 0.0, "count": 0, "min": float("inf"), "max": float("-inf")}
    )
    daily_units: dict[tuple[str, str], str] = {}
    workouts: list[dict[str, Any]] = []
    raw_count = 0

    # Single pass over the entire XML
    context = etree.iterparse(file, events=("end",))
    for _, elem in context:
        if elem.tag not in _RECORD_TAGS:
            continue

        if elem.tag == "Workout":
            start = _parse_timestamp(elem.get("startDate"))
            if not start:
                _clear_elem(elem)
                continue

            workout_type = elem.get("workoutActivityType", "Unknown")
            duration = elem.get("duration")
            energy = elem.get("totalEnergyBurned")

            workouts.append({
                "source_type": "apple_health",
                "record_type": "HKWorkoutTypeIdentifier",
                "modality": "workout",
                "short_name": workout_type.replace("HKWorkoutActivityType", ""),
                "value": float(duration) if duration else None,
                "unit": "min",
                "timestamp": start,
                "end_timestamp": _parse_timestamp(elem.get("endDate")),
                "metadata": {
                    "workout_type": workout_type,
                    "duration_minutes": duration,
                    "energy_burned": energy,
                    "energy_unit": elem.get("totalEnergyBurnedUnit"),
                    "source_name": elem.get("sourceName"),
                },
            })
            raw_count += 1
            _clear_elem(elem)
            continue

        # Record element
        hk_type = elem.get("type", "")
        type_info = HEALTH_TYPE_MAP.get(hk_type)
        if not type_info:
            _clear_elem(elem)
            continue

        start = _parse_timestamp(elem.get("startDate"))
        if not start:
            _clear_elem(elem)
            continue

        value = _parse_value(elem.get("value"))
        unit = elem.get("unit") or ""
        date_key = start.strftime("%Y-%m-%d")
        bucket_key = (date_key, hk_type)

        v = float(value) if isinstance(value, (int, float)) else 1.0
        stats = daily_stats[bucket_key]
        stats["sum"] += v
        stats["count"] += 1
        if v < stats["min"]:
            stats["min"] = v
        if v > stats["max"]:
            stats["max"] = v

        daily_units[bucket_key] = unit
        raw_count += 1
        _clear_elem(elem)

    logger.info(f"Scanned {raw_count} raw records, aggregating into {len(daily_stats)} daily buckets")

    # --- Build aggregated records ---
    records: list[dict[str, Any]] = []

    for (date_str, hk_type), stats in sorted(daily_stats.items()):
        modality, short_name = HEALTH_TYPE_MAP[hk_type]
        unit = daily_units.get((date_str, hk_type), "")
        ts = datetime.strptime(date_str, "%Y-%m-%d")

        if hk_type in _SUM_TYPES:
            total = stats["sum"]
            total_display = int(total) if total == int(total) else round(total, 1)
            records.append({
                "source_type": "apple_health",
                "record_type": hk_type,
                "modality": modality,
                "short_name": short_name,
                "value": total_display,
                "unit": unit,
                "timestamp": ts,
                "end_timestamp": None,
                "metadata": {"aggregation": "daily_sum", "sample_count": stats["count"]},
            })
        elif hk_type in _COUNT_TYPES:
            records.append({
                "source_type": "apple_health",
                "record_type": hk_type,
                "modality": modality,
                "short_name": short_name,
                "value": stats["count"],
                "unit": "sessions",
                "timestamp": ts,
                "end_timestamp": None,
                "metadata": {"aggregation": "daily_count", "sample_count": stats["count"]},
            })
        else:
            avg = stats["sum"] / stats["count"]
            records.append({
                "source_type": "apple_health",
                "record_type": hk_type,
                "modality": modality,
                "short_name": short_name,
                "value": round(avg, 1),
                "unit": unit,
                "timestamp": ts,
                "end_timestamp": None,
                "metadata": {
                    "aggregation": "daily_avg",
                    "min": round(stats["min"], 1),
                    "max": round(stats["max"], 1),
                    "sample_count": stats["count"],
                },
            })

    records.extend(workouts)
    logger.info(f"Parsed {len(records)} aggregated records from Apple Health export ({raw_count} raw readings)")
    return records


def stream_raw_readings(file: BinaryIO, source_id: str) -> tuple[list[tuple], int]:
    """Stream individual readings from Apple Health XML for bulk DB storage.

    Single-pass parsing of both Record and Workout elements.
    Returns (rows, count) where rows are tuples for bulk_insert_raw_readings:
    (source_id, record_type, modality, short_name, value, unit, timestamp, end_timestamp)
    """
    rows: list[tuple] = []
    count = 0

    context = etree.iterparse(file, events=("end",))
    for _, elem in context:
        if elem.tag not in _RECORD_TAGS:
            continue

        if elem.tag == "Workout":
            start = _parse_timestamp(elem.get("startDate"))
            if not start:
                _clear_elem(elem)
                continue

            workout_type = elem.get("workoutActivityType", "Unknown")
            duration = elem.get("duration")
            end = _parse_timestamp(elem.get("endDate"))

            rows.append((
                source_id,
                "HKWorkoutTypeIdentifier",
                "workout",
                workout_type.replace("HKWorkoutActivityType", ""),
                float(duration) if duration else None,
                "min",
                start.isoformat(),
                end.isoformat() if end else None,
            ))
            count += 1
            _clear_elem(elem)
            continue

        # Record element
        hk_type = elem.get("type", "")
        type_info = HEALTH_TYPE_MAP.get(hk_type)
        if not type_info:
            _clear_elem(elem)
            continue

        start = _parse_timestamp(elem.get("startDate"))
        if not start:
            _clear_elem(elem)
            continue

        modality, short_name = type_info
        value = _parse_value(elem.get("value"))
        unit = elem.get("unit") or ""
        end = _parse_timestamp(elem.get("endDate"))

        rows.append((
            source_id,
            hk_type,
            modality,
            short_name,
            float(value) if isinstance(value, (int, float)) else None,
            unit,
            start.isoformat(),
            end.isoformat() if end else None,
        ))
        count += 1
        _clear_elem(elem)

    logger.info(f"Streamed {count} individual readings for bulk storage")
    return rows, count
