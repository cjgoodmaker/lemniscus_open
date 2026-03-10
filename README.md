# Lemniscus

> **Disclaimer:** This software is provided for **research and development purposes only**. Lemniscus is **not a medical device, diagnostic tool, or clinical product**. It has not been evaluated, approved, or cleared by the FDA or any other regulatory authority. LLM outputs may be inaccurate, incomplete, or misleading. **Do not use this software to make medical decisions.** Always consult a qualified healthcare professional for medical advice, diagnosis, or treatment. Use at your own risk.

A local MCP server that lets Claude query your Apple Health data. Export from your iPhone, point to the folder, ask questions in natural language. All data stays on your machine.

## Install

### Option A: Claude Desktop Extension (easiest)

1. Download `lemniscus.mcpb` from [Releases](https://github.com/cjgoodmaker/lemniscus_open/releases)
2. Open Claude Desktop → Settings → Extensions
3. Drag the file in and select your folder containing `export.xml`
4. Start chatting

### Option B: Claude Code (developer)

```bash
git clone https://github.com/cjgoodmaker/lemniscus_open.git
cd lemniscus_open && bash setup.sh
```

Copy your `export.xml` into `data/`, then run `claude`. The MCP server connects automatically via `.mcp.json`.

## Export from iPhone

1. Open the **Health** app → tap your profile picture (top-right)
2. Scroll down → **Export All Health Data**
3. AirDrop or transfer the zip to your Mac, unzip, and copy `export.xml` to your data folder

## Tools

| Tool | Description |
|------|-------------|
| `list_metrics` | Available metrics with counts, date ranges, and descriptive stats |
| `get_daily` | Daily aggregates — sum for steps/energy, avg for vitals, nightly sleep grouping |
| `get_daily_joined` | Multi-metric pivot table — one row per date, one column per metric |
| `get_summary` | Monthly or yearly aggregates (avg/min/max/total) |
| `query_readings` | Individual raw readings filtered by metric and date range |
| `reindex` | Re-scan data folder for new or modified exports |

## Examples

### 1. Discover available metrics

> **You:** What health data do I have?

Claude calls `list_metrics` and returns something like:

```
Total readings: 2,847,312
Metrics:
  HeartRate       — 1,203,456 readings (2019-06-15 to 2026-03-07) avg 72.3 bpm
  Steps           —   412,890 readings (2019-06-15 to 2026-03-07) avg 8,241/day
  SleepAnalysis   —    45,210 readings (2020-01-01 to 2026-03-07)
  RestingHR       —     2,190 readings (2020-03-01 to 2026-03-07) avg 58.1 bpm
  HRV             —     2,044 readings (2020-03-01 to 2026-03-07) avg 42.7 ms
  ... 50+ more metrics
```

### 2. Analyse daily trends

> **You:** How were my steps and resting heart rate last month?

Claude calls `get_daily_joined` with `metrics=Steps,RestingHR` and `start=2026-02-01`, `end=2026-02-28`, then summarises:

```
February 2026:
  Steps — avg 9,102/day, best day Feb 15 (16,340), lowest Feb 3 (2,110)
  Resting HR — avg 57 bpm, range 52–63 bpm
  Correlation: on your highest step days, resting HR tended to be 2-3 bpm lower
```

### 3. Compare sleep and activity over time

> **You:** Compare my sleep duration and active energy month over month for the last year

Claude calls `get_summary` with `period=month`, `year=2025`, then chains a second call for 2026. It returns:

```
Monthly averages (2025):
  Jan: 7.1h sleep, 520 kcal active    Jul: 6.8h sleep, 610 kcal active
  Feb: 7.3h sleep, 490 kcal active    Aug: 6.5h sleep, 640 kcal active
  Mar: 7.0h sleep, 550 kcal active    Sep: 6.9h sleep, 580 kcal active
  ...
  Trend: sleep decreased slightly over summer while activity increased.
```

## How It Works

```
iPhone → Health app → Export All Health Data
  ↓
export.xml — your raw Apple Health data
  ↓
Parser — streams millions of readings from XML
  ↓
SQLite — indexed readings + pre-computed stats (mean, median, p5, p95)
  ↓
MCP Server — 6 composable tools over stdio
  ↓
Claude — chains tools to answer your questions
```

All processing happens locally. No network calls, no cloud services, no AI models bundled. Just structured queries over your data.

## Privacy

All data stays on your machine. No telemetry, no analytics, no network connections. See the full [Privacy Policy](PRIVACY.md).

When Claude queries your data through Lemniscus tools, the returned results are sent to Anthropic's API as part of your conversation, subject to [Anthropic's privacy policy](https://www.anthropic.com/privacy).

## Troubleshooting

**"No data indexed yet"**
Place your Apple Health `export.xml` in the data folder you selected during setup, then ask Claude to "reindex my health data".

**Duplicate readings**
Ask Claude to "reindex with force=true". This drops all existing data and re-indexes from scratch.

**Extension disconnects in Claude Desktop**
Quit and reopen Claude Desktop. The extension restarts automatically. Check logs in Claude Desktop → Settings → Extensions → Lemniscus.

**Indexing takes a long time**
Large exports (600MB+, 1M+ readings) can take 1-2 minutes on first index. Subsequent startups skip already-indexed files.

**Missing metrics**
Run `list_metrics` to see all indexed data. Lemniscus indexes every Record and Workout element in your export — if a metric doesn't appear, it's not in your export.xml.

## License

MIT
