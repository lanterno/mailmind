# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

A `justfile` wraps everything below — `just` alone lists the recipes. The raw commands
work identically if `just` isn't installed. `just run`, `just lint` and `just test` need
[uv](https://docs.astral.sh/uv/) (`brew install uv`); `uv run` installs the runtime packages and
the dev tools on first use.

```bash
just verify                       # lint + compile + all tests + frontend check — run before committing
just test-full                    # every test, plus the browser tests in Chromium (Playwright)
just dev                          # read-only: writes nothing to a mail server, no background sync
just run [port]                   # the real thing (default http://127.0.0.1:8721)
just run-old [port]               # the same through the old front door, which needs nothing installed
```

```bash
uv run python -m mailmind serve [port]   # run the app (default http://127.0.0.1:8721)
python3 server.py [port]          # the old front door: the same app, with nothing installed
MAILMIND_PROFILE=read-only python3 server.py   # the same read-only profile, either door

python3 config.py check           # verify credentials resolve, per account (prints ok/fail, no secrets)
python3 ingest.py all 90          # pull last 90 days; `ingest.py <email> <days>` for one account
python3 summarize.py 2000 3       # summarize up to 2000 messages, 3 workers
python3 -m mailmind db check-upgrade   # owner only: try the next upgrade on a copy; counts and checks
python3 -m mailmind ai usage --days 7  # what the Claude API cost each day, by feature; reads only
```

Tests — plain scripts, each exits non-zero on failure. `just test` (`uv run pytest`) runs them
all through `tests/test_legacy_suites.py`, each in its own interpreter with a temp
`MAILMIND_HOME`, because on import each makes a temp directory the data directory
(`settings.configure`) and some replace module functions with fakes:

```bash
python3 tests.py                  # classifier behaviour (27 assertions)
python3 tests_sanitize.py         # HTML sanitizer: 24 attack classes, expects 0 leaks
python3 test_css_sanitizer.py     # CSS sanitizer unit tests (40)
python3 test_expire.py            # expiry rules (15)
python3 test_thread_file.py       # whole-conversation archive/trash (10)
python3 test_upcoming.py          # Today & next invites, RRULE expansion + the Waiting view's recipient (23)
python3 test_contacts_known.py    # the "new to you" lookup: who counts as known, exact matching, CSRF (17)
python3 test_send_after.py        # /api/send refuses passed and far-off send times (5)
python3 test_send_guard.py        # the composer's send-time checks, run in node (19)
python3 test_draft_resume.py      # reopening a saved draft: split, files, Bcc, threading, refusals, saves (81)
python3 test_draft_resume_ui.py   # the composer's reopen helpers, run in node (8)
python3 test_hardening.py         # security and data-loss fixes, one test or more per fix (37)
```

`just test -k hardening` picks suites by file name; within a suite there is no single-test
flag. `tests.py` and `tests_sanitize.py` are linear scripts —
comment out cases or copy one into a REPL. `test_css_sanitizer.py`, `test_expire.py` and
`test_thread_file.py`, `test_upcoming.py`, `test_contacts_known.py`,
`test_send_after.py`, `test_send_guard.py`, `test_draft_resume.py`, `test_draft_resume_ui.py` and
`test_hardening.py` collect callables from `globals()` whose names start with `test_`, so you can gate them by
renaming. `test_send_guard.py` slices the block between `/* send-guard:pure:start */` and
`/* send-guard:pure:end */` out of `dashboard.html` and runs it in node, so that block must
stay free of DOM and `state` references. `test_draft_resume_ui.py` does the same with the
`/* draft-resume:pure:start */` … `/* draft-resume:pure:end */` block.

### The pytest suite in `tests/`

The rest of `tests/` pins today's behaviour ahead of the refactor. Each test sees only fictional
data (`example.test` addresses) in a temp directory. `tests/conftest.py` sets that up before any
MailMind module loads:

- It removes any Claude key and any `MAILMIND_HOME` from the environment and sets `TZ=UTC`.
- It installs the tripwire in `tests/guard.py`. The tripwire refuses the real data files (at the
  repo root, and in the directory `MAILMIND_HOME` named), any non-loopback host, and the
  `claude`, `op` and `security` programs. A test that trips it fails.

The fixtures, from the bottom up:

- `data_dir` makes a fresh temp directory the app's data directory (`settings.use`) and creates
  the schema there.
- `world` is a fictional 25-message corpus (`tests/fixtures/corpus/`, three accounts) served by
  `tests/fakes/imap_server.py`. The app's own ingest synced it into the temp database.
  `tests/fakes/smtp_server.py` receives what the accounts send. Both fakes can refuse, drop or
  renumber on cue (`server.fail`, `server.drop`, `smtp.fail`, ...).
- `app` runs the app in-process through each front door in turn: `door=old` is `server.py`'s own
  `ThreadingHTTPServer`, `door=new` the app `mailmind.bootstrap.front.build` makes, on uvicorn.
  So every app test runs twice, both runs share one golden file, and `app.door` says which run
  this is. Its CSRF token is fixed, and the clock is frozen at `conftest.NOW` (2026-11-02 12:00
  UTC); `app.clock.shift(timedelta(...))` moves it. `tests/test_front_door.py` runs each door as a
  program instead.
- `ai` gives the fake Claude API (`tests/fakes/anthropic_api.py`) a key it accepts. It answers
  each call from the call's own schema unless the test sets `ai.queue(...)` or `ai.responder`.
  Without `ai` there is no key, and AI calls get a 401.
- `golden` records exchanges in `tests/golden/<suite>/<test>.json`, after it normalizes ports,
  paths and random ids. `uv run pytest --update-golden` rewrites the files; review the diff like
  code. `tests/golden/schema_v<N>.sql` is the schema snapshot: rename it in the commit that adds
  migration N.

A known bug is a `xfail(strict=True)` test that states the right behaviour, so fixing the bug
fails the test until the marker goes. `uv run pytest --runxfail -k <name>` shows where each fails.
`test_hwf_*.py` hold the hard-won fixes, one file per layer.

`tests/browser/` drives `dashboard.html` in Chromium (Playwright) on the `app` fixture:

- The page may reach only the app's origin.
- Its clock starts at the server's frozen time.
- Console errors fail the test, and so does any content security policy violation, which a
  report-only policy logs at the info level.
- axe's findings are compared with `tests/golden/browser/axe_baseline.json`, which may only go down.

These tests are marked `browser` and skipped unless pytest runs with `--browser`.
`just test-full` installs Chromium if needed and runs everything. `just verify` leaves them out
to stay fast.

There is no build step. `pyproject.toml` declares the runtime packages and, in its dev group,
the dev tools (pytest, ruff, import-linter, Playwright and the like), all locked with hashes in
`uv.lock`, and `.python-version` pins 3.14 for uv and pyenv alike. `just lint` is `ruff check` with pyflakes' rules only (`select = ["F"]`),
then `lint-imports` (import-linter) on the `mailmind` package's layers. New pytest-native tests go in `tests/`;
pytest's `--import-mode=importlib` keeps that directory from shadowing the root's `tests.py`.

`just check-frontend` parses every inline `<script>` with `node --check`, checks CSS
brace balance, and rejects raw control bytes in `dashboard.html` (see Conventions).

## Constraints

- **`python3 server.py` runs with nothing installed.** The app was standard library only until
  the refactor's Phase 2, and that shaped every design decision below (hand-rolled sanitizer,
  hand-rolled IMAP handling, no web framework, no frontend build). Since then it may use
  runtime packages (FastAPI, uvicorn and the Anthropic SDK so far), each declared in `pyproject.toml` and locked
  with hashes in `uv.lock`. Each dependency change is its own commit, and the `uv.lock` change
  goes in the next. But `python3 server.py`, the old front door, is the kill switch while the
  new one carries daily use, so nothing it imports may import a third-party package;
  `tests/test_front_door.py` checks that.
