"""Domain models for the lightweight health data server."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class Modality(enum.StrEnum):
    """Type of health data."""

    VITALS = "vitals"
    ACTIVITY = "activity"
    SLEEP = "sleep"
    BODY = "body"
    NUTRITION = "nutrition"
    FITNESS = "fitness"
    MINDFULNESS = "mindfulness"
    WORKOUT = "workout"
    OTHER = "other"


class HealthRecord(BaseModel):
    """A standardized health data record from any wearable source."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_id: str = Field(description="User or device identifier")
    source_type: str = Field(description="apple_health, oura, garmin")
    modality: Modality
    record_type: str = Field(description="Original type identifier from source")
    value: float | str | None = None
    unit: str | None = None
    timestamp: datetime
    end_timestamp: datetime | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    ingested_at: datetime = Field(default_factory=datetime.utcnow)


class TimelineEntry(BaseModel):
    """An entry on the health timeline, ready for embedding and search."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_id: str
    record_id: uuid.UUID
    timestamp: datetime
    modality: Modality
    summary: str
    metadata: dict[str, object] = Field(default_factory=dict)
