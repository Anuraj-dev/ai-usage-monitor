"""FastAPI app: background poll thread + JSON API + dashboard. Entry point."""
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

from . import config, db, collectors, vault

STATIC = Path(__file__).parent / "static"

_last_poll = {"ts": 0, "added": 0, "tools": 0, "error": None}


def poll_once():
    events, tools = collectors.collect_all()
    added = db.upsert_events(events)
    added_tools = db.upsert_tools(tools)
    vault.write_daily_note()
    _last_poll.update(ts=int(time.time()), added=added, tools=added_tools, error=None)
    return added + added_tools


def _poll_loop():
    while True:
        try:
            n = poll_once()
            if n:
                print(f"[poll] +{n} new events")
        except Exception as e:
            _last_poll.update(error=str(e))
            print(f"[poll] error: {e}")
        time.sleep(config.POLL_SECONDS)


@asynccontextmanager
async def lifespan(app):
    db.init()
    threading.Thread(target=_poll_loop, daemon=True).start()
    yield


app = FastAPI(title="AI Usage Monitor", lifespan=lifespan)


@app.get("/api/stats")
def api_stats(days: int | None = Query(None)):
    data = db.stats(days=days)
    data["poll"] = _last_poll
    return JSONResponse(data)


@app.get("/api/live")
def api_live():
    return JSONResponse(db.live())


@app.get("/api/sessions")
def api_sessions(limit: int = Query(80)):
    return JSONResponse(db.sessions_list(limit=limit))


@app.post("/api/refresh")
def api_refresh():
    return {"added": poll_once()}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


def main():
    import uvicorn
    db.init()
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
