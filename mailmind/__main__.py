"""`uv run python -m mailmind <command>`, run from the repo root. WHO is an account's address or `all`.

  serve [port]                 serve the app on http://127.0.0.1:<port> (default 8721)
  check                        whether each account's credential resolves: ok or fail, never a secret
  accounts [materialize [WHO]] the connected accounts; `materialize` keeps each secret held elsewhere
                               (1Password, a variable, the Bridge password) in the Keychain
  sync [WHO] [days]            pull mail from the servers into the database
  assess [limit] [workers]     summarize the mail with no summary yet, at full price
  assess --batch               send it as a batch at half price, within today's limit, and bring in
                               the answers of one that has ended
  status | inventory [WHO]     each INBOX and Trash count | every folder's counts
  plan [WHO]                   classify the live Inbox and show what a sweep would trash
  sweep [WHO] [--dry]          move junk to Trash, verified and audited; --dry only says what
  sweep-stored [WHO] [--dry]   the same, for all the mail stored as junk
  expire [WHO] [--dry]         move Inbox mail past its expiry rule to Trash
  empty-trash [WHO]            empty Trash (not on Proton Bridge)
  rules [list]                 the learned keep and block lists, and expiry
  rules keep | block TOKEN...  always keep, or always trash, a sender or domain
  rules remove TOKEN...        forget a keep or block (expiry stays)
  rules expire TOKEN HOURS     expire a sender's or domain's Inbox mail after HOURS (0 clears)
  views [list]                 the saved views, built-ins marked *
  views add NAME QUERY | remove ID
  export [--format F]          write the synced mail out (eml, mbox, db), or `plan` to see what that
                               holds; touches no mail server
  db check-upgrade             try the next start's migrations on a copy of the database, and report
                               only row counts and integrity checks
  db compact                   give back the room freed in the database file, with MailMind stopped
  db rethread [WHO]            the repair: regroup the mail into threads from what each message names,
                               whole; no sync needs it, and what it changes moves as a merge does
  ai usage [--days N]          what the Claude API cost each local day of the last N (default 7), by
                               feature, from the usage ledger; it migrates nothing and calls nothing
"""
import argparse
import os
import shlex
import signal
import sys


MAILBOX_COMMANDS = {"status": "INBOX and Trash counts", "inventory": "every folder's counts",
                    "plan": "classify the live Inbox and show what a sweep would trash",
                    "empty-trash": "empty Trash (not on Proton Bridge)"}
DRY_COMMANDS = {"sweep": "move junk to Trash, verified and audited", "sweep-stored": "the same, for all the mail stored as junk",
                "expire": "move Inbox mail past its expiry rule to Trash"}


def parser():
    top = argparse.ArgumentParser(prog="uv run python -m mailmind", description=(__doc__ or "").split("\n", 1)[0])
    commands = top.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the app on 127.0.0.1 (FastAPI on uvicorn)")
    serve.add_argument("port", nargs="?", type=int, help="default 8721")
    commands.add_parser("check", help="whether each account's credential resolves")
    people = commands.add_parser("accounts", help="the connected accounts")
    people.add_argument("action", nargs="?", choices=["list", "materialize"], default="list")
    people.add_argument("who", nargs="?", help="with materialize, just this account")
    sync = commands.add_parser("sync", help="pull mail into the database")
    sync.add_argument("who", nargs="?", default="all", help="an account's address, or all")
    sync.add_argument("days", nargs="?", type=int, help="how far back")
    assess = commands.add_parser("assess", help="triage the mail with no summary yet")
    assess.add_argument("limit", nargs="?", type=int, default=200, help="how many at most (default 200)")
    assess.add_argument("workers", nargs="?", type=int, help="how many at once")
    assess.add_argument("--batch", action="store_true", help="through the Message Batches API, at half price")
    for name, what in MAILBOX_COMMANDS.items():
        commands.add_parser(name, help=what).add_argument("who", nargs="?", default="all", help="an account's address, or all")
    for name, what in DRY_COMMANDS.items():
        one = commands.add_parser(name, help=what)
        one.add_argument("who", nargs="?", default="all", help="an account's address, or all")
        one.add_argument("--dry", action="store_true", help="only say what it would do")
    rules = commands.add_parser("rules", help="the learned rules").add_subparsers(dest="rules_command")
    rules.add_parser("list", help="the keep and block lists, and expiry")
    for verdict in ("keep", "block"):
        rules.add_parser(verdict, help=f"always {verdict if verdict == 'keep' else 'trash'} a sender or domain").add_argument("tokens", nargs="+")
    rules.add_parser("remove", help="forget a keep or block; expiry stays").add_argument("tokens", nargs="+")
    expire = rules.add_parser("expire", help="expire a sender's or domain's Inbox mail after some hours")
    expire.add_argument("token")
    expire.add_argument("hours", type=int, help="0 clears")
    views = commands.add_parser("views", help="the saved views").add_subparsers(dest="views_command")
    views.add_parser("list", help="the saved views, built-ins marked *")
    add = views.add_parser("add", help="save a view")
    add.add_argument("name")
    add.add_argument("query")
    views.add_parser("remove", help="remove a view").add_argument("id")
    export = commands.add_parser("export", help="write the synced mail out; touches no mail server")
    export.add_argument("--format", choices=["eml", "mbox", "db", "plan"], default="plan")
    export.add_argument("--out", default="/tmp/mailmind-export")
    export.add_argument("--account", default=None)
    export.add_argument("--attachments", action="store_true")
    db = commands.add_parser("db", help="the database").add_subparsers(dest="db_command", required=True)
    check = db.add_parser("check-upgrade", help="try the migrations on a copy of the database")
    check.add_argument("--db", help="the database to check (default: the data directory's)")
    shrink = db.add_parser("compact", help="give back the room freed in the file, with MailMind stopped")
    shrink.add_argument("--db", help="the database to compact (default: the data directory's)")
    db.add_parser("rethread", help="regroup the mail into threads, whole").add_argument("who", nargs="?", default="all", help="an account's address, or all")
    ai = commands.add_parser("ai", help="the Claude API").add_subparsers(dest="ai_command", required=True)
    usage = ai.add_parser("usage", help="what AI cost each day, by feature")
    usage.add_argument("--days", type=int, default=7, help="how many days, today included (default 7)")
    return top


