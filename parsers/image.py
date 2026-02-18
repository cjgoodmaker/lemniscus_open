"""Image file parser — store metadata and file path for tracking."""

from __future__ import annotations

import logging
import struct
from datetime import datetime
from typing import Any, BinaryIO

logger = logging.getLogger(__name__)


def parse_image(file: BinaryIO) -> list[dict[str, Any]]:
    """Parse an image file and return a metadata record.

    Extracts EXIF date if available (JPEG). For now, no OCR —
    just stores the image as a trackable record with its date.
    """
    exif_date = _extract_exif_date(file)
    file.seek(0)

    timestamp = exif_date or datetime.utcnow()

    return [{
        "record_type": "image",
        "value": None,
        "unit": "",
        "timestamp": timestamp,
        "modality": "other",
        "short_name": "Photo",
        "metadata": {
            "content_type": "image",
            "has_exif_date": exif_date is not None,
            "text_content": f"Photo taken {timestamp.strftime('%Y-%m-%d')}",
        },
    }]


def _extract_exif_date(file: BinaryIO) -> datetime | None:
    """Try to extract DateTimeOriginal from JPEG EXIF data."""
    try:
        header = file.read(2)
        if header != b"\xff\xd8":  # Not a JPEG
            return None

        while True:
            marker = file.read(2)
            if len(marker) < 2:
                break
            if marker[0] != 0xFF:
                break

            # APP1 marker (EXIF)
            if marker[1] == 0xE1:
                size_bytes = file.read(2)
                if len(size_bytes) < 2:
                    break
                size = struct.unpack(">H", size_bytes)[0]
                data = file.read(size - 2)

                # Look for "DateTimeOriginal" tag value in raw bytes
                # EXIF date format: "YYYY:MM:DD HH:MM:SS"
                idx = data.find(b"DateTimeOriginal")
                if idx == -1:
                    idx = data.find(b"DateTime")
                if idx != -1:
                    # Search forward for the date string pattern
                    search_start = idx
                    search_region = data[search_start:search_start + 100]
                    for offset in range(len(search_region) - 19):
                        chunk = search_region[offset:offset + 19]
                        try:
                            text = chunk.decode("ascii")
                            if len(text) == 19 and text[4] == ":" and text[7] == ":":
                                dt = datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
                                return dt
                        except (UnicodeDecodeError, ValueError):
                            continue
                return None

            # Skip other markers
            size_bytes = file.read(2)
            if len(size_bytes) < 2:
                break
            size = struct.unpack(">H", size_bytes)[0]
            file.seek(size - 2, 1)

    except Exception as e:
        logger.debug(f"EXIF extraction failed: {e}")

    return None
