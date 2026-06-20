"""SQLite store, incremental scan-state, and the full metric engine."""
import sqlite3
import statistics
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    ts          INTEGER NOT NULL,
    source      TEXT NOT NULL,
    session_id  TEXT,
    model       TEXT,
    input       INTEGER DEFAULT 0,
    output      INTEGER DEFAULT 0,
    reasoning   INTEGER DEFAULT 0,
    cache_write INTEGER DEFAULT 0,
    cache_read  INTEGER DEFAULT 0,
    cost        REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);

CREATE TABLE IF NOT EXISTS tool_calls (
    id           TEXT PRIMARY KEY,
    ts           INTEGER NOT NULL,
    source       TEXT NOT NULL,
    session_id   TEXT,
    tool         TEXT NOT NULL,
    ok           INTEGER DEFAULT 1,
    operation    TEXT,
    bash_cmd     TEXT,
    file_path    TEXT,
    file_ext     TEXT,
    lines_added  INTEGER DEFAULT 0,
    lines_removed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tool_ts ON tool_calls(ts);
CREATE INDEX IF NOT EXISTS idx_tool_tool ON tool_calls(tool);
CREATE INDEX IF NOT EXISTS idx_tool_ext ON tool_calls(file_ext);

CREATE TABLE IF NOT EXISTS scan_state (path TEXT PRIMARY KEY, offset INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


@contextmanager
def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with connect() as c:
        c.executescript(_SCHEMA)


# ---- scan-state / meta ----
def get_offset(path):
    with connect() as c:
        r = c.execute("SELECT offset FROM scan_state WHERE path=?", (str(path),)).fetchone()
        return r[0] if r else 0


def set_offset(path, offset):
    with connect() as c:
        c.execute("INSERT INTO scan_state(path, offset) VALUES(?,?) "
                  "ON CONFLICT(path) DO UPDATE SET offset=excluded.offset",
                  (str(path), int(offset)))


def get_meta(k, default=None):
    with connect() as c:
        r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else default


def set_meta(k, v):
    with connect() as c:
        c.execute("INSERT INTO meta(k, v) VALUES(?,?) "
                  "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


# ---- writes ----
def upsert_events(events):
    if not events:
        return 0
    rows = [(e["id"], int(e["ts"]), e["source"], e.get("session_id"), e.get("model", ""),
             int(e.get("input", 0)), int(e.get("output", 0)), int(e.get("reasoning", 0)),
             int(e.get("cache_write", 0)), int(e.get("cache_read", 0)), float(e.get("cost", 0.0)))
            for e in events]
    with connect() as c:
        before = c.total_changes
        c.executemany(
            "INSERT OR IGNORE INTO events "
            "(id, ts, source, session_id, model, input, output, reasoning, cache_write, cache_read, cost) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        return c.total_changes - before


def upsert_tools(tools):
    if not tools:
        return 0
    rows = [(t["id"], int(t["ts"]), t["source"], t.get("session_id"), t["tool"],
             int(t.get("ok", 1)), t.get("operation"), t.get("bash_cmd"),
             t.get("file_path"), t.get("file_ext"),
             int(t.get("lines_added", 0)), int(t.get("lines_removed", 0)))
            for t in tools]
    with connect() as c:
        before = c.total_changes
        c.executemany(
            "INSERT OR IGNORE INTO tool_calls "
            "(id, ts, source, session_id, tool, ok, operation, bash_cmd, file_path, file_ext, lines_added, lines_removed) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        return c.total_changes - before


# Back-compat alias used by older callers
def upsert(events):
    return upsert_events(events)


# --------------------------------------------------------------------------- #
# Metric engine
# --------------------------------------------------------------------------- #
_TOK = "(input+output+reasoning+cache_write+cache_read)"


def _local_midnight(now=None):
    return int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def stats(days=None):
    """Full Observatory metric set. `days` filters the main window (None=all time)."""
    now = int(time.time())
    day = 86400
    win = f" WHERE ts>={now - days*day}" if days else ""
    midnight = _local_midnight()

    with connect() as c:
        def one(sql, *a):
            r = c.execute(sql, a).fetchone()
            return (r[0] if r and r[0] is not None else 0)

        # ---- totals (windowed) ----
        trow = c.execute(
            f"SELECT COUNT(*) reqs, COALESCE(SUM(input),0) i, COALESCE(SUM(output),0) o, "
            f"COALESCE(SUM(reasoning),0) r, COALESCE(SUM(cache_read),0) cr, "
            f"COALESCE(SUM(cache_write),0) cw, COALESCE(SUM(cost),0) cost, "
            f"COALESCE(SUM({_TOK}),0) tok FROM events{win}").fetchone()
        total_tok = trow["tok"]
        totals = {
            "requests": trow["reqs"], "input": trow["i"], "output": trow["o"],
            "reasoning": trow["r"], "cache_read": trow["cr"], "cache_write": trow["cw"],
            "cost": round(trow["cost"], 6), "tokens": total_tok,
            "cost_per_mtok": round(trow["cost"] / (total_tok / 1e6), 4) if total_tok else 0,
            "tokens_per_req": round(total_tok / trow["reqs"]) if trow["reqs"] else 0,
            "token_efficiency": round(trow["o"] / total_tok, 4) if total_tok else 0,
            "cache_hit_rate": round(trow["cr"] / (trow["cr"] + trow["i"] + trow["cw"]), 4)
                              if (trow["cr"] + trow["i"] + trow["cw"]) else 0,
        }

        # ---- today vs 7d avg ----
        today_cost = one("SELECT SUM(cost) FROM events WHERE ts>=?", midnight)
        cost_7d = one("SELECT SUM(cost) FROM events WHERE ts>=?", now - 7 * day)
        avg7 = cost_7d / 7 if cost_7d else 0
        cost_30d = one("SELECT SUM(cost) FROM events WHERE ts>=?", now - 30 * day)
        totals["today_cost"] = round(today_cost, 6)
        totals["avg_daily_7d"] = round(avg7, 6)
        totals["today_vs_avg"] = round((today_cost / avg7 - 1) * 100, 1) if avg7 else 0
        totals["monthly_forecast"] = round(cost_30d / 30 * 30, 2)  # = 30d spend, rolling month
        totals["cost_30d"] = round(cost_30d, 6)

        # ---- model diversity ----
        totals["model_count"] = one("SELECT COUNT(DISTINCT model) FROM events WHERE model<>''")

        # ---- daily series (30d), per source ----
        series = {}
        for r in c.execute(
            "SELECT strftime('%Y-%m-%d', ts,'unixepoch','localtime') d, source, "
            f"SUM(cost) cost, SUM({_TOK}) tok FROM events WHERE ts>=? GROUP BY d, source ORDER BY d",
            (now - 30 * day,)):
            e = series.setdefault(r["d"], {"date": r["d"], "cost": {}, "tokens": {}})
            e["cost"][r["source"]] = round(r["cost"] or 0, 6)
            e["tokens"][r["source"]] = r["tok"] or 0
        series = [series[k] for k in sorted(series)]

        # ---- by source / model ----
        by_source = [dict(source=r["source"], cost=round(r["cost"] or 0, 6),
                          tokens=r["tok"] or 0, calls=r["calls"])
                     for r in c.execute(
                         f"SELECT source, SUM(cost) cost, SUM({_TOK}) tok, COUNT(*) calls "
                         f"FROM events{win} GROUP BY source ORDER BY cost DESC")]
        by_model = [dict(model=r["model"] or "(unknown)", source=r["source"], calls=r["calls"],
                         tokens=r["tok"] or 0, cost=round(r["cost"] or 0, 6))
                    for r in c.execute(
                        f"SELECT model, source, COUNT(*) calls, SUM({_TOK}) tok, SUM(cost) cost "
                        f"FROM events{win} GROUP BY model, source ORDER BY cost DESC LIMIT 60")]
        totals["top_model_share"] = round(by_model[0]["cost"] / totals["cost"] * 100, 1) \
            if by_model and totals["cost"] else 0

        # ---- peak days ----
        pc = c.execute("SELECT strftime('%Y-%m-%d', ts,'unixepoch','localtime') d, SUM(cost) v "
                       "FROM events GROUP BY d ORDER BY v DESC LIMIT 1").fetchone()
        pt = c.execute(f"SELECT strftime('%Y-%m-%d', ts,'unixepoch','localtime') d, SUM({_TOK}) v "
                       "FROM events GROUP BY d ORDER BY v DESC LIMIT 1").fetchone()
        peak = {"cost_day": pc["d"] if pc else None, "cost_val": round(pc["v"], 4) if pc else 0,
                "token_day": pt["d"] if pt else None, "token_val": pt["v"] if pt else 0}

        # ---- velocity / streak ----
        days_rows = [r["d"] for r in c.execute(
            "SELECT DISTINCT strftime('%Y-%m-%d', ts,'unixepoch','localtime') d FROM events ORDER BY d")]
        active_days = len(days_rows)
        velocity = {
            "active_days": active_days,
            "requests_per_day": round(totals["requests"] / active_days, 1) if active_days else 0,
            "requests_per_hour": round(totals["requests"] / active_days / 12, 1) if active_days else 0,
        }
        streak = _streak(days_rows)

        # ---- sessions ----
        srow = c.execute(
            "SELECT COUNT(DISTINCT session_id) n, COUNT(*) reqs, SUM(cost) cost "
            "FROM events WHERE session_id IS NOT NULL").fetchone()
        n_sessions = srow["n"] or 0
        sessions = {
            "count": n_sessions,
            "avg_cost": round((srow["cost"] or 0) / n_sessions, 4) if n_sessions else 0,
            "per_day": round(n_sessions / active_days, 1) if active_days else 0,
        }
        turns_per = [r["t"] for r in c.execute(
            "SELECT COUNT(*) t FROM events WHERE session_id IS NOT NULL GROUP BY session_id")]
        if turns_per:
            sessions.update(avg_turns=round(statistics.mean(turns_per), 1),
                            median_turns=int(statistics.median(turns_per)),
                            max_turns=max(turns_per))
        else:
            sessions.update(avg_turns=0, median_turns=0, max_turns=0)

        # ---- heatmap weekday(0=Sun) x hour ----
        heat = [[0] * 24 for _ in range(7)]
        for r in c.execute(
            "SELECT CAST(strftime('%w', ts,'unixepoch','localtime') AS INT) w, "
            "CAST(strftime('%H', ts,'unixepoch','localtime') AS INT) h, COUNT(*) n "
            "FROM events GROUP BY w, h"):
            heat[r["w"]][r["h"]] = r["n"]

        # ---- recent ----
        recent = [dict(ts=r["ts"], source=r["source"], model=r["model"],
                       tokens=(r["input"] + r["output"] + r["reasoning"] + r["cache_read"] + r["cache_write"]),
                       cost=round(r["cost"], 6))
                  for r in c.execute("SELECT * FROM events ORDER BY ts DESC LIMIT 20")]

        # ---- tool stats ----
        twin = f" WHERE ts>={now - days*day}" if days else ""
        tcalls = c.execute(f"SELECT COUNT(*) n, SUM(ok) ok FROM tool_calls{twin}").fetchone()
        n_tool = tcalls["n"] or 0
        tool_summary = {"total": n_tool, "success": tcalls["ok"] or 0,
                        "failures": n_tool - (tcalls["ok"] or 0),
                        "success_rate": round((tcalls["ok"] or 0) / n_tool * 100, 1) if n_tool else 0}
        tool_breakdown = [dict(tool=r["tool"], calls=r["n"], ok=r["ok"] or 0,
                               rate=round((r["ok"] or 0) / r["n"] * 100, 1) if r["n"] else 0)
                          for r in c.execute(
                              f"SELECT tool, COUNT(*) n, SUM(ok) ok FROM tool_calls{twin} "
                              "GROUP BY tool ORDER BY n DESC LIMIT 20")]
        bash_breakdown = [dict(cmd=r["cmd"], calls=r["n"], ok=r["ok"] or 0)
                          for r in c.execute(
                              "SELECT bash_cmd cmd, COUNT(*) n, SUM(ok) ok FROM tool_calls "
                              "WHERE bash_cmd IS NOT NULL GROUP BY bash_cmd ORDER BY n DESC LIMIT 15")]

        # ---- file types / lines of code ----
        frow = c.execute("SELECT COALESCE(SUM(lines_added),0) a, COALESCE(SUM(lines_removed),0) r, "
                         "COUNT(*) edits FROM tool_calls WHERE file_path IS NOT NULL").fetchone()
        file_totals = {"added": frow["a"], "removed": frow["r"],
                       "net": frow["a"] - frow["r"], "edits": frow["edits"]}
        file_types = [dict(ext=r["ext"] or "(none)", added=r["a"] or 0, removed=r["rm"] or 0,
                           edits=r["n"])
                      for r in c.execute(
                          "SELECT file_ext ext, SUM(lines_added) a, SUM(lines_removed) rm, COUNT(*) n "
                          "FROM tool_calls WHERE file_path IS NOT NULL "
                          "GROUP BY file_ext ORDER BY (COALESCE(SUM(lines_added),0)+COALESCE(SUM(lines_removed),0)) DESC LIMIT 15")]

    return {
        "totals": totals, "series": series, "by_source": by_source, "by_model": by_model,
        "peak": peak, "velocity": velocity, "streak": streak, "sessions": sessions,
        "heatmap": heat, "recent": recent, "tool_summary": tool_summary,
        "tool_breakdown": tool_breakdown, "bash_breakdown": bash_breakdown,
        "file_totals": file_totals, "file_types": file_types,
        "days": days, "generated": now,
    }


def _streak(days_sorted):
    """Current + longest consecutive-day streak from sorted 'YYYY-MM-DD' list."""
    if not days_sorted:
        return {"current": 0, "longest": 0}
    ds = [datetime.strptime(d, "%Y-%m-%d").date() for d in days_sorted]
    longest = run = 1
    for i in range(1, len(ds)):
        run = run + 1 if (ds[i] - ds[i - 1]).days == 1 else 1
        longest = max(longest, run)
    today = datetime.now().date()
    cur = 0
    if ds[-1] in (today, today - timedelta(days=1)):
        cur = 1
        for i in range(len(ds) - 1, 0, -1):
            if (ds[i] - ds[i - 1]).days == 1:
                cur += 1
            else:
                break
    return {"current": cur, "longest": longest}


def live():
    now = int(time.time())
    hour = now - 3600
    with connect() as c:
        h = c.execute(f"SELECT COUNT(*) n, COALESCE(SUM(cost),0) cost, COALESCE(SUM({_TOK}),0) tok "
                      "FROM events WHERE ts>=?", (hour,)).fetchone()
        recent = [dict(ts=r["ts"], source=r["source"], model=r["model"], session_id=r["session_id"],
                       input=r["input"], output=r["output"], reasoning=r["reasoning"],
                       cache_read=r["cache_read"], cost=round(r["cost"], 6))
                  for r in c.execute("SELECT * FROM events ORDER BY ts DESC LIMIT 60")]
    return {"hour": {"requests": h["n"], "cost": round(h["cost"], 6), "tokens": h["tok"]},
            "recent": recent, "generated": now}


def sessions_list(limit=80):
    with connect() as c:
        rows = c.execute(
            f"SELECT session_id, source, MIN(ts) first, MAX(ts) last, COUNT(*) reqs, "
            f"SUM(cost) cost, SUM({_TOK}) tok FROM events WHERE session_id IS NOT NULL "
            "GROUP BY session_id ORDER BY last DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            top = c.execute("SELECT model, COUNT(*) n FROM events WHERE session_id=? "
                            "GROUP BY model ORDER BY n DESC LIMIT 1", (r["session_id"],)).fetchone()
            tools = c.execute("SELECT COUNT(*) n FROM tool_calls WHERE session_id=?",
                              (r["session_id"],)).fetchone()
            out.append(dict(session_id=r["session_id"], source=r["source"], first=r["first"],
                            last=r["last"], requests=r["reqs"], cost=round(r["cost"], 6),
                            tokens=r["tok"], model=top["model"] if top else "",
                            tool_calls=tools["n"] if tools else 0))
    return {"sessions": out}
