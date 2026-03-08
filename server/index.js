#!/usr/bin/env node
"use strict";

const { McpServer } = require("@modelcontextprotocol/sdk/server/mcp.js");
const { StdioServerTransport } = require("@modelcontextprotocol/sdk/server/stdio.js");
const { z } = require("zod");
const path = require("path");
const fs = require("fs");
const { HealthDB } = require("./db.js");
const { streamRawReadings } = require("./parser.js");

const DATA_DIR = process.env.LEMNISCUS_DATA_DIR || path.join(__dirname, "..", "data");
const DB_PATH = path.join(DATA_DIR, "lemniscus.db");
const MANIFEST_PATH = path.join(DATA_DIR, ".indexed_files.json");
const SOURCE_ID = "local";

// ---------------------------------------------------------------------------
// Indexing
// ---------------------------------------------------------------------------

function loadManifest() {
  try {
    if (fs.existsSync(MANIFEST_PATH)) {
      return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf-8"));
    }
  } catch {}
  return {};
}

function saveManifest(manifest) {
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2));
}

function findXmlFiles(dir) {
  const results = [];
  if (!fs.existsSync(dir)) return results;
  const entries = fs.readdirSync(dir, { withFileTypes: true, recursive: true });
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    const name = entry.name;
    if (name.startsWith(".")) continue;
    if (!name.toLowerCase().endsWith(".xml")) continue;
    const fullPath = path.join(entry.parentPath || entry.path, name);
    results.push(fullPath);
  }
  return results.sort();
}

async function scanAndIndex(db) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  let manifest = loadManifest();
  let indexed = 0;
  let skipped = 0;
  const errors = [];
  let cleared = false;

  // If manifest says files are indexed but DB is empty, force re-index
  const dbCount = db.readingCount(SOURCE_ID);
  if (Object.keys(manifest).length > 0 && dbCount === 0) {
    console.error("Manifest exists but DB is empty — forcing re-index");
    manifest = {};
    saveManifest(manifest);
  }

  const xmlFiles = findXmlFiles(DATA_DIR);

  for (const filePath of xmlFiles) {
    const rel = path.relative(DATA_DIR, filePath);
    const stat = fs.statSync(filePath);
    const entry = manifest[rel];

    if (entry && entry.size === stat.size && entry.mtimeMs === stat.mtimeMs) {
      skipped++;
      continue;
    }

    if (!cleared) {
      console.error("Clearing old data for clean re-index...");
      db.clearSource(SOURCE_ID);
      manifest = {};
      cleared = true;
    }

    console.error(`Indexing ${rel}...`);
    try {
      const { rows, count } = await streamRawReadings(filePath, SOURCE_ID);
      if (rows.length > 0) {
        db.bulkInsertRawReadings(rows);
      }
      manifest[rel] = {
        size: stat.size,
        mtimeMs: stat.mtimeMs,
        readings: count,
        indexed_at: new Date().toISOString(),
      };
      indexed++;
      console.error(`  -> ${count} readings`);
    } catch (e) {
      console.error(`  -> Failed: ${e.message}`);
      errors.push({ file: rel, error: e.message });
    }
  }

  if (indexed > 0) {
    console.error("Building metric stats cache...");
    db.rebuildMetricStats(SOURCE_ID);
  }

  saveManifest(manifest);
  return { indexed, skipped, errors: errors.length };
}

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------

