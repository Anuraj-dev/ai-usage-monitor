# AI Usage & Token Monitor — Design

A local, always-on monitor that tracks token usage and **dollar cost** across every
AI CLI you use (Claude Code, Codex, Gemini, OpenCode), stores it in SQLite, and serves
a dark, dense analytics dashboard. Runs as a systemd **user** service.

## Decisions (locked)

| Question | Choice |
|---|---|
| Capture method | **CLI logs only** — parse local session logs, zero config, no proxy |
| Stack | **FastAPI + Chart.js single page** (one process: poller + API + dashboard) |
| Visual | **Dark dense analytics** (Grafana-style KPI cards + charts + model table) |
| Obsidian | **Yes** — daily `## AI Usage` summary written to the vault |

## How capture works (verified against real files on this machine)

| Tool | Source | Token fields | Dedupe key |
|---|---|---|---|
| **Claude Code** | `~/.claude/projects/**/*.jsonl` | `message.usage.{input_tokens, cache_creation_input_tokens, cache_read_input_tokens, output_tokens}`, model `message.model` | `uuid` |
| **Codex** | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | `token_count` events → `last_token_usage.{input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens}` | `file:lineno` |
| **OpenCode** | `~/.local/share/opencode/opencode.db` (SQLite) | assistant `message.data.tokens.{input, output, reasoning, cache.read, cache.write}`, model `model.modelID` | message `id` |
| **Gemini** | best-effort (no per-call token log exposed by Gemini CLI) | — | — |

Each parsed call becomes one **event** row. The poller re-reads the logs every 60s and
inserts with `INSERT OR IGNORE` on the dedupe key, so re-scanning never double-counts.

### Cost model
Pricing lives in `monitor/pricing.py` as USD per 1M tokens `(input, output, cache_write, cache_read)`,
matched by model-name substring (longest match wins). Cost per event:

```
cost = (new_input·in + output·out + cache_write·cw + cache_read·cr) / 1e6
```
Cached/cheap input is billed at the cache-read rate; unknown/free models cost $0.
Edit `pricing.py` to correct any rate — totals recompute on next poll for new events.

## Schema

```sql
events(
  id TEXT PRIMARY KEY,   -- dedupe key
  ts INTEGER,            -- epoch seconds
  source TEXT,           -- claude_code | codex | opencode
  model TEXT,
  input INT, output INT, cache_write INT, cache_read INT,
  cost REAL              -- USD
)
```

All dashboard numbers are SQL aggregates over this one table.

## Architecture

```
 ~/.claude  ~/.codex  opencode.db
        \      |      /
      collectors.py  (parse → event dicts)
            |
        db.py  (SQLite, INSERT OR IGNORE)
            |
   poller thread (every 60s) ──► vault.py (daily Obsidian note)
            |
       server.py  (FastAPI)
        /        \
  /api/stats   /  (dark dashboard, Chart.js)
```

One process (`python -m monitor.server`) runs the poll thread **and** the web server on
`http://127.0.0.1:8787`. Managed by `~/.config/systemd/user/ai-usage-monitor.service`.

## Dashboard layout (dark dense analytics)

- Top: 4 KPI cards — **Today $**, **Today tokens**, **30-day $**, **All-time $**.
- Row 2: **Daily spend** area chart (30d) + **Cost by tool** doughnut.
- Row 3: **Tokens by day** stacked bar (per tool).
- Bottom: **Model cost table** — model, source, calls, tokens, $ (sortable, hottest first).
- Palette: base `#0b0e14` / `#11151f`, accent teal `#40E5D1`, violet `#5B14C5`, alert `#E84045`.
- Auto-refreshes every 30s.

## File map

```
ai-usage-monitor/
├── design.md
├── README.md
├── install.sh                 # venv + pip + enable systemd service
├── requirements.txt
├── ai-usage-monitor.service   # systemd user unit (template)
└── monitor/
    ├── config.py              # paths, port, vault location
    ├── pricing.py             # USD rate table + cost_for()
    ├── db.py                  # SQLite store + aggregate queries
    ├── collectors.py          # claude / codex / opencode parsers
    ├── vault.py               # daily Obsidian note writer
    ├── server.py              # FastAPI app + poll thread (entrypoint)
    └── static/index.html      # dashboard
```

## Build steps
1. `pricing.py` — rates + cost. 2. `db.py` — store/queries. 3. `collectors.py` — 3 parsers.
4. `vault.py` — daily note. 5. `server.py` — API + poll thread + static. 6. `index.html` — dashboard.
7. `install.sh` + systemd unit. 8. Backfill + launch + verify.
