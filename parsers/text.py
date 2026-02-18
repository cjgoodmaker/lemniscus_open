"""Plain text file parser — chunk and embed text content for search."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, BinaryIO

logger = logging.getLogger(__name__)


def parse_text(file: BinaryIO) -> list[dict[str, Any]]:
    """Parse a plain text file and return it as a searchable record.

    The full text content is stored in metadata["text_content"]
    and used as the embedding summary. The chunker in the pipeline
    will split it into overlapping segments for embedding.
    """
    content = file.read().decode("utf-8", errors="replace").strip()
    if not content:
        return []

    logger.info(f"Text parser: {len(content)} chars")

    return [{
        "record_type": "document_text",
        "value": None,
        "unit": "",
        "timestamp": datetime.utcnow(),
        "modality": "other",
        "short_name": "Text document",
        "metadata": {
            "char_count": len(content),
            "text_content": content,
        },
    }]
