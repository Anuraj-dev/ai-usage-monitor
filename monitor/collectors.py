"""Parse local AI-CLI logs into usage events + tool calls.

collect_all() -> (events, tools). Scanning is incremental and de-duplicated.
"""
import json
import os
import sqlite3
from datetime import datetime

from . import config, db
from .pricing import cost_for

_EDIT_TOOLS = {"Edit": "edit", "Write": "write", "MultiEdit": "edit",
               "NotebookEdit": "edit", "Read": "read"}


def _iso_to_epoch(s):
    if not s:
        return 0
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def _ext(path):
    if not path:
        return None
    b = os.path.basename(path)
    return b.rsplit(".", 1)[-1].lower() if "." in b else None


def _scan_lines(path):
    """Yield newly appended json objects; advance stored byte offset."""
    try:
        start = db.get_offset(path)
        size = path.stat().st_size
        if size < start:
            start = 0
        if size == start:
            return
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            f.seek(start)
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except Exception:
                        pass
            db.set_offset(path, f.tell())
    except FileNotFoundError:
        return


def _patch_lines(structured_patch):
    add = rem = 0
    if isinstance(structured_patch, list):
        for hunk in structured_patch:
            for ln in (hunk.get("lines") or []):
                if ln.startswith("+"):
                    add += 1
                elif ln.startswith("-"):
                    rem += 1
    return add, rem


# --------------------------------------------------------------------------- #
# Claude Code
# --------------------------------------------------------------------------- #
def collect_claude(events, tools):
    root = config.CLAUDE_PROJECTS
    if not root.exists():
        return
    for path in root.rglob("*.jsonl"):
        objs = list(_scan_lines(path))
        if not objs:
            continue
        # index tool results by tool_use_id (from user messages)
        results = {}
        for o in objs:
            tur = o.get("toolUseResult")
            msg = o.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        rid = b.get("tool_use_id")
                        if rid:
                            results[rid] = {"is_error": bool(b.get("is_error")),
                                            "patch": (tur or {}).get("structuredPatch")
                                            if isinstance(tur, dict) else None}
        for o in objs:
            sid = o.get("sessionId")
            msg = o.get("message") or {}
            ts = _iso_to_epoch(o.get("timestamp"))
            # usage event
            if o.get("type") == "assistant":
                u = msg.get("usage") or {}
                uid = o.get("uuid") or msg.get("id")
                if u and uid:
                    ni = u.get("input_tokens", 0) or 0
                    out = u.get("output_tokens", 0) or 0
                    cw = u.get("cache_creation_input_tokens", 0) or 0
                    cr = u.get("cache_read_input_tokens", 0) or 0
                    model = msg.get("model", "")
                    events.append({
                        "id": f"cc:{uid}", "ts": ts, "source": "claude_code",
                        "session_id": sid, "model": model, "input": ni, "output": out,
                        "reasoning": 0, "cache_write": cw, "cache_read": cr,
                        "cost": cost_for(model, ni, out, cw, cr)})
                # tool_use blocks
                content = msg.get("content")
                if isinstance(content, list):
                    for b in content:
                        if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                            continue
                        name = b.get("name", "tool")
                        inp = b.get("input") or {}
                        res = results.get(b.get("id"), {})
                        fp = inp.get("file_path") or inp.get("notebook_path")
                        add, rem = _patch_lines(res.get("patch"))
                        bash = None
                        if name == "Bash":
                            cmd = (inp.get("command") or "").strip()
                            bash = cmd.split()[0] if cmd else None
                        tools.append({
                            "id": f"cc:{b.get('id')}", "ts": ts, "source": "claude_code",
                            "session_id": sid, "tool": name,
                            "ok": 0 if res.get("is_error") else 1,
                            "operation": _EDIT_TOOLS.get(name, "tool"),
                            "bash_cmd": bash, "file_path": fp, "file_ext": _ext(fp),
                            "lines_added": add, "lines_removed": rem})


