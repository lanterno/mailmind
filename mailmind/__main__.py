"""`python -m mailmind <command>`, run from the repo root.

  serve [port]           serve the app on http://127.0.0.1:<port> (default 8721) through the new
                         front door
  db check-upgrade       try the next start's migrations on a copy of the database, and report
                         only row counts and integrity checks
  db compact             give back the room freed in the database file, with MailMind stopped
  ai usage [--days N]    what the Claude API cost each local day of the last N (default 7), by
                         feature, from the usage ledger; it migrates nothing and calls nothing
  assess --batch         send the mail with no summary yet as a batch, at half price, within today's
                         limit, and bring in the answers of one that has ended
"""
import argparse
import os
import signal
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m mailmind")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the app on 127.0.0.1 (FastAPI on uvicorn)")
    serve.add_argument("port", nargs="?", type=int, help="default 8721")
    db = commands.add_parser("db", help="the database").add_subparsers(dest="db_command", required=True)
    check = db.add_parser("check-upgrade", help="try the migrations on a copy of the database")
    check.add_argument("--db", help="the database to check (default: the data directory's)")
    shrink = db.add_parser("compact", help="give back the room freed in the file, with MailMind stopped")
    shrink.add_argument("--db", help="the database to compact (default: the data directory's)")
    ai = commands.add_parser("ai", help="the Claude API").add_subparsers(dest="ai_command", required=True)
    usage = ai.add_parser("usage", help="what AI cost each day, by feature")
    usage.add_argument("--days", type=int, default=7, help="how many days, today included (default 7)")
    assess = commands.add_parser("assess", help="triage the mail with no summary yet")
    assess.add_argument("--batch", action="store_true", required=True,
                        help="through the Message Batches API, at half price")
    args = parser.parse_args(argv)
    if args.command == "assess":
        from mailmind.bootstrap import legacy
        sys.exit(legacy.assess_batch())
    if args.command == "ai":
        sys.exit(ai_usage(args.days))
    if args.command == "db":
        from mailmind.platform import settings
        path = args.db or settings.current().db
        if args.db_command == "compact":
            from mailmind.platform.db import compact
            sys.exit(compact.compact(path))
        from mailmind.platform.db import check as db_check
        sys.exit(db_check.check_upgrade(path))
    if args.command == "serve":
        try:
            from mailmind.bootstrap import front
        except ModuleNotFoundError as e:
            sys.exit(f"serve needs the runtime packages, and {e.name} isn't installed. Run it as "
                     f"`uv run python -m mailmind serve`, or use the old front door, `python3 server.py`.")
        try:
            front.serve(args.port)
        except KeyboardInterrupt:
            # uvicorn stops cleanly on Ctrl-C, then raises it again so the program ends as
            # interrupted. End that way, so a caller such as `just` stops too, without a traceback.
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGINT)


def ai_usage(days):
    """Print what AI cost each of the last `days` local days, newest first, by feature, and the
    total. It reads the usage ledger and nothing else: no migration, no call. 0 when it could."""
    import sqlite3
    from mailmind.platform import settings
    db = settings.current().db
    if not os.path.exists(db):
        print("There is no database yet, so no usage either.")
        return 1
    from mailmind.assistant.domain.pricing import usd
    from mailmind.bootstrap import assistant
    try:
        report = assistant.gateway().usage(max(1, days))
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise
        print("The database predates the usage ledger: start the app once and it will be added.")
        return 1
    span = "today" if len(report) == 1 else f"the last {len(report)} days"
    print(f"AI usage by local day at Anthropic's list prices, {span}:")
    calls = sum(d["calls"] for d in report)
    total = usd(sum(d["cost_micros"] for d in report))
    width = max([len(total)] + [len(usd(d["cost_micros"])) for d in report])
    for d in report:
        if not d["calls"]:
            print(f"  {d['day']}  {'-':>{width}}")
            continue
        parts = sorted(d["features"].items(), key=lambda kv: (-kv[1]["cost_micros"], kv[0]))
        what = " · ".join(f"{name} {usd(f['cost_micros'])} ({f['calls']})" for name, f in parts)
        print(f"  {d['day']}  {usd(d['cost_micros']):>{width}}  {_calls(d['calls'])}  {what}")
    print(f"  {'in all':<10}  {total:>{width}}  {_calls(calls)}")
    return 0


def _calls(n):
    return f"{n:>5} {'call' if n == 1 else 'calls':<5}"


if __name__ == "__main__":
    main()
