"use strict";

const Database = require("better-sqlite3");
const path = require("path");

class HealthDB {
  constructor(dbPath) {
    this.dbPath = dbPath;
    this.db = null;
  }

  connect() {
    this.db = new Database(this.dbPath);
    this.db.pragma("journal_mode = WAL");
    this._createTables();
  }

  close() {
    if (this.db) {
      this.db.close();
      this.db = null;
    }
  }

  _createTables() {
    this.db.exec(`
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
    `);
  }

  clearSource(sourceId) {
    this.db.prepare("DELETE FROM raw_readings WHERE source_id = ?").run(sourceId);
    this.db.prepare("DELETE FROM metric_stats WHERE source_id = ?").run(sourceId);
  }

  bulkInsertRawReadings(rows) {
    const insert = this.db.prepare(`
      INSERT INTO raw_readings
        (source_id, record_type, modality, short_name, value, unit, timestamp, end_timestamp)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `);
    const tx = this.db.transaction((rows) => {
      for (const row of rows) {
        insert.run(...row);
      }
    });
    tx(rows);
    return rows.length;
  }

  rebuildMetricStats(sourceId) {
    this.db.prepare("DELETE FROM metric_stats WHERE source_id = ?").run(sourceId);

    const baseRows = this.db.prepare(`
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
    `).all(sourceId);

    const pctStmt = this.db.prepare(`
      WITH ranked AS (
        SELECT value,
               ROW_NUMBER() OVER (ORDER BY value) AS rn
        FROM raw_readings
        WHERE source_id = ? AND short_name = ? AND value IS NOT NULL
      )
      SELECT rn, value FROM ranked
      WHERE rn IN (?, ?, ?)
    `);

    const insertStats = this.db.prepare(`
      INSERT OR REPLACE INTO metric_stats
        (source_id, short_name, record_type, unit, modality,
         reading_count, earliest, latest, mean, min, max, median, p5, p95)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);

    const tx = this.db.transaction(() => {
      for (const r of baseRows) {
        const n = r.reading_count;
        const p5Row = Math.max(1, Math.floor(n * 0.05));
        const medRow = Math.max(1, Math.floor(n * 0.5));
        const p95Row = Math.max(1, Math.floor(n * 0.95));

        const pctRows = pctStmt.all(sourceId, r.short_name, p5Row, medRow, p95Row);
        const pct = {};
        for (const row of pctRows) {
          pct[row.rn] = row.value;
        }

        insertStats.run(
          sourceId, r.short_name, r.record_type, r.unit, r.modality,
          n, r.earliest, r.latest, r.mean, r.min, r.max,
          round2(pct[medRow] ?? r.mean),
          round2(pct[p5Row] ?? r.min),
          round2(pct[p95Row] ?? r.max)
        );
      }
    });
    tx();
  }

  listMetrics(sourceId) {
    return this.db.prepare(`
      SELECT * FROM metric_stats
      WHERE source_id = ?
      ORDER BY reading_count DESC
    `).all(sourceId);
  }

  queryReadings(sourceId, { recordType, shortName, start, end, limit = 500 } = {}) {
    let query = "SELECT * FROM raw_readings WHERE source_id = ?";
    const params = [sourceId];
    if (recordType) {
      query += " AND record_type = ?";
      params.push(recordType);
    }
    if (shortName) {
      query += " AND short_name LIKE ?";
      params.push(`%${shortName}%`);
    }
    if (start) {
      query += " AND timestamp >= ?";
      params.push(start);
    }
    if (end) {
      query += " AND timestamp <= ?";
      params.push(end);
    }
    query += " ORDER BY timestamp DESC LIMIT ?";
    params.push(limit);
    return this.db.prepare(query).all(...params);
  }

  aggregateReadings(sourceId, { period = "month", year, metric } = {}) {
    const fmt = period === "month" ? "%Y-%m" : "%Y";
    let query = `
      SELECT
        short_name, record_type, unit,
        strftime('${fmt}', timestamp) AS period,
        COUNT(*) AS reading_count,
        ROUND(AVG(value), 2) AS avg_value,
        ROUND(MIN(value), 2) AS min_value,
        ROUND(MAX(value), 2) AS max_value,
        ROUND(SUM(value), 2) AS total_value
      FROM raw_readings
      WHERE source_id = ?
    `;
    const params = [sourceId];
    if (year != null) {
      query += " AND strftime('%Y', timestamp) = ?";
      params.push(String(year));
    }
    if (metric) {
      query += " AND (short_name LIKE ? OR record_type LIKE ?)";
      params.push(`%${metric}%`, `%${metric}%`);
    }
    query += `
      GROUP BY short_name, record_type, unit, strftime('${fmt}', timestamp)
      ORDER BY period DESC, short_name
    `;
    return this.db.prepare(query).all(...params);
  }

  dailyAggregate(sourceId, { metrics, start, end, topN, order = "asc" } = {}) {
    let query = `
      SELECT
        CASE
          WHEN short_name = 'SleepAnalysis'
               AND CAST(strftime('%H', timestamp) AS INTEGER) < 12
          THEN DATE(timestamp, '-1 day')
          ELSE DATE(timestamp)
        END AS date,
        short_name, unit, modality,
        COUNT(*) AS count,
        ROUND(SUM(value), 2) AS sum,
        ROUND(AVG(value), 2) AS avg,
        ROUND(MIN(value), 2) AS min,
        ROUND(MAX(value), 2) AS max
      FROM raw_readings
      WHERE source_id = ?
    `;
    const params = [sourceId];

    if (metrics && metrics.length > 0) {
      const placeholders = metrics.map(() => "?").join(",");
      query += ` AND short_name IN (${placeholders})`;
      params.push(...metrics);
    }
    if (start) {
      query += " AND timestamp >= ?";
      params.push(start);
    }
    if (end) {
      const endVal = end.length === 10 ? end + "T23:59:59" : end;
      query += " AND timestamp <= ?";
      params.push(endVal);
    }

    query += " GROUP BY date, short_name, unit, modality ORDER BY date, short_name";

    const rows = this.db.prepare(query).all(...params);

    // Post-process: pick the right primary value per metric type
    for (const row of rows) {
      if (SUM_METRICS.has(row.short_name)) {
        row.value = row.sum;
        row.aggregation = "sum";
      } else if (row.modality === "workout") {
        row.value = row.sum;
        row.aggregation = "sum";
      } else {
        row.value = row.avg;
        row.aggregation = "avg";
      }
    }

    // Top-N / extremes
    if (topN && rows.length > 0) {
      const sortDesc = order.toLowerCase() === "desc";
      rows.sort((a, b) => {
        const cmp = a.short_name.localeCompare(b.short_name);
        if (cmp !== 0) return sortDesc ? -cmp : cmp;
        return sortDesc ? (b.value || 0) - (a.value || 0) : (a.value || 0) - (b.value || 0);
      });

      if (metrics && metrics.length === 1) {
        rows.length = Math.min(rows.length, topN);
      } else {
        const byMetric = {};
        for (const r of rows) {
          if (!byMetric[r.short_name]) byMetric[r.short_name] = [];
          byMetric[r.short_name].push(r);
        }
        const result = [];
        for (const group of Object.values(byMetric)) {
          result.push(...group.slice(0, topN));
        }
        result.sort((a, b) => a.date.localeCompare(b.date));
        return result;
      }
    }

    return rows;
  }

  dailyJoined(sourceId, { metrics, start, end } = {}) {
    const daily = this.dailyAggregate(sourceId, { metrics, start, end });

    const byDate = {};
    for (const row of daily) {
      const date = row.date;
      if (!byDate[date]) byDate[date] = { date };
      const name = row.short_name;

      if (row.modality === "workout") {
        byDate[date].workout_count = (byDate[date].workout_count || 0) + row.count;
        byDate[date].workout_duration = round1(
          (byDate[date].workout_duration || 0) + (row.sum || 0)
        );
      } else {
        byDate[date][name] = row.value;
        if (row.aggregation === "avg") {
          byDate[date][`${name}_min`] = row.min;
          byDate[date][`${name}_max`] = row.max;
        }
        byDate[date][`${name}_count`] = row.count;
      }
    }

    return Object.values(byDate).sort((a, b) => a.date.localeCompare(b.date));
  }

  readingCount(sourceId) {
    const row = this.db.prepare(
      "SELECT COUNT(*) as cnt FROM raw_readings WHERE source_id = ?"
    ).get(sourceId);
    return row ? row.cnt : 0;
  }
}

const SUM_METRICS = new Set([
  "Steps", "Distance", "ActiveEnergy", "BasalEnergy", "FlightsClimbed",
  "ExerciseTime", "StandTime", "Calories", "Protein", "Carbs", "Fat", "Water",
  "SleepAnalysis",
]);

function round2(v) {
  return v != null ? Math.round(v * 100) / 100 : null;
}

function round1(v) {
  return v != null ? Math.round(v * 10) / 10 : null;
}

module.exports = { HealthDB };