# --------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------- #
def collect_codex(events, tools):
    root = config.CODEX_SESSIONS
    if not root.exists():
        return
    for path in root.rglob("rollout-*.jsonl"):
        sid = path.stem
        model = db.get_meta(f"codex_model:{path.name}", "gpt-5")
        idx = db.get_offset(path)
        for o in _scan_lines(path):
            idx += 1
            payload = o.get("payload") if isinstance(o.get("payload"), dict) else {}
            ptype = payload.get("type") or o.get("type")
            m = (payload.get("model") or o.get("model")
                 or (payload.get("turn_context") or {}).get("model"))
            if m:
                model = m
                db.set_meta(f"codex_model:{path.name}", model)
            ts = _iso_to_epoch(o.get("timestamp")) or int(path.stat().st_mtime)
            if ptype == "token_count":
                info = payload.get("info") or o.get("info") or {}
                last = info.get("last_token_usage") or {}
                if not last:
                    continue
                ti = last.get("input_tokens", 0) or 0
                cached = last.get("cached_input_tokens", 0) or 0
                ni = max(ti - cached, 0)
                out = last.get("output_tokens", 0) or 0
                reasoning = last.get("reasoning_output_tokens", 0) or 0
                if ni == 0 and cached == 0 and out == 0 and reasoning == 0:
                    continue
                events.append({
                    "id": f"cx:{path.name}:{idx}", "ts": ts, "source": "codex",
                    "session_id": sid, "model": model, "input": ni, "output": out,
                    "reasoning": reasoning, "cache_write": 0, "cache_read": cached,
                    "cost": cost_for(model, ni, out + reasoning, 0, cached)})
            elif ptype == "function_call":
                name = payload.get("name") or o.get("name") or "tool"
                bash = None
                if name in ("shell", "local_shell", "bash"):
                    try:
                        args = json.loads(payload.get("arguments") or "{}")
                        cmd = args.get("command")
                        if isinstance(cmd, list):
                            cmd = " ".join(cmd)
                        bash = (cmd or "").strip().split()[0] if cmd else None
                    except Exception:
                        pass
                tools.append({
                    "id": f"cx:{path.name}:{idx}", "ts": ts, "source": "codex",
                    "session_id": sid, "tool": "shell" if bash else name, "ok": 1,
                    "operation": "tool", "bash_cmd": bash, "file_path": None,
                    "file_ext": None, "lines_added": 0, "lines_removed": 0})


# --------------------------------------------------------------------------- #
# OpenCode
# --------------------------------------------------------------------------- #
def collect_opencode(events, tools):
    path = config.OPENCODE_DB
    if not path.exists():
        return
    last_ts = int(db.get_meta("opencode_last_ts", "0"))
    newest = last_ts
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, session_id, time_created, data FROM message WHERE time_created > ? "
            "ORDER BY time_created", (last_ts,)).fetchall()
        conn.close()
    except Exception:
        return
    for r in rows:
        newest = max(newest, r["time_created"] or 0)
        try:
            o = json.loads(r["data"])
        except Exception:
            continue
        if o.get("role") != "assistant":
            continue
        tok = o.get("tokens") or {}
        if not tok:
            continue
        cache = tok.get("cache") or {}
        ni = tok.get("input", 0) or 0
        out = tok.get("output", 0) or 0
        reasoning = tok.get("reasoning", 0) or 0
        cw = cache.get("write", 0) or 0
        cr = cache.get("read", 0) or 0
        model = o.get("modelID") or ((o.get("model") or {}).get("modelID")) or ""
        provider = o.get("providerID") or ((o.get("model") or {}).get("providerID")) or ""
        reported = o.get("cost")
        cost = reported if reported else cost_for(model, ni, out + reasoning, cw, cr)
        events.append({
            "id": f"oc:{r['id']}", "ts": int((r["time_created"] or 0) / 1000),
            "source": "opencode", "session_id": r["session_id"],
            "model": model + (f" ({provider})" if provider else ""),
            "input": ni, "output": out, "reasoning": reasoning,
            "cache_write": cw, "cache_read": cr, "cost": cost})
    db.set_meta("opencode_last_ts", newest)


def collect_all():
    events, tools = [], []
    for fn in (collect_claude, collect_codex, collect_opencode):
        try:
            fn(events, tools)
        except Exception as e:
            print(f"[collector] {fn.__name__} failed: {e}")
    return events, tools
