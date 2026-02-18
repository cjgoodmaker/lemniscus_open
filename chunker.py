"""Text chunking for the embedding pipeline."""

from __future__ import annotations


def chunk_text(
    text: str,
    max_length: int = 512,
    overlap: int = 50,
) -> list[str]:
    """Chunk text into overlapping word-based segments.

    Args:
        text: Text to chunk.
        max_length: Maximum words per chunk.
        overlap: Number of words to overlap between chunks.

    Returns:
        List of text chunks.
    """
    if not text or not text.strip():
        return []

    words = text.split()

    if len(words) <= max_length:
        return [text]

    chunks = []
    start = 0

    while start < len(words):
        end = min(start + max_length, len(words))
        chunks.append(" ".join(words[start:end]))
        start = end - overlap
        if start >= len(words):
            break

    return chunks
