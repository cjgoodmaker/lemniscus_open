"""Multi-strategy search: vector + keyword + temporal ranking."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from db import Database
from embedder import Embedder
from models import TimelineEntry


class SearchResult:
    """A search result with score and metadata."""

    def __init__(
        self,
        entry_id: str,
        score: float,
        source: str,
        rank: int,
        entry: TimelineEntry | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.score = score
        self.source = source
        self.rank = rank
        self.entry = entry


def search(
    query: str,
    source_id: str,
    db: Database,
    embedder: Embedder,
    top_k: int = 10,
    rrf_k: int = 60,
    temporal_decay_halflife_days: int = 180,
    modalities: list[str] | None = None,
) -> dict[str, Any]:
    """Execute multi-strategy search and return context package.

    Combines:
    1. Vector similarity (sqlite-vec)
    2. Keyword search (FTS5)
    3. RRF fusion
    4. Temporal decay
    """
    top_k = max(1, min(top_k, 100))

    # 1. Vector search
    query_vector = embedder.embed_single(query)
    raw_vector = db.search_vectors(query_vector, top_k=top_k * 3)

    vector_results: list[SearchResult] = []
    for rank, (eid, distance) in enumerate(raw_vector, start=1):
        entry = db.get_timeline_entry(eid)
        if not entry or entry.source_id != source_id:
            continue
        if modalities and entry.modality.value not in modalities:
            continue
        # Convert distance to similarity (sqlite-vec returns L2 distance by default)
        score = 1.0 / (1.0 + distance)
        vector_results.append(SearchResult(entry_id=eid, score=score, source="vector", rank=rank, entry=entry))

    # 2. Keyword search (FTS5)
    raw_fts = db.search_fts(query, source_id, top_k=top_k * 3)

    fts_results: list[SearchResult] = []
    for rank, (eid, score) in enumerate(raw_fts, start=1):
        entry = db.get_timeline_entry(eid)
        if not entry:
            continue
        if modalities and entry.modality.value not in modalities:
            continue
        fts_results.append(SearchResult(entry_id=eid, score=score, source="fts", rank=rank, entry=entry))

    # 3. RRF fusion
    result_lists: dict[str, list[SearchResult]] = {}
    if vector_results:
        result_lists["vector"] = vector_results
    if fts_results:
        result_lists["fts"] = fts_results

    if not result_lists:
        return _empty_response(source_id, query)

    fused = reciprocal_rank_fusion(result_lists, k=rrf_k)

    # 4. Temporal decay
    ranked = apply_temporal_decay(fused, halflife_days=temporal_decay_halflife_days)

    # 5. Deduplicate
    deduped = deduplicate_results(ranked)

    # 6. Take top K
    top_results = deduped[:top_k]

    # 7. Compose context package
    return compose_context_package(top_results, source_id, query)


# --- RRF ---

def reciprocal_rank_fusion(
    result_lists: dict[str, list[SearchResult]],
    k: int = 60,
) -> list[SearchResult]:
    """Combine multiple ranked lists using Reciprocal Rank Fusion."""
    scores: dict[str, float] = defaultdict(float)
    entries: dict[str, SearchResult] = {}

    for source, results in result_lists.items():
        for rank, result in enumerate(results, start=1):
            rrf_score = 1.0 / (k + rank)
            scores[result.entry_id] += rrf_score
            if result.entry_id not in entries:
                entries[result.entry_id] = result

    ranked_ids = sorted(scores.keys(), key=lambda eid: scores[eid], reverse=True)

    return [
        SearchResult(
            entry_id=eid,
            score=scores[eid],
            source="rrf",
            rank=rank,
            entry=entries[eid].entry,
        )
        for rank, eid in enumerate(ranked_ids, start=1)
    ]


# --- Temporal Decay ---

def apply_temporal_decay(
    results: list[SearchResult],
    halflife_days: int = 180,
    reference_time: datetime | None = None,
) -> list[SearchResult]:
    """Apply exponential temporal decay: score * 2^(-days / halflife)."""
    if not results:
        return results

    ref = reference_time or datetime.now(timezone.utc)

    decayed = []
    for r in results:
        if r.entry is None:
            decayed.append(r)
            continue

        ts = r.entry.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        days_diff = (ref - ts).total_seconds() / 86400
        decay_factor = math.pow(2, -days_diff / halflife_days)

        decayed.append(
            SearchResult(
                entry_id=r.entry_id,
                score=r.score * decay_factor,
                source=r.source,
                rank=r.rank,
                entry=r.entry,
            )
        )

    decayed.sort(key=lambda x: x.score, reverse=True)
    for rank, r in enumerate(decayed, start=1):
        r.rank = rank

    return decayed


# --- Dedup ---

def deduplicate_results(results: list[SearchResult]) -> list[SearchResult]:
    """Deduplicate results using entry metadata dedup_key."""
    if not results:
        return results

    by_key: dict[str, SearchResult] = {}
    for r in results:
        key = r.entry.metadata.get("dedup_key", r.entry_id) if r.entry else r.entry_id
        if key not in by_key or r.score > by_key[key].score:
            by_key[key] = r

    deduped = sorted(by_key.values(), key=lambda x: x.score, reverse=True)
    for rank, r in enumerate(deduped, start=1):
        r.rank = rank
    return deduped


# --- Context Package ---

def compose_context_package(
    results: list[SearchResult],
    source_id: str,
    query: str,
) -> dict[str, Any]:
    """Compose context package from ranked results."""
    entries = [r.entry for r in results if r.entry]
    entries.sort(key=lambda e: e.timestamp)

    if not entries:
        return _empty_response(source_id, query)

    by_modality: dict[str, list[TimelineEntry]] = defaultdict(list)
    for entry in entries:
        by_modality[entry.modality.value].append(entry)

    time_start = entries[0].timestamp
    time_end = entries[-1].timestamp
    span_days = (time_end - time_start).days

    lines = [
        f"Retrieved {len(entries)} entries for query: '{query}'",
        f"Time range: {time_start.isoformat()} to {time_end.isoformat()} ({span_days} days)",
        f"Modalities: {', '.join(f'{mod} ({len(items)})' for mod, items in sorted(by_modality.items()))}",
        "",
    ]

    for mod, mod_entries in sorted(by_modality.items()):
        lines.append(f"[{mod}] ({len(mod_entries)} entries)")
        for entry in mod_entries:
            lines.append(f"  {entry.timestamp.isoformat()} | {entry.summary}")
        lines.append("")

    narrative = "\n".join(lines)

    structured_data = {
        mod: [
            {
                "timestamp": e.timestamp.isoformat(),
                "summary": e.summary,
                "metadata": e.metadata,
            }
            for e in mod_entries
        ]
        for mod, mod_entries in by_modality.items()
    }

    provenance = [
        {
            "entry_id": r.entry_id,
            "score": r.score,
            "rank": r.rank,
            "source": r.source,
            "timestamp": r.entry.timestamp.isoformat() if r.entry else None,
        }
        for r in results
    ]

    token_estimate = int(len(narrative.split()) * 1.3)

    return {
        "narrative": narrative,
        "structured_data": structured_data,
        "provenance": provenance,
        "token_estimate": token_estimate,
    }


def _empty_response(source_id: str, query: str) -> dict[str, Any]:
    return {
        "narrative": f"No relevant data found for source {source_id}.",
        "structured_data": {},
        "provenance": [],
        "token_estimate": 0,
    }
