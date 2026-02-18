"""SQLite + sqlite-vec database layer."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import sqlite_vec

from models import HealthRecord, Modality, TimelineEntry

VECTOR_SIZE = 384


def _adapt_uuid(val: uuid.UUID) -> str:
    return str(val)


def _convert_uuid(val: bytes) -> uuid.UUID:
    return uuid.UUID(val.decode())


sqlite3.register_adapter(uuid.UUID, _adapt_uuid)
sqlite3.register_converter("UUID", _convert_uuid)


class Database:
    """SQLite database with FTS5 and sqlite-vec for vector search."""

    def __init__(self, db_path: str = "lemniscus.db") -> None:
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.conn = sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self._create_tables()

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def _create_tables(self) -> None:
        c = self.conn
        c.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                modality TEXT NOT NULL,
                record_type TEXT NOT NULL,
                value TEXT,
                unit TEXT,
                timestamp TEXT NOT NULL,
                end_timestamp TEXT,
                metadata TEXT DEFAULT '{}',
                ingested_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_records_source ON records(source_id);
            CREATE INDEX IF NOT EXISTS idx_records_timestamp ON records(timestamp);
            CREATE INDEX IF NOT EXISTS idx_records_type ON records(record_type);

            CREATE TABLE IF NOT EXISTS raw_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                record_type TEXT NOT NULL,
                modality TEXT NOT NULL,
                short_name TEXT NOT NULL,
                value REAL,
                unit TEXT,
                timestamp TEXT NOT NULL,
                end_timestamp TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_raw_source_type_ts ON raw_readings(source_id, record_type, timestamp);
            CREATE INDEX IF NOT EXISTS idx_raw_modality ON raw_readings(modality);

            CREATE TABLE IF NOT EXISTS timeline_entries (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                record_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                modality TEXT NOT NULL,
                summary TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (record_id) REFERENCES records(id)
            );

            CREATE INDEX IF NOT EXISTS idx_timeline_source ON timeline_entries(source_id);
            CREATE INDEX IF NOT EXISTS idx_timeline_timestamp ON timeline_entries(timestamp);
            CREATE INDEX IF NOT EXISTS idx_timeline_modality ON timeline_entries(modality);
        """)

        # FTS5 for keyword search
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                entry_id,
                source_id,
                modality,
                content,
                tokenize='porter unicode61'
            )
        """)

        # sqlite-vec for vector search
        c.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS embeddings USING vec0(
                entry_id TEXT,
                embedding float[{VECTOR_SIZE}]
            )
        """)

        c.commit()

    # --- Record operations ---

    def insert_record(self, record: HealthRecord) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO records
               (id, source_id, source_type, modality, record_type, value, unit,
                timestamp, end_timestamp, metadata, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(record.id),
                record.source_id,
                record.source_type,
                record.modality.value,
                record.record_type,
                str(record.value) if record.value is not None else None,
                record.unit,
                record.timestamp.isoformat(),
                record.end_timestamp.isoformat() if record.end_timestamp else None,
                json.dumps(record.metadata, default=str),
                record.ingested_at.isoformat(),
            ),
        )

    def bulk_insert_raw_readings(self, rows: list[tuple]) -> int:
        """Bulk insert raw readings efficiently. Each tuple:
        (source_id, record_type, modality, short_name, value, unit, timestamp, end_timestamp)
        """
        self.conn.executemany(
            """INSERT INTO raw_readings
               (source_id, record_type, modality, short_name, value, unit, timestamp, end_timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def query_raw_readings(
        self,
        source_id: str,
        record_type: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Query individual raw readings for drill-down."""
        query = "SELECT * FROM raw_readings WHERE source_id = ?"
        params: list = [source_id]
        if record_type:
            query += " AND record_type = ?"
            params.append(record_type)
        if start:
            query += " AND timestamp >= ?"
            params.append(start)
        if end:
            query += " AND timestamp <= ?"
            params.append(end)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # --- Timeline operations ---

    def insert_timeline_entry(self, entry: TimelineEntry) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO timeline_entries
               (id, source_id, record_id, timestamp, modality, summary, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(entry.id),
                entry.source_id,
                str(entry.record_id),
                entry.timestamp.isoformat(),
                entry.modality.value,
                entry.summary,
                json.dumps(entry.metadata, default=str),
            ),
        )

    def get_timeline_entry(self, entry_id: str) -> TimelineEntry | None:
        row = self.conn.execute(
            "SELECT * FROM timeline_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_timeline_entry(row)

    def list_timeline(
        self,
        source_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
        modality: str | None = None,
        limit: int = 500,
    ) -> list[TimelineEntry]:
        query = "SELECT * FROM timeline_entries WHERE source_id = ?"
        params: list = [source_id]

        if start:
            query += " AND timestamp >= ?"
            params.append(start.isoformat())
        if end:
            query += " AND timestamp <= ?"
            params.append(end.isoformat())
        if modality:
            query += " AND modality = ?"
            params.append(modality)

        query += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_timeline_entry(r) for r in rows]

    # --- Embedding operations ---

    def insert_embedding(self, entry_id: str, vector: list[float], content: str, source_id: str, modality: str) -> None:
        # Store vector
        self.conn.execute(
            "INSERT INTO embeddings (entry_id, embedding) VALUES (?, ?)",
            (entry_id, sqlite_vec.serialize_float32(vector)),
        )
        # Store FTS content
        self.conn.execute(
            "INSERT INTO chunks_fts (entry_id, source_id, modality, content) VALUES (?, ?, ?, ?)",
            (entry_id, source_id, modality, content),
        )

    def commit(self) -> None:
        """Explicit commit — call after batch operations."""
        self.conn.commit()

    def search_vectors(self, query_vector: list[float], top_k: int = 20) -> list[tuple[str, float]]:
        rows = self.conn.execute(
            """SELECT entry_id, distance
               FROM embeddings
               WHERE embedding MATCH ?
               ORDER BY distance
               LIMIT ?""",
            (sqlite_vec.serialize_float32(query_vector), top_k),
        ).fetchall()
        return [(row["entry_id"], float(row["distance"])) for row in rows]

    def search_fts(self, query: str, source_id: str, top_k: int = 20) -> list[tuple[str, float]]:
        rows = self.conn.execute(
            """SELECT entry_id, rank
               FROM chunks_fts
               WHERE chunks_fts MATCH ? AND source_id = ?
               ORDER BY rank
               LIMIT ?""",
            (query, source_id, top_k),
        ).fetchall()
        return [(row["entry_id"], -float(row["rank"])) for row in rows]

    # --- Helpers ---

    @staticmethod
    def _row_to_timeline_entry(row: sqlite3.Row) -> TimelineEntry:
        return TimelineEntry(
            id=uuid.UUID(row["id"]),
            source_id=row["source_id"],
            record_id=uuid.UUID(row["record_id"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            modality=Modality(row["modality"]),
            summary=row["summary"],
            metadata=json.loads(row["metadata"]),
        )