- **The frontend is one file.** `dashboard.html` (~340KB) holds all HTML, CSS and JS
  inline. No bundler, no modules, no npm. Edit it directly. This holds until the refactor's
  frontend track moves it to React 19 and Vite.
- **Nothing personal is committed.** `mailmind.db`, `mailmind.config.json`,
  `mailmind_rules.json`, `mailmind_views.json` and `*.jsonl` audit trails are gitignored.
  They live in the data directory: the repo root, unless `MAILMIND_HOME` names another.
  The repo is public — treat any real address, employer or message count as a leak.

## Architecture

Pipeline, roughly in data-flow order:

```
mailmind.config.json → config.py/accounts.py → ingest.py → mailclass.py → store.py (SQLite)
                                                                ↓
                                              summarize.py (Claude API / CLI) → server.py + dashboard.html
```

Around that spine: `mailops.py` (IMAP writes), `compose.py`/`smtpsend.py`/`outbox.py`
(send), `search.py` (FTS + operator grammar), `threads.py`, `mailparse.py`,
`sanitize_html.py`, `ics.py`, `realtime.py` (IMAP IDLE), and the AI modules
(`summarize*.py`, `draft_reply.py`, `rewrite.py`, `ask.py`).

The app is one view, a real folder-and-account client whose Inbox opens on today's brief
(`renderBrief`, built from the `/api/digest` items and the cached `/api/briefing` narrative; its
side column, `briefSideHtml`, from `/api/upcoming` and the `__waiting__` view).
A separate Briefing view existed until it was folded into this one; `body.mode-client`
survives as a CSS specificity hook, not a mode.

### The `mailmind` package

The refactor's new code grows in `mailmind/`, beside the legacy modules at the repo root. The
legacy modules may import from it freely; the reverse goes only through `mailmind.bootstrap`.

- `mailmind.platform.settings` is the one source of the data paths and the port. Code calls
  `settings.current()` where it opens a file or checks the port, never once at import, so a test
  can swap the whole data directory with `settings.use(...)`. `MAILMIND_HOME` names the data
  directory; unset, it is the repo root. It must exist, and nothing creates it.
  `python3 server.py [port]` and `python -m mailmind serve [port]` put the port in the settings.
