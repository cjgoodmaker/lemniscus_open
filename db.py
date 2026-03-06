"""SQLite database layer — structured health readings only."""

from __future__ import annotations

import sqlite3


class Database:
    """SQLite database for Apple Health readings."""

    def __init__(self, db_path: str = "lemniscus.db") -> None:
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.conn = sqlite3.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
            timeout=30,
        )
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass  # Another instance already set WAL mode
        self._create_tables()

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def _create_tables(self) -> None:
        self.conn.executescript("""
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

            CREATE INDEX IF NOT EXISTS idx_raw_source_type_ts
                ON raw_readings(source_id, record_type, timestamp);
            CREATE INDEX IF NOT EXISTS idx_raw_short_name
                ON raw_readings(short_name);
            CREATE INDEX IF NOT EXISTS idx_raw_timestamp
                ON raw_readings(timestamp);

            CREATE TABLE IF NOT EXISTS metric_stats (
                source_id TEXT NOT NULL,
                short_name TEXT NOT NULL,
                record_type TEXT NOT NULL,
                unit TEXT,
                modality TEXT NOT NULL,
                reading_count INTEGER NOT NULL,
                earliest TEXT,
                latest TEXT,
                mean REAL,
                min REAL,
                max REAL,
                median REAL,
                p5 REAL,
                p95 REAL,
                PRIMARY KEY (source_id, short_name)
            );
        """)
        self.conn.commit()

    def clear_source(self, source_id: str) -> None:
        """Remove all data for a source so it can be cleanly re-indexed."""
        self.conn.execute("DELETE FROM raw_readings WHERE source_id = ?", (source_id,))
        self.conn.execute("DELETE FROM metric_stats WHERE source_id = ?", (source_id,))
        self.conn.commit()

    def bulk_insert_raw_readings(self, rows: list[tuple]) -> int:
        """Bulk insert raw readings. Each tuple:
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

    def rebuild_metric_stats(self, source_id: str) -> None:
        """Materialise descriptive stats per metric. Called after indexing."""
        self.conn.execute("DELETE FROM metric_stats WHERE source_id = ?", (source_id,))

        # Base aggregates in one pass
        rows = self.conn.execute("""
            SELECT short_name, record_type, unit, modality,
                   COUNT(*) as reading_count,
                   MIN(timestamp) as earliest,
                   MAX(timestamp) as latest,
                   ROUND(AVG(value), 2) as mean,
                   ROUND(MIN(value), 2) as min,
                   ROUND(MAX(value), 2) as max
            FROM raw_readings
            WHERE source_id = ? AND value IS NOT NULL
            GROUP BY short_name, record_type, unit, modality
        """, (source_id,)).fetchall()

        for r in rows:
            n = r["reading_count"]
            p5_row = max(1, int(n * 0.05))
            med_row = max(1, int(n * 0.50))
            p95_row = max(1, int(n * 0.95))

            pct_rows = self.conn.execute("""
                WITH ranked AS (
                    SELECT value,
                           ROW_NUMBER() OVER (ORDER BY value) AS rn
                    FROM raw_readings
                    WHERE source_id = ? AND short_name = ? AND value IS NOT NULL
                )
                SELECT rn, value FROM ranked
                WHERE rn IN (?, ?, ?)
            """, (source_id, r["short_name"], p5_row, med_row, p95_row)).fetchall()

            pct = {row["rn"]: row["value"] for row in pct_rows}

            self.conn.execute("""
                INSERT OR REPLACE INTO metric_stats
                (source_id, short_name, record_type, unit, modality,
                 reading_count, earliest, latest, mean, min, max, median, p5, p95)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                source_id, r["short_name"], r["record_type"], r["unit"], r["modality"],
                n, r["earliest"], r["latest"], r["mean"], r["min"], r["max"],
                round(pct.get(med_row, r["mean"]), 2),
                round(pct.get(p5_row, r["min"]), 2),
                round(pct.get(p95_row, r["max"]), 2),
            ))

        self.conn.commit()

    def list_metrics(self, source_id: str) -> list[dict]:
        """List all metrics with cached descriptive stats. Instant read."""
        rows = self.conn.execute("""
            SELECT * FROM metric_stats
            WHERE source_id = ?
            ORDER BY reading_count DESC
        """, (source_id,)).fetchall()
        return [dict(r) for r in rows]

    def query_readings(
        self,
        source_id: str,
        record_type: str | None = None,
        short_name: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Query individual readings with optional filters."""
        query = "SELECT * FROM raw_readings WHERE source_id = ?"
        params: list = [source_id]
        if record_type:
            query += " AND record_type = ?"
            params.append(record_type)
        if short_name:
            query += " AND short_name LIKE ?"
            params.append(f"%{short_name}%")
        if start:
            query += " AND timestamp >= ?"
            params.append(start)
        if end:
            query += " AND timestamp <= ?"
            params.append(end)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def aggregate_readings(
        self,
        source_id: str,
        period: str = "month",
        year: int | None = None,
        metric: str | None = None,
    ) -> list[dict]:
        """Aggregate readings by month or year. Returns avg/min/max/total."""
        fmt = "%Y-%m" if period == "month" else "%Y"
        query = f"""
            SELECT
                short_name, record_type, unit,
                strftime('{fmt}', timestamp) AS period,
                COUNT(*) AS reading_count,
                ROUND(AVG(value), 2) AS avg_value,
                ROUND(MIN(value), 2) AS min_value,
                ROUND(MAX(value), 2) AS max_value,
                ROUND(SUM(value), 2) AS total_value
            FROM raw_readings
            WHERE source_id = ?
        """
        params: list = [source_id]
        if year is not None:
            query += " AND strftime('%Y', timestamp) = ?"
            params.append(str(year))
        if metric:
            query += " AND (short_name LIKE ? OR record_type LIKE ?)"
            params.extend([f"%{metric}%", f"%{metric}%"])
        query += f"""
            GROUP BY short_name, record_type, unit, strftime('{fmt}', timestamp)
            ORDER BY period DESC, short_name
        """
        return [dict(r) for r in self.conn.execute(query, params).fetchall()]

    def reading_count(self, source_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM raw_readings WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        return row["cnt"] if row else 0