async function main() {
  const db = new HealthDB(DB_PATH);
  db.connect();
  fs.mkdirSync(DATA_DIR, { recursive: true });

  // Auto-index in background so server starts immediately
  scanAndIndex(db).catch((e) => console.error("Background indexing error:", e));

  const server = new McpServer({
    name: "lemniscus",
    version: "1.2.0",
  });

  server.tool(
    "list_metrics",
    "List all available health metrics with counts, date ranges, and descriptive stats. Call this first to see what data is available.",
    { source_id: z.string().default(SOURCE_ID).describe("Data source identifier") },
    async ({ source_id }) => {
      const metrics = db.listMetrics(source_id);
      const total = metrics.reduce((sum, m) => sum + m.reading_count, 0);
      return { content: [{ type: "text", text: JSON.stringify({ total_readings: total, metrics }) }] };
    }
  );

  server.tool(
    "query_readings",
    "Get individual health readings filtered by metric and date range. Use the metric parameter to filter by short_name (e.g. 'HeartRate', 'Steps'). Call list_metrics first to see available names.",
    {
      metric: z.string().optional().describe("Filter by short_name (e.g. 'HeartRate', 'Steps', 'HRV'). Partial match supported."),
      start: z.string().optional().describe("Start of time range (ISO 8601, e.g. '2024-01-01')"),
      end: z.string().optional().describe("End of time range (ISO 8601, e.g. '2024-01-31')"),
      limit: z.number().min(1).max(5000).default(500).describe("Maximum readings to return (1-5000, default 500)"),
      source_id: z.string().default(SOURCE_ID).describe("Data source identifier"),
    },
    async ({ metric, start, end, limit, source_id }) => {
      const rows = db.queryReadings(source_id, { shortName: metric, start, end, limit });
      return { content: [{ type: "text", text: JSON.stringify({ count: rows.length, readings: rows }) }] };
    }
  );

  server.tool(
    "get_summary",
    "Aggregated health statistics grouped by month or year. Returns avg/min/max/total for each metric. Best for trends and overviews.",
    {
      period: z.enum(["month", "year"]).default("month").describe("'month' or 'year'"),
      year: z.number().optional().describe("Optional year filter (e.g. 2024)"),
      metric: z.string().optional().describe("Optional metric filter (e.g. 'HeartRate', 'Steps'). Partial match."),
      source_id: z.string().default(SOURCE_ID).describe("Data source identifier"),
    },
    async ({ period, year, metric, source_id }) => {
      const rows = db.aggregateReadings(source_id, { period, year, metric });

      const metrics = {};
      for (const row of rows) {
        const key = row.short_name;
        if (!metrics[key]) {
          metrics[key] = {
            short_name: row.short_name,
            record_type: row.record_type,
            unit: row.unit,
            periods: [],
          };
        }
        metrics[key].periods.push({
          period: row.period,
          avg: row.avg_value,
          min: row.min_value,
          max: row.max_value,
          total: row.total_value,
          count: row.reading_count,
        });
      }

      return {
        content: [{
          type: "text",
          text: JSON.stringify({
            period_type: period,
            total_metrics: Object.keys(metrics).length,
            metrics: Object.values(metrics),
          }),
        }],
      };
    }
  );

  server.tool(
    "get_daily",
    "Get daily aggregated values for one or more metrics. Returns one row per day per metric with the appropriate aggregation: SUM for steps/energy, AVG for vitals, nightly grouping for sleep. Use top_n with order='desc' to find best/worst days.",
    {
      metrics: z.string().optional().describe("Comma-separated metric short_names (e.g. 'Steps,HeartRate,SleepAnalysis'). None = all."),
      start: z.string().optional().describe("Start date (ISO 8601, e.g. '2024-01-01')"),
      end: z.string().optional().describe("End date (ISO 8601, e.g. '2024-12-31')"),
      top_n: z.number().optional().describe("Only return top N days per metric, ordered by primary value"),
      order: z.enum(["asc", "desc"]).default("asc").describe("'asc' (lowest first) or 'desc' (highest first). Used with top_n."),
      source_id: z.string().default(SOURCE_ID).describe("Data source identifier"),
    },
    async ({ metrics, start, end, top_n, order, source_id }) => {
      const metricList = metrics ? metrics.split(",").map((m) => m.trim()) : undefined;
      const rows = db.dailyAggregate(source_id, {
        metrics: metricList,
        start,
        end,
        topN: top_n,
        order,
      });
      return { content: [{ type: "text", text: JSON.stringify({ count: rows.length, days: rows }) }] };
    }
  );

  server.tool(
    "get_daily_joined",
    "Get a multi-metric daily table — one row per date, one column per metric. Perfect for cross-metric analysis. Workout types are merged into workout_count and workout_duration columns.",
    {
      metrics: z.string().describe("Comma-separated metric short_names (e.g. 'Steps,RestingHR,SleepAnalysis,ActiveEnergy')"),
      start: z.string().optional().describe("Start date (ISO 8601, e.g. '2024-01-01')"),
      end: z.string().optional().describe("End date (ISO 8601, e.g. '2024-12-31')"),
      source_id: z.string().default(SOURCE_ID).describe("Data source identifier"),
    },
    async ({ metrics, start, end, source_id }) => {
      const metricList = metrics.split(",").map((m) => m.trim());
      const rows = db.dailyJoined(source_id, { metrics: metricList, start, end });
      return { content: [{ type: "text", text: JSON.stringify({ count: rows.length, days: rows }) }] };
    }
  );

  server.tool(
    "reindex",
    "Re-scan the data folder and index any new or modified Apple Health exports. Call this after adding new XML files to the data folder.",
    {},
    async () => {
      const result = await scanAndIndex(db);
      return { content: [{ type: "text", text: JSON.stringify(result) }] };
    }
  );

  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((e) => {
  console.error("Fatal:", e);
  process.exit(1);
});
