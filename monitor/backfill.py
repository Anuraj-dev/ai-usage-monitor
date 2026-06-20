"""One-shot: scan all existing logs into the DB (run before first start)."""
from . import db, collectors, vault


def main():
    db.init()
    events, tools = collectors.collect_all()
    added = db.upsert_events(events)
    added_tools = db.upsert_tools(tools)
    vault.write_daily_note()
    print(f"Backfill complete: {added} events, {added_tools} tool calls stored.")


if __name__ == "__main__":
    main()