- `mailmind.bootstrap.legacy.wire(server)` subscribes the server, on `mailmind.platform.bus`, to
  what the legacy workers publish: `outbox.Sent`, `readmutq.Applied` and `realtime.InboxChanged`.
  It also hands each account event (below) to `realtime.reconcile`. Wiring again replaces the last wiring, and `unwire()` ends it (the tests' state reset does).
  `server.__main__` passes `sys.modules[__name__]`: importing `server` there would load a second
  copy with state of its own.
  `legacy.start(server)` is the startup both doors share: the profile check, the schema, `wire`,
  then the workers. Full, those are the outbox's and the read-mutation queue's drains, the
  scheduler's jobs and IDLE; read-only, the snooze job alone.
- `python -m mailmind serve` (`mailmind/__main__.py`) is the new front door.
  `mailmind.bootstrap.front.serve` starts the app as above and serves it with FastAPI on uvicorn,
  bound to 127.0.0.1. Every route still runs in `server.H`: `mailmind.http.bridge.HandlerBridge`,
  the FastAPI router's fallback, hands it each request without a socket, on a daemon thread of
  its own, and streams back what it writes, the event stream and the export included.
  `mailmind.http.middleware.Perimeter` runs the perimeter (see Security model) before any of it,
  and before a body is read. `mailmind.http.serving.Server` ends open streams as shutdown begins
  (`HandlerBridge.close`), so Ctrl-C stops at once.
- `mailmind.platform.profile` says how the process runs, `full` or `read-only` (see Writes,
  undo, and offline).
- `mailmind.platform.workers.gates` is the IMAP gate every live-IMAP job passes (see
  Concurrency).
- `mailmind.platform.workers.scheduler.Scheduler` runs the background jobs. A job is a round (a
  function doing one round of the work) on a lane of its own, a thread named `job:<name>`, so
  its rounds never overlap. A round may return the seconds until its next; one that raises is
  logged and counted, and the job goes on. `server.schedule(jobs)` is the job table: `snooze` in
  every profile, then `sync`, `assess`, `backfill`, `draft-ahead`, `reconcile` and `expire` in a
  full one. `assess` triages the mail that has no summary yet, outside the sync's single-flight,
  and a sync that brought new mail kicks it (`server._kick`). With AI off, or triage held back
  by the cost governor (see AI calls), it tries nothing: `summarize.summarize_pending` returns 0
  at once, and a round that is only yielding to a click comes back in `ASSESS_YIELD` seconds.
  `/api/debug/jobs` shows each job's timing and last outcome, an error as its class name only.
  A test calls the round itself (`server.backfill_round()`) or `jobs.run(name)`. It is
  standard library only.
- `mailmind.platform.bus` is an in-process publish and subscribe. An event is a frozen dataclass
  of ids and counts, and its class is its topic. Handlers run in the publisher's thread once the
  change has committed; one that raises is logged by class name and the rest still hear. Nothing
  on it is durable: work that must survive a crash is a queue row written with the change.
- `mailmind.platform.sse` is the hub behind `/api/events`, the live event stream. `sse.publish`
  numbers each event `<run>-<n>` and keeps the last 512. A tab resumes after the `Last-Event-ID`
  its EventSource sends; one the hub can't resume (an earlier run, or further back than it keeps)
  gets one `resync`. `sse.TOPICS` lists every topic and `publish` refuses any other;
  `tests/test_sse.py` checks that `connectEvents` in `dashboard.html` listens for each, and for
  `resync`, and nothing else. Data carries ids and counts, never a subject. Golden files write the
  run as `<run>`.
- `mailmind.platform.workers.durable_queue.DurableQueue` is the queue the outbox and the offline
  move queue share (see Writes, undo, and offline). It is standard library only.
- `mailmind.platform.workers.singleflight.SingleFlight` makes each costly AI read once. Only one
  run goes at a time for each key. A caller that finds one going waits, then reads what it
  stored. The leader reads again before it makes, unless `recheck=False` asks for a fresh one.
  The brief and delta, the review and thread summaries each hold one (`_FLIGHTS`), and so does
  drafting ahead (`server.DRAFT_FLIGHTS`, whose `busy(key)` answers "generating"). It is standard
  library only.
- `mailmind.platform.db` opens the database, runs its migrations, backs it up before one and
  checks an upgrade on a copy (see Schema migrations). It is standard library only, since
  `python3 server.py` uses it.
- `mailmind.kernel` is empty so far. It stays standard library only and imports nothing else
  from `mailmind`.
- `mailmind.assistant` is the first bounded context: what MailMind asks Claude (see AI calls).
  `application` holds the ports, the `Gateway` and the `CostGovernor`, `domain` what it knows
  about the models and the features, and
  `adapters` the `LanguageModel`s: `AnthropicModel` (the SDK), `UrllibModel` (standard library)
  and `DisabledModel`. `mailmind.bootstrap.assistant` binds one and resolves the key.
- `mailmind.accounts` is the second: the mail accounts and how MailMind reaches each. So far it
  holds `adapters.config_file`, which `config.py` reads and writes `mailmind.config.json`
  through. `ConfigFile.read` parses the file again only when its inode, size or modification
  time changed, and hands each caller a copy of its own; `write` replaces it atomically, readable
  by its owner only. `adapters.secrets` resolves a credential in each style the file allows
  (plain, `op`, `env`, the Bridge password), caching an `op` read only when it succeeds.
  `config.py` and `mailmind.bootstrap.assistant` (the API key, the daily limit) read through
  both. `domain.provider.ProviderProfile` says what an account's server does on its own and its
  quirks: Gmail files sent mail itself, archives by dropping `\Inbox` and threads by X-GM-THRID;
  Proton Bridge (or any loopback STARTTLS server) is fragile, recycled sooner in the pool and never
  bulk-expunged, and marks trashed mail read; `with_capabilities` adds what CAPABILITY says.
  `provider.resolve(acct)` replaces checking `kind == "gmail"`. `domain.events` holds
  `AccountAdded`, `AccountChanged` and `AccountRemoved`, which `config.add_account`,
  `update_account` and `remove_account` publish on the bus once the file holds the change (a
  refused change publishes nothing). `domain.tls_pin` says how a loopback certificate is pinned
  (see Security model). It is standard library only.
- `mailmind.mailbox` is the third: the mail on each server and its folders. So far it holds
  `domain.roles`, which says which folder plays which role (`inbox`, `sent`, `drafts`, `archive`,
  `all`, `junk`, `trash`, else `custom`): INBOX by name, then the SPECIAL-USE attribute, then the
  whole last segment of the name, never part of one ("Bulk orders" is nobody's junk). Where
  several could play a role, an attribute beats a name, a shallower path a deeper one, then LIST's
  order. `adapters.imap_list` reads an imaplib LIST answer into `Folder`s, literals included.
  `mailops.find_role(M, *roles)` finds a folder on a live connection; the rail gives each role to
  the one folder `find` picks, and `all` (Gmail's All Mail) is where archiving goes only when
  there is no `archive`. It is standard library only.
- Two checks hold the shape. import-linter's contracts in `pyproject.toml` run in `just lint`:
  bootstrap, then http, then the contexts, then platform, then kernel; within each context,
  adapters over application over domain; no adapter imported outside bootstrap; and the
  `anthropic` package reached only through its adapter. `tests/legacy_ratchet.txt` lists
  every import of a legacy module from inside the package; `tests/test_ratchet.py` fails when the
  code and the list differ. Only `mailmind.bootstrap` may appear in it, and it should only shrink.

### Routing — there is no router

`server.py` is one `BaseHTTPRequestHandler` subclass. `do_GET` and `do_POST` are long
`if/elif` chains comparing `u.path` against **literal strings**. There is no route table,
no decorator registry, and no `getattr`-based dispatch anywhere in the file.

Consequence: to find a route, grep the literal path. To confirm a handler is dead, grep
its name — nothing can reach it dynamically. The new front door serves this same handler
through `HandlerBridge`, so that holds there too.

### Schema migrations

The database's code is `mailmind.platform.db`; `store.conn()` and `store.init()` call into it.

- `database.connect` opens every connection: WAL, `synchronous=FULL` with `fullfsync` (a commit
  survives a power cut, because `delivered` and `applied` are exactly-once gates),
  `busy_timeout` 60 s, and `foreign_keys=ON`, which the cascades depend on.
- No transaction stays open across a network call. `database.connect` tracks each connection for
  the thread that opened it, and `txguard.refuse_network(what)` raises `TxAcrossNetworkError`
  while one has a transaction open, since a write held while a server answers keeps every other
  writer waiting. An IMAP connection and each command (`accounts.connect`), an SMTP session and
  all it sends (`smtpsend.smtp_connect`), a Claude API request and a one-click unsubscribe check
  it, in tests and in use. Commit before talking to a server; a new network path calls
  `refuse_network` first, or wraps the connection's method with `txguard.guard_calls`.
- `migrations.MIGRATIONS` is a list of `(target_user_version, callable)` run against
  `PRAGMA user_version`. Migrations 1 to 20 are frozen in `migrations/m0001_m0020.py`, beside
  `BASE_SCHEMA`, `LEGACY_COLS` (pre-versioning column adds) and copies of the code they call
  (`_frozen_threads.py`, `_frozen_mailparse.py`). Never edit those files. A schema change is a
  new migration in a new file after them, as `m0021_ai_usage.py` is; one that needs the app's
  code freezes a copy of it.
- Migrations are **append-only**. Never renumber or delete one — the version number is
  the on-disk contract, and retired features leave a historical no-op behind (`_m14_notes` →
  `_m18_drop_notes`).
- `migrate.init` runs the base schema, then each pending migration, each in a transaction of its
  own (`BEGIN IMMEDIATE`) that sets its version too, so one that fails leaves the database as it
  was before it. A migration gets the connection through `_Within`: `execute`, and
  `executescript` run one statement at a time inside the transaction. It never commits.
- Each must still be idempotent; `init()` runs on every process start.
- Before it migrates a database that holds anything, `init` backs it up with SQLite's backup API
  to `backups/mailmind-v{N}-{UTC time}.db` beside it, readable by its owner only, keeping the
  newest 3 (`backup.py` says how to roll back).
- `python3 -m mailmind db check-upgrade` (`just db-check-upgrade`) migrates a read-only copy and
  prints only per-table row counts and integrity verdicts. The owner runs it on the real
  database before an upgrade; no agent does.
- `tests/test_db_upgrade.py` upgrades a database seeded with data from every version. A
  migration that derives a column on purpose adds it to `DERIVED` there.

### Concurrency

Every job that talks to an account's IMAP server runs through
`gates.run(account, fn, priority=..., wait=...)` (`mailmind.platform.workers.gates`): the
requests that open and file mail, undo, reconcile, expiry, the body backfill, the outbox, the
offline queue and the thread summarizer. `MAILMIND_WORKERS` picks the gate:

- Unset (or `global`), one process-wide lock is every account's gate, so all live-IMAP work is
  serialized and a black-holed account holds up the rest until its connection times out.
- `account`, each account has a gate of its own. Waiting jobs go by priority, then in turn. An
  `EVERY_ACCOUNT` job waits until no gate is held and holds them all, and a job that would come
  after it waits behind it. The whole suite passes under either; the owner soaks `account`
  before it becomes the default and the flag goes.

A job runs on its caller's thread either way, so context variables (`assistant.allowing_over()`)
and txguard's per-thread record stay with it. The connection pool's per-account lock is taken
*inside* the gate. The 300 s sync and IDLE's syncs don't take it yet. `server.sync_guarded` is
their single-flight: a sync asked for while one runs is recorded, and the running one goes round
again for it as it ends.

- A job is a function, not a block: it returns what its caller needs instead of setting the
  caller's variables.
- Each names its account (a message's through `mailops.account_of`), or `gates.EVERY_ACCOUNT`
  for work across accounts, and a `gates.Priority`: INTERACTIVE, AFTER_USER, SYNC or
  MAINTENANCE.
- Background work passes `wait=False` and skips its turn on `gates.Busy`, so it never stalls
  the user. `deadline=` (seconds) raises `Busy` for a job not let in by then, and it never runs.
  A job that waits on a gate from inside a job raises `RuntimeError` instead of hanging; one that
  takes another account's gate with `wait=False` may.
- Tests ask `gates.held(account)`, and hold the gate with `held_elsewhere()` from
  `tests/gate.py`, which runs a job like any caller. `tests/test_chaos.py` holds what one
  account's trouble may do to the rest; `FakeIMAPServer.stall` black-holes a command.

`pool.py` (persistent per-account IMAP connections) and `warm.py` (multi-folder sync)
are both **default-off** behind `MAILMIND_POOL` / `MAILMIND_WARM_FOLDERS`. Code must
work with them unset.

IDLE's workers follow the configuration. `realtime.reconcile`, which the account events call,
retires the worker of an account removed, disabled or now reached another way (kind, host, port,
TLS, credential), and starts one for each account without one, up to `MAX_IDLE_ACCOUNTS`. An alias
or signature change leaves the worker be. A worker also checks its account after each IDLE cycle,
so an edit of the file, which publishes nothing, still reaches it. `readmutq` gives up a queued
move whose account is gone, as the outbox does a send.

`realtime.stop()` and a retirement only shut a worker's socket down; the worker closes its own
connection. Closing a socket another thread may be about to wait on frees its descriptor number
for the next socket opened.

`mailmind.platform.profile` is the only reader of `MAILMIND_PROFILE` (`full`, the default, or
`read-only`) and of the older `MAILMIND_NOSYNC`, which still selects read-only: any value but
empty, `0`, `false`, `no` or `off` turns it on, so a typo fails toward read-only, and read-only
wins when the two disagree. A front door refuses to start on a profile name it doesn't know.
`server.nosync()` asks the profile.

### Writes, undo, and offline

- Mutations go through `mailops.py`. A connection failure raises a member of
  `mailops._UNREACHABLE`, which the route catches and turns into a durable queue row via
  `store.read_mut_insert`.
- `readmutq.py` drains that queue when IMAP returns, using the `applied` flag as an
  exactly-once gate. It and `outbox.py` each run a `DurableQueue`, `QUEUE`, over their table:
  one claim wins a row, a failure backs off from 20 s doubling to 15 minutes, and the fifth
  goes to `error`. The gate column (`applied`, `delivered`) is set the instant the one
  irreversible step returns, so the boot-time reset of running rows never repeats it. A row's
  state changes go through `QUEUE`, not the store. The move is itself an IMAP op, so it runs
  as a job on the account's IMAP gate; the outbox's SMTP send holds none.
- The read-only profile writes nothing to a mail server or anywhere else outside (decision D4).
  `accounts.connect` hands back an `accounts.ReadOnlyIMAP`, which opens every mailbox with
  EXAMINE and raises `profile.ReadOnlyError` on any command not known to only read, before it is
  sent. `smtpsend.smtp_connect` and `_unsub_oneclick_post` refuse too. `ReadOnlyError` is a
  member of `mailops._UNREACHABLE`, so a move queues as it does offline, and both queues' drains
  wait for a full run. What nothing queues (a flag, a draft, a sweep) answers 409 through
  `H.do_POST`'s catch. A new write path either queues or calls `profile.refuse_writes` first.
- A server NO or BAD means the message didn't move. `_imap_mutate` returns `(False, None)`, and
  trash, archive, junk and move answer `ok: false` and keep the local row. imaplib returns a NO
  rather than raising it, so check `typ` on COPY, STORE and APPEND. Never send a bulk `EXPUNGE`:
  `mailops.expunge_uid` removes one UID, and `_move_uid` flags the original only after COPY succeeds.
- The queue's idempotency key carries the request's `mutation_id`, or a random one when the
  client sends none, so only a resubmit of the same request dedupes.
- `ingest.ingest_message` (undo's one-message re-ingest) never moves the sync watermark or
  creates a folder's `sync_state` row; only a sync that saw every UID may.
- Undo is an in-memory LIFO `_undo_stack` — **lost on restart**, by design. Bulk actions
  push one grouped entry with children. A group id must be unique for the process's life:
  the frontend's `undoGroup()` is a UUID, since the stack outlives a page load.
- Whole-conversation archive/trash (`/api/thread/archive|trash` → `do_thread_file`) resolves
  the members server-side — the anchor's `thread_id` within the anchor's folder — and files
  each through the per-message op (`do_write` / `do_trash`) under one undo group.
- Send-time checks: `composeSend` runs `guardCheck` synchronously, before `composeClose(true)`,
  so nothing is awaited before the composer closes and a double submit still can't happen.
  `/api/contacts/known` (the "new" marks) is a read-only POST: pure SQL, no IMAP gate.
  `do_send` refuses a `send_after` that has passed instead of sending now.
- Reopening a draft (`/api/draft/resume` → `draft_resume`) reads the draft's own bytes, never
  the original. At each save `do_draft` records beside the `drafts` row what the composer held
  (migration 20: `own_text`, `tail_text`, `tail_html`, `bcc`, `raw_sha`). That record is
  trusted only for the very copy it describes, the same UID or the same sha256. The composer
  sends `resume` (threading and the quote's text) on every save and send. `_compose_spec`
  then builds `own + tail`, with HTML only where the row vouches for that exact tail. The
  original is looked up by `(account, Message-ID)` for `\Answered` only. `_draft_live`, the
  "is this copy still there" probe, is a stateless 10-second connection outside the IMAP gate.
  Under the gate `draft_resume` re-reads the `drafts` row and answers 409 if it is gone or moved,
  so `do_draft` and the outbox's draft cleanup also change that row only under the gate. Edit
  answers 423 while a queued or sending outbox row holds the draft. A copy in another account
  or folder is removed only after the new APPEND succeeds, and only if MailMind saved it or
  the server flags it `\Draft`.
- A composer replaced after an `await` (Undo Send, a failed send's Edit → `makeRoom`) must
  close and open in the same task. In the gap `_syncBack`'s MutationObserver calls
  `history.back()`, the new composer's `pushState` lands before that traversal does, and the
  Back trap ends one entry short, so the next close steps the tab off the app.
- **`pk` is not stable.** Reconcile can renumber rows, so anything durable keys on
  `(account, message_id)` — see `store.get_message_by_msgid`. A stored AI answer is checked
  against `mailmind.assistant.domain.fingerprint`, which names messages by Message-ID: a thread
  summary, a reply drafted ahead, the brief, the delta and the review all use it. Migration 22
  moved stored thread summaries onto it.

### Security model

Every request passes `mailmind.platform.perimeter` before any route, through either door:
`server.H`'s `do_GET` and `do_POST` call it, and so does the new door's `Perimeter` middleware.
It is standard library only.

- `refuse_host` — the Host must be this server's (127.0.0.1 or localhost on its port), and an
  Origin, when sent, its own. Blocks DNS rebinding. Every request, `/` included.
- `refuse_cross_site` — under `/api/` only `Sec-Fetch-Site: same-origin` passes, so another
  site's no-cors `<img>`, a typed URL and a client that says nothing are all refused. `/` also
  opens from a typed URL or another site's top-level link.
- `refuse_body` — a Content-Length over the route's cap (`server.post_body_cap`:
  `MAX_SEND_POST` for send and draft, `MAX_POST` otherwise), judged before the body is read.
- `refuse_mutation` — every POST: the two checks above, then the `X-CSRF-Token` header
  compared with `secrets.compare_digest`, then an Origin, which must be this server's.

The new door puts every method but GET and HEAD through the POST checks, and refuses before it
reads a body; the old one reads the body first.

Every answer, a refusal included, carries `perimeter.answer_headers`: nosniff,
`Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, COOP and CORP `same-origin`, a
Permissions-Policy, and under `/api/` `Cache-Control: no-store`. `H.end_headers` adds each one
an answer didn't set itself, and so does the middleware. The page also carries
`Content-Security-Policy-Report-Only` from `perimeter.page_policy`: its own inline scripts by
sha256 and nothing else, styles and fonts from Google Fonts too, connections to this server
only, framed by no one. The browser tests fail on any violation.

A failed GET or POST answers a fixed message and logs the detail. Every TLS connection, IMAP
and SMTP alike, STARTTLS or implicit, takes `accounts.tls_context(host)`: verified everywhere but
loopback, where Proton Bridge's certificate is self-signed. There `accounts.check_pin` stands in,
after the handshake and before LOGIN or AUTH: the first certificate an account meets at a loopback
`host:port` is pinned (`config.tls_pin`, the account's `tls_pins`, `sha256:` of its DER bytes), and
any other after it raises `TLSPinMismatch` (a `ConnectionError`, so a move queues) with nothing
sent. Health calls it `TLS_PIN`; `/api/accounts/trust-cert` forgets the account's pins and syncs it.
An unsaved draft pins nothing. A new TLS connection calls `check_pin` too.

A credential goes only to its own account's server. `/api/accounts/test` logs a saved account in
at its saved endpoint, and tries a draft (an account not yet saved) only with a password typed
into it, at the host typed with it. A draft naming a stored secret (`op`, `env`, the Bridge
password) is refused, since the request names the host.

Folder names reach IMAP only through `accounts.mailbox_arg`. It wraps a name in the form LIST
gave it and refuses one that isn't a valid quoted-string body (CR, LF, NUL, a bare quote, a
trailing backslash) with `accounts.BadMailbox`, an `IMAP4.error`. Never build `f'"{folder}"'`.

`CSRF` is a per-process token. `dashboard.html` ships a `__CSRF_TOKEN__` placeholder in a
`<meta>` tag that `server.py` substitutes at serve time; the frontend's `api()` helper
attaches it to every request, GETs included.

Mail bodies render in a sandboxed iframe with a deny-all CSP. `sanitize_html.py` is
defense in depth — **the iframe sandbox is the actual boundary**, so a sanitizer miss
alone cannot execute script. Preserve that ordering when touching either.

### AI calls

Everything routes through `summarize.call_claude_json(prompt, schema)` (or
`call_claude` for plain text), which call `mailmind.bootstrap.assistant.gateway()`. The
Claude API is the only engine (decision D8). The `Gateway` builds each request from
`mailmind.assistant.domain`: effort where the model takes one (`models.takes_effort`), the
refusal fallback where it takes that (`models.takes_fallback`), the schema in structured
output's form (`schema.structured`). It sends it through a `LanguageModel` and gets back a
`Reply`: the text, the model that served it, its `Usage` and its stop reason. A call with no
reply raises `ModelError` (a `RuntimeError` with `status` and `permanent`).

The new front door talks through the Anthropic SDK (`front.build` calls
`assistant.use("sdk")`); `python3 server.py`, the scripts and the root suites through
`UrllibModel`, since the old door runs with nothing installed. Only
`mailmind/assistant/adapters/anthropic_sdk.py` imports `anthropic`, and only the new door loads
it. The two share the wire format (`adapters.wire`) and must behave the same:
`tests/test_ai_contract.py` runs every case through each. The key is
`assistant.config_key()` (top-level `"anthropic_api_key"` in `mailmind.config.json`, any
credential style) and only then `ANTHROPIC_API_KEY`; a config key that won't resolve
means no key, never a fall-through to the environment's. With no key the model is
`DisabledModel`: `summarize.engine()` is `"none"`, and a call raises `AIOff` (re-exported as
`summarize.AIOff`) before anything is sent.
`MAILMIND_NO_SCHEMA=1` falls back to free-form text. AI actions never
fire on their own — drafts wait, bulk commands require a preview and confirm.

`schema.structured` drops the keywords structured output rejects (`minimum`, `maximum`,
`maxLength`) and closes every object, so callers must keep clamping what they read. Both
transports send `fallbacks: "default"` with its beta on the models that accept it, read a
refusal as an empty reply, and retry 408/409/429/5xx twice. The SDK sends the key as
`Authorization: Bearer`, urllib as `x-api-key`; the fake API accepts both.

One contract: a failed call raises `RuntimeError` (the caller retries
later, nothing is cached); a successful but unusable reply returns `{}` (the caller
stores its fallback). No key degrades to no summaries, with nothing sent, rather than
failing; keep that property. A call that was never sent raises a `NotSent`: `AIOff`, or a
`HeldBack` from the cost governor (`OverLimit`, `Deferred`). A caller lets `NotSent` through
(`except _sz.NotSent: raise`) ahead of its own `except Exception`, and so does a route's own
catch for `summarize.OverLimit` and `summarize.AIOff`, so `H.do_GET` and `H.do_POST` can answer
them: `OverLimit` as a 402 (below), `AIOff` as a 409 with `ai_off: true`, which the page shows as
"AI is off" rather than an error to retry. `call_claude` and `call_claude_json` are wrapped by `_noted`,
which records each call's outcome for `summarize.status()` (not an `AIOff`: no call was made) — served on `/api/digest` as
`ai`, and the source of the brief's "AI is off" card and the sync menu's AI line. A new
entry point that bypasses them won't show up there.

The `Gateway` counts every call it sends in the usage ledger, migration 21's `ai_usage` (the
`UsageLedger` port, `adapters.sqlite_ledger`). There is one row per local day, feature, model
asked, model that served it and outcome: `ok`, `refused`, `rejected` (a permanent 4xx) or
`failed`. Each row holds the calls, their tokens and `cost_micros`. `domain.pricing` computes the
cost from Anthropic's list prices in integers, rounded up. It prices an unknown model as the
dearest, and a server-side fallback at the dearer of the two models. `Gateway.ask` requires a
`feature`: `triage`, `thread`, `brief`, `delta`, `review`, `draft`, `draft-ahead`, `rewrite`,
`ask` and `plan` so far, and a new AI call names its own. `call_claude` and `call_claude_json`
pass it on, `other` when the caller names none. With AI off nothing is counted. A ledger write
that fails is logged by class name and never fails the call.

What the ledger holds is read back in three places:
- `summarize.status()` carries `spent_today_micros`, which the sync menu's AI line shows.
- `/api/debug/ai?days=N` (30 by default) lists each day by feature.
- `python3 -m mailmind ai usage` prints the same. It reads the ledger only and never migrates.

The `Gateway` asks the cost governor (`application.governor.CostGovernor`,
`assistant.governor()`) before every call it sends. `domain.features.kind` sorts each feature:
`triage` is background, `draft-ahead` speculative, and every other feature interactive.
- Slots. Two interactive calls at a time, and two background or speculative ones beside them.
- Yielding. Background and speculative calls don't start while an interactive one runs, or for
  45 seconds after one ends (`YIELD_SECONDS`).
- Presence. A speculative call also needs someone to have used the app in the last 45 minutes.
  `server._ui_touch` calls `saw_someone()` for every `/api/` request a person makes, except the
  polling routes in `_UI_QUIET`. A start counts as someone here.
- The daily limit is `assistant.limit_micros()`: the top-level `"ai_daily_limit_usd"`, a
  positive number, or $15 (decision D7). It counts everything the ledger holds for today, and a
  ledger that can't be read counts as over. At the limit, background and speculative calls raise
  `Deferred("limit")`. An interactive call raises `OverLimit`, which `H.do_GET` and `H.do_POST`
  answer as a 402 with `over_limit: true`. The page's `api()` asks (`askOverLimit`), then sends
  the request again with `X-MailMind-AI-Over-Limit: allow` (`server.AI_OVER_HEADER`), which
  runs it inside `assistant.allowing_over()`.
- The breaker. A 401 or 403 stops every background and speculative call, and three permanent
  4xx in a row for one feature and model stop that pair. It stays open until the model in use
  changes, which a new key, base URL or transport does. Interactive calls still go.

Background work asks `gateway().held_back(feature)` before a batch, and a call that was held
back anyway raises `Deferred(reason)`. Nothing is counted for it, and `_noted` doesn't record it
as a failure. Nothing in the governor is durable: a restart closes the breaker, and the limit
holds across one because the ledger does. `/api/debug/ai` shows its `snapshot()`, and the
tests start each one afresh with `forget()`.

The brief, the delta and the review are read and written apart:
- A GET of `/api/briefing`, `/api/briefing/since` or `/api/review` reads only what is stored. When
  there is none it answers `missing: true` and calls nothing (the builders' `generate=False`).
- A POST to the same path writes one, reading first, and its `/refresh` POST writes it again.
- The page GETs, and POSTs only on `missing`. What it writes on its own (the brief as the Inbox
  opens, the delta for the new-mail pill) passes `askOver: false` to `api()`. At the limit it
  then gets the 402 as an error (`overLimit`), and the brief offers "Write it" instead of asking
  unprompted.

A new AI route spends on a POST. The one GET that still starts a call is drafting ahead's kick,
which needs the CSRF token.

### Classification

`mailclass.py` assigns one of seven buckets (`SPAM`, `BLOCKED`, `MKT`, `NOTIF`,
`RECEIPT`, `PROTECTED`, `PERSONAL`); `JUNK_BUCKETS` decides what ingest drops.
`rules.py` layers a learned per-sender/domain keep/block list on top, persisted to
`mailmind_rules.json` — that file is personal data and stays gitignored. The same file
holds expiry rules (`expire_hours`: sender/domain → hours), enforced by
`manage.expire_account` from the CLI and by the server's `expire` job hourly (not read-only).
`rules.remove` deliberately leaves expiry alone; clear it with `set_expiry(token, 0)`.

## Conventions

- **Don't create `*.bak-*` snapshot files.** The repo used to carry 38 of them; they are
  now gitignored. Use a git branch.
- **No inline event handlers in `dashboard.html`.** The page's content security policy lets its
  `<script>` blocks run by hash and nothing else, so an `onclick=` or the like is a violation:
  attach a listener instead (delegated, like `#doPanel`'s). Keep each script a plain `<script>`
  with no attributes, or `perimeter.page_policy` won't find it to hash.
- **Keep `dashboard.html` free of raw control bytes.** A single literal NUL byte once
  made `grep` and `ripgrep` treat the file as binary and silently return nothing past it,
  hiding half the frontend from every search tool. Use escape sequences in string
  literals.
- **`ROADMAP.md` lists only work that is still open.** When something ships, summarize it
  into the README's Features section and delete it from the roadmap — no "✅ DONE"
  entries. Verify status against the code, not the doc, and cite functions and symbols
  rather than line numbers (they drift).
