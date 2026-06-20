# AI Usage Monitor

AI Usage Monitor is a local dashboard for tracking token consumption and estimated cost across Claude Code, Codex, and OpenCode. It reads each tool's local session data, stores normalized usage events in SQLite, and presents them through a browser-based dashboard.

No proxy or provider API key is required.

![Dashboard overview](docs/dashboard-overview.png)

## Features

- Usage and estimated cost by tool and model
- Daily token and cost trends
- Cache hit rate, request totals, and monthly projections
- Live event stream with automatic refresh
- Per-session usage breakdowns
- Activity heatmap, tool usage, and command statistics
- Lines-of-code-by-language from edit operations
- Incremental collection with event deduplication
- Optional daily summaries written to an Obsidian vault

## Supported data sources

| Tool | Local source |
| --- | --- |
| Claude Code | `~/.claude/projects/**/*.jsonl` |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` |
| OpenCode | `~/.local/share/opencode/opencode.db` |

Pricing is defined in `monitor/pricing.py` and should be reviewed periodically as provider pricing changes. Models without a defined rate are recorded with zero cost.

## Requirements

- Linux
- Python 3
- systemd user services for the standard installation

## Installation

```bash
git clone https://github.com/Anuraj-dev/ai-usage-monitor.git
cd ai-usage-monitor
./install.sh
```

The installer creates a virtual environment, installs the Python dependencies, imports existing usage history, and starts the user service. Open [http://127.0.0.1:8787](http://127.0.0.1:8787) after installation.

## Dashboard

The dashboard has three views.

**Overview** — totals, trends, model breakdown, activity heatmap, tool and command statistics.

**Live** — last-hour counters and an event stream that refreshes every five seconds.

![Live view](docs/live.png)

**Sessions** — per-session requests, tool calls, tokens, and cost.

![Sessions view](docs/sessions.png)

## HTTP API

| Endpoint | Description |
| --- | --- |
| `GET /api/stats` | Aggregated metrics. Accepts `?days=N` to limit the window. |
| `GET /api/live` | Last-hour counters and recent events. |
| `GET /api/sessions` | Per-session summaries. Accepts `?limit=N`. |
| `GET /health` | Liveness check. |

## Manual use

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m monitor.backfill   # import existing history
.venv/bin/python -m monitor.server     # start the dashboard
```

## Service management

```bash
systemctl --user status ai-usage-monitor
systemctl --user restart ai-usage-monitor
journalctl --user -u ai-usage-monitor -f
```

## Configuration

The service accepts the following environment variables:

| Variable | Purpose | Default |
| --- | --- | --- |
| `AIUM_HOST` | Server bind address | `127.0.0.1` |
| `AIUM_PORT` | Dashboard port | `8787` |
| `AIUM_POLL` | Collection interval in seconds | `60` |
| `AIUM_DB` | SQLite database path | `usage.db` next to the project |
| `AIUM_VAULT` | Obsidian vault path | Local configured vault |
| `AIUM_VAULT_OFF` | Set to `1` to disable vault summaries | Unset |

Set environment overrides in `ai-usage-monitor.service`, then reload and restart the service:

```bash
systemctl --user daemon-reload
systemctl --user restart ai-usage-monitor
```

## How it works

A background poller reads each tool's session logs at a fixed interval. New records are parsed into a common event shape (tokens, cost, model, session, tool calls, and file edits) and written to SQLite. Scans are incremental — file byte offsets and database timestamps are tracked so existing records are never re-read — and inserts are deduplicated by a stable per-record key, so repeated polling never double-counts. The dashboard is a single FastAPI process that serves both the JSON API and the static page.

## Privacy

All collection and reporting runs locally. The generated SQLite database is excluded from version control because it contains personal usage history.

## Acknowledgements

The dashboard design is adapted from [agentObservatory](https://github.com/Snehit70/agentObservatory).
