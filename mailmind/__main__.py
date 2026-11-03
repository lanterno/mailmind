"""`python -m mailmind <command>`, run from the repo root.

  serve [port]           serve the app on http://127.0.0.1:<port> (default 8721) through the new
                         front door
  db check-upgrade       try the next start's migrations on a copy of the database, and report
                         only row counts and integrity checks
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
    args = parser.parse_args(argv)
    if args.command == "db":
        from mailmind.platform import settings
        from mailmind.platform.db import check as db_check
        sys.exit(db_check.check_upgrade(args.db or settings.current().db))
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


if __name__ == "__main__":
    main()
