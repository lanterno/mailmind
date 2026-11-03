"""`python -m mailmind <command>`, run from the repo root.

  serve [port]   serve the app on http://127.0.0.1:<port> (default 8721) through the new front door
"""
import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m mailmind")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the app on 127.0.0.1 (FastAPI on uvicorn)")
    serve.add_argument("port", nargs="?", type=int, help="default 8721")
    args = parser.parse_args(argv)
    if args.command == "serve":
        try:
            from mailmind.bootstrap import front
        except ModuleNotFoundError as e:
            sys.exit(f"serve needs the runtime packages, and {e.name} isn't installed. Run it as "
                     f"`uv run python -m mailmind serve`, or use the old front door, `python3 server.py`.")
        front.serve(args.port)


if __name__ == "__main__":
    main()
