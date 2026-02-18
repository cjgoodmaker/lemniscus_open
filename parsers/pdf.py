"""PDF document parser — extract text per page for embedding and search."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, BinaryIO

from pypdf import PdfReader

logger = logging.getLogger(__name__)


def parse_pdf(file: BinaryIO) -> list[dict[str, Any]]:
    """Parse a PDF and return one record per page with extracted text.

    Each page becomes a searchable record. The text content is stored
    in metadata["text_content"] and used as the embedding summary.
    """
    reader = PdfReader(file)
    total_pages = len(reader.pages)
    records: list[dict[str, Any]] = []

    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        text = text.strip()
        if not text:
            continue

        records.append({
            "record_type": "document_page",
            "value": None,
            "unit": "",
            "timestamp": datetime.utcnow(),
            "modality": "other",
            "short_name": f"Page {i + 1}",
            "metadata": {
                "page_number": i + 1,
                "total_pages": total_pages,
                "text_content": text,
            },
        })

    logger.info(f"Extracted text from {len(records)}/{total_pages} pages")
    return records