def main(argv=None):
    """Run the command `argv` names (sys.argv's by default). Every command runs on the runtime packages, so one that
    finds a package missing says how to run it with them, rather than ending in a traceback; and one that can't open
    the database (still plaintext, a key that doesn't fit, a Keychain that won't answer) says what to do about it."""
    argv = sys.argv[1:] if argv is None else argv
    args = parser().parse_args(argv)
    try:
        return _run(args)
    except ModuleNotFoundError as e:
        if not e.name or e.name.partition(".")[0] == "mailmind":
            raise
        sys.exit(f"MailMind needs its runtime packages, and {e.name} isn't installed. Run it as "
                 f"`uv run python -m mailmind {shlex.join(argv)}`.")
    except RuntimeError as e:
        from mailmind.platform.db import database, key
        if isinstance(e, (database.NotEncrypted, database.KeyDoesNotFit, key.KeyProblem)):
            sys.exit(str(e))            # what to do about it, without a traceback
        raise


def _run(args):
    if args.command == "assess" and args.batch:
        from mailmind.bootstrap import wiring
        sys.exit(wiring.assess_batch())
    if args.command == "ai":
        sys.exit(ai_usage(args.days))
    if args.command == "db" and args.db_command == "rethread":
        from mailmind.bootstrap import cli
        return cli.rethread(args.who)
    if args.command == "db":
        from mailmind.platform import settings
        path = args.db or settings.current().db
        if args.db_command == "compact":
            from mailmind.platform.db import compact
            sys.exit(compact.compact(path))
        from mailmind.platform.db import check as db_check
        sys.exit(db_check.check_upgrade(path))
    if args.command != "serve":
        from mailmind.bootstrap import cli
        if args.command == "check":
            return cli.check()
        if args.command == "accounts":
            return cli.materialize(args.who) if args.action == "materialize" else cli.list_accounts()
        if args.command == "sync":
            return cli.sync(args.who, args.days)
        if args.command == "assess":
            return cli.assess(args.limit, args.workers)
        if args.command in MAILBOX_COMMANDS:
            return cli.mailboxes(args.command, args.who)
        if args.command in DRY_COMMANDS:
            return cli.mailboxes(args.command, args.who, dry=args.dry)
        if args.command == "rules":
            if args.rules_command in ("keep", "block"):
                return cli.rules_add(args.rules_command, args.tokens)
            if args.rules_command == "remove":
                return cli.rules_remove(args.tokens)
            if args.rules_command == "expire":
                return cli.rules_expire(args.token, args.hours)
            return cli.rules_list()
        if args.command == "views":
            if args.views_command == "add":
                return cli.views_add(args.name, args.query)
            if args.views_command == "remove":
                return cli.views_remove(args.id)
            return cli.views_list()
        if args.command == "export":
            return cli.export(args.format, args.out, args.account, args.attachments)
    if args.command == "serve":
        from mailmind.bootstrap import front
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
    from mailmind.platform import settings
    from mailmind.platform.db import driver
    db = settings.current().db
    if not os.path.exists(db):
        print("There is no database yet, so no usage either.")
        return 1
    from mailmind.assistant.domain.pricing import usd
    from mailmind.bootstrap import assistant
    try:
        report = assistant.gateway().usage(max(1, days))
    except driver.OperationalError as e:
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
