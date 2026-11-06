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
just test-dovecot                 # the MailSession contract against Dovecot in Docker
just dev                          # read-only: writes nothing to a mail server, no background sync
just run [port]                   # the real thing (default http://127.0.0.1:8721)
just run-old [port]               # the same through the old front door, which needs nothing installed
just run-next [port]              # build the React app, then run; it's at /next
just setup                        # install the Python packages, node_modules and Chromium
```

```bash
uv run python -m mailmind serve [port]   # run the app (default http://127.0.0.1:8721)
python3 server.py [port]          # the old front door: the same app, with nothing installed
MAILMIND_PROFILE=read-only python3 server.py   # the same read-only profile, either door

python3 -m mailmind check         # verify credentials resolve, per account (prints ok/fail, no secrets)
python3 -m mailmind sync all 90   # pull last 90 days; `sync <email> <days>` for one account
python3 -m mailmind assess 2000 3 # summarize up to 2000 messages, 3 workers
python3 -m mailmind rules         # the learned rules; `rules keep|block|remove|expire ...` change them
python3 -m mailmind sweep --dry   # what a junk sweep would trash; also status, inventory, plan, expire
python3 -m mailmind export        # what an export would hold; `--format eml|mbox|db --out DIR` writes it
python3 -m mailmind db check-upgrade   # owner only: try the next upgrade on a copy; counts and checks
python3 -m mailmind db compact         # owner only, MailMind stopped: give back the room freed in the file
python3 -m mailmind ai usage --days 7  # what the Claude API cost each day, by feature; reads only
python3 -m mailmind assess --batch     # send what has no summary as a batch; brings in one that ended
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
python3 test_upcoming.py          # Today & next invites, RRULE expansion + the Waiting view's recipient (23)
python3 test_contacts_known.py    # the "new to you" lookup: who counts as known, exact matching, CSRF (16)
python3 test_send_after.py        # /api/send refuses passed and far-off send times (5)
python3 test_send_guard.py        # the composer's send-time checks, run in node (19)
python3 test_draft_resume.py      # reopening a saved draft: split, files, Bcc, threading, refusals, saves (81)
python3 test_draft_resume_ui.py   # the composer's reopen helpers, run in node (8)
python3 test_hardening.py         # security and data-loss fixes, one test or more per fix (37)
```

`just test -k hardening` picks suites by file name; within a suite there is no single-test
flag. `tests.py` and `tests_sanitize.py` are linear scripts —
comment out cases or copy one into a REPL. `test_css_sanitizer.py`, `test_expire.py`,
`test_upcoming.py`, `test_contacts_known.py`,
`test_send_after.py`, `test_send_guard.py`, `test_draft_resume.py`, `test_draft_resume_ui.py` and
`test_hardening.py` collect callables from `globals()` whose names start with `test_`, so you can gate them by
renaming. `test_send_guard.py` and `test_draft_resume_ui.py` slice their block (`/* send-guard:pure:start */` … and
`/* draft-resume:pure:start */` …) out of `web/assets/legacy/pure.js` and run it in node. That file is written from
`web/src/compose/sendGuard.ts` and `draftResume.ts`, which stay free of DOM and `state` references and have Vitest
twins beside them; edit those and run `just gen-web`. The send guard's two suites share
`tests/fixtures/send_guard_words.json`.

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
  this is. In the world's config Cy's Gmail asks for IMAPClient, so the new door's run reaches it
  through IMAPClient and the old door's through imaplib. Its CSRF token is fixed, and the clock
  is frozen at `conftest.NOW` (2026-11-02 12:00 UTC); `app.clock.shift(timedelta(...))` moves
  it. `tests/test_front_door.py` runs each door as a program instead.
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

`tests/test_queue_state_machine.py` runs Hypothesis over the offline queue and Undo: random moves, flag
changes, outages, refusals, crashes, syncs, Undo, Retry and Discard. It runs 12 fixed cases in the suite;
`MAILMIND_MACHINE_RUNS=1500` asks for that many with fresh seeds, and `MAILMIND_MACHINE_STEPS` for longer ones.

`tests/browser/` drives the page in Chromium (Playwright) on the `app` fixture, and `test_next_*.py` the React app, built once for the session into a folder of its own:

- The page may reach only the app's origin.
- Its clock starts at the server's frozen time.
- Console errors fail the test, and so does any content security policy violation, which a
  report-only policy logs at the info level.
- axe's findings are compared with `tests/golden/browser/axe_baseline.json`, which may only go down.
- `page.pw.evaluate` has no timeout, so a promise that never settles hangs the run for good. A test waiting on animations leaves out
  the looping ones (`FADES_DONE`).

These tests are marked `browser` and skipped unless pytest runs with `--browser`.
`just test-full` installs Chromium if needed and runs everything. `just verify` leaves them out
to stay fast.

`tests/test_mail_session_dovecot.py` holds the MailSession contract against a real server,
Dovecot 2.3 in Docker (pinned by digest), through each adapter, and checks what each call left
through a connection of its own. Its tests are marked `dovecot` and skipped unless pytest runs
with `--dovecot`; `just test-dovecot` runs them, and needs Docker running. One container serves
the run, and each test logs in as a user of its own, with an empty mailbox.

There is no build step. `pyproject.toml` declares the runtime packages and, in its dev group,
the dev tools (pytest, ruff, import-linter, Playwright and the like), all locked with hashes in
`uv.lock`, and `.python-version` pins 3.14 for uv and pyenv alike. `just lint` is `ruff check` with pyflakes' rules only (`select = ["F"]`),
then `lint-imports` (import-linter) on the `mailmind` package's layers. New pytest-native tests go in `tests/`;
pytest's `--import-mode=importlib` keeps that directory from shadowing the root's `tests.py`.

`just check-web` (part of `just verify`) type-checks `web/src` with `tsc`, runs its Vitest suites and checks that
`web/assets/legacy/pure.js` is what `web/src/compose` gives; it installs `web/node_modules` from the lockfile if they
are missing. `just gen-web` writes `pure.js`. `just gen-api` runs the whole test suite with `MAILMIND_RECORD_API` set and
writes `web/src/api/types.gen.ts` from the JSON answers it saw, shapes only (`tests/gen_api_types.py`), since `server.H`
has no schema to generate from: run it when an answer's shape changes, and review the diff.

`just check-frontend` parses each script in `web/assets/legacy/` with `node --check`, checks CSS
brace balance, and rejects an inline `<script>` or raw control bytes (see Conventions).

## Constraints

- **`python3 server.py` runs with nothing installed.** The app was standard library only until
  the refactor's Phase 2, and that shaped every design decision below (hand-rolled sanitizer,
  hand-rolled IMAP handling, no web framework, no frontend build). Since then it may use
  runtime packages (FastAPI, uvicorn, the Anthropic SDK and IMAPClient so far), each declared in
  `pyproject.toml` and locked with hashes in `uv.lock`. Each dependency change is its own
  commit, and the `uv.lock` change goes in the next. But `python3 server.py`, the old front door,
  is the kill switch while the new one carries daily use, so nothing it imports may import a
  third-party package; `tests/test_front_door.py` checks that.
- **The page runs as it stands.** `web/index.html` is the markup, `web/assets/styles/` the CSS (`fonts.css` the
  self-hosted fonts, Latin, Cyrillic and Greek; Japanese glyphs come from system fonts), and `web/assets/legacy/` the
  scripts: `theme.js`, `pure.js` (written from `web/src/compose`) and `app.js`, ~310KB of classic script. No build is needed:
  `mailmind.platform.webroot` serves this source to either door with gzip and an ETag, so the old door still needs
  nothing installed. `just build-web` (Vite, Node) writes the same files, named by their content, to
  `mailmind/http/dist` (gitignored), served for good while it is as new as every source file, so an edit never leaves
  the app on old files. A new top-level script joins `CLASSIC` in `web/vite.config.js`. `/assets/` serves only `.css`,
  `.js` and `.woff2`. `/api/events` opens with `hello{build}` (`sse.HELLO`, no id), and a tab that loaded another build
  offers a reload (`onHello`). `web/src` holds strict TypeScript: the pure modules and the API client (`api/client.ts`, typed by `api/types.gen.ts`).
  `web/next.html` and `web/src/main.tsx` are the React app at `/next` (React 19 with the React Compiler, TanStack Query, React Aria Components), beside the page and on its
  stylesheet. Unlike the page it exists only as built: without `just build-web`, `/next` answers 404 and says so, and `/` is untouched. Where you are lives in the URL
  (`app/route.ts`: a `ListSource` and what is open), the event stream invalidates queries (`app/realtime.ts`), `features/mailbox` holds the rail, the list, the reader
  and filing, and `app/commands.ts` the one list of commands that the keys and the palette (⌘K) both read. `features/compose` is the composer: `machine.ts` holds its rules with no DOM (`createComposing(env)`, what
  `machine.test.ts` drives), `compose.ts` binds that to the page, and `Composer.tsx` shows the copy the machine publishes. `features/brief` is today's brief, the lists the digest answers (`/next/brief/needs`, `tidy`, `topic/<label>`),
  Commitments, Your week and the new-mail pill, `features/ask` Ask and Do from the search box, `features/status` the sync pill, the account alert, the meeting reminder and the keyboard help, and `features/settings` the connections.
  `features/lanes` is the Feed and the Paper Trail, `features/senders` the Screener and the contact card, `features/docs` the Document Hub and `features/curate` the drawer and a sender's cleanup. `lib/backTrap.ts` makes Back close the top layer (`useLayer`), and `features/mailbox/swipe.ts` is a phone's swipe. It does what the page does now, and the top bar links to `/` until it takes the page's place. A modal makes everything outside it inert, and a click goes straight through that, so what must stay in reach over one carries `data-react-aria-top-layer`, as the floats do. Unlike the page's,
  its search box acts on Enter, and its pill names only mail that landed outside the list on screen, since a list refreshes in place. A value hooks read outside React is a `lib/store.ts` store, whose hook is `useValue`, since the compiler
  doesn't take a method named `use` for a hook. The compiler also keeps what a call with no reactive input gave, so a component reads the time or storage in a child mounted when it's needed, or through a store.
- **Nothing personal is committed.** `mailmind.db`, `mailmind.config.json`,
  `mailmind_rules.json`, `mailmind_views.json` and `*.jsonl` audit trails are gitignored.
  They live in the data directory: the repo root, unless `MAILMIND_HOME` names another.
  The repo is public — treat any real address, employer or message count as a leak.

## Architecture

Pipeline, roughly in data-flow order:

```
mailmind.config.json → config.py/accounts.py → ingest.py → mailclass.py → store.py (SQLite)
                                                                ↓
                                              summarize.py (Claude API) → server.py + web/
```

Around that spine: `mailops.py` (IMAP writes), `compose.py`/`smtpsend.py`/`outbox.py`
(send), `search.py` (FTS + operator grammar), `threads.py`, `mailparse.py`,
`sanitize_html.py`, `ics.py`, `realtime.py` (IMAP IDLE), and the AI modules
(`summarize*.py`, `draft_reply.py`, `rewrite.py`, `ask.py`).

`search.build` reads a date with no zone at this machine's midnight. `search.visible` leaves snoozed mail out of
a search and of what a command acts on, unless the query says `is:snoozed`. `search.BM25` is the one table of
ranking weights, which Ask uses too. All three come from `mailmind.discovery`, below. A message is one search result
however many folders hold it: among the copies the query found, the one in the Inbox, else any but All Mail's
(`server._in_folders_playing`, by what the last LIST said and else by name).

`mailparse.dec` and `sender_of` are the one way to read a header off a compat32 message: they unfold
it first, since `decode_header` and `parseaddr` both go wrong across a fold. `mailparse.walk` takes an
attached email as one part of its message, as a reader does; what it holds isn't the outer message's.
`mailparse.recipient_addrs` reads To and Cc as bare addresses before any encoded word is decoded, so a name
can't pass for one.

`messages.recipients` is the decoded To and Cc as text, for search. `message_recipients` (migration 35,
`store.set_recipients`) holds each address of each copy, written at ingest and again when a body is stored,
and goes with the copy. It is what `server.contacts_known` asks. Mail stored before it was read from the text,
which was cut at 2000 characters then, or for a column that was cut from the raw message where one is stored;
with none, the whole list comes when a body is stored.

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
  what the legacy workers publish: `outbox.Sent`, `outbox.DraftCopyRemoved`, `readmutq.Applied` and
  `realtime.InboxChanged`.
  It also hands each account event (below) to `realtime.reconcile`. `readmutq.Restored` (a
  queued move that won't happen) refreshes the tabs as `Applied` does. Wiring again replaces the last wiring, and `unwire()` ends it (the tests' state reset does).
  `server.__main__` passes `sys.modules[__name__]`: importing `server` there would load a second
  copy with state of its own.
  `legacy.start(server)` is the startup both doors share: the profile check, the schema, `wire`,
  then the workers. Full, those are the outbox's and the read-mutation queue's drains, the
  scheduler's jobs and IDLE; read-only, the snooze job alone.
- `python -m mailmind serve` (`mailmind/__main__.py`) is the new front door.
  `mailmind.bootstrap.front.serve` starts the app as above and serves it with FastAPI on uvicorn,
  bound to 127.0.0.1. Its `build` picks the Anthropic SDK and offers IMAPClient to an account
  that asks for it (see the Mailbox paragraph); both doors print at startup which IMAP adapter
  serves how many accounts (`[imap] ...`). Every route still runs in `server.H`:
  `mailmind.http.bridge.HandlerBridge`,
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
  every profile, then `sync`, `assess`, `backfill`, `draft-ahead`, `reconcile`, `expire` and `forget`
  in a full one. `assess` triages the mail that has no summary yet, outside the sync's single-flight,
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
  `tests/test_sse.py` checks that `connectEvents` in `app.js` listens for each, and for
  `resync` and `hello`, and nothing else. Data carries ids and counts, never a subject. Golden files write the
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
- `mailmind.kernel` stays standard library only and imports nothing else from `mailmind`. So far it
  holds `attention`, what the AI's verdict on a message asks of you: `needs_you`, `reply_worthy`,
  `commitment_worthy` and `safe_to_file`, and the first two as SQL. Queries ask it, list rows in the API carry
  each answer, and the page reads those and never works them out.
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
  several could play a role, an attribute beats a name, a shallower path a deeper one, then the
  lower name, never LIST's order (Proton Bridge shuffles it). `adapters.imap_list` reads an
  imaplib LIST answer into `Folder`s, literals included.
  `session.find_role(s, *roles)` finds a folder on a live session; the rail gives each role to
  the one folder `find` picks, and `all` (Gmail's All Mail) is where archiving goes only when
  there is no `archive`. `application.session.MailSession` is the port for one logged-in
  connection: its capabilities (what the server says after login), `enable_condstore` (before any
  select, or the folder says no `highest_modseq`), what changed since a modseq, folders, status, select, UID
  search, a message by Message-ID, headers (in batches of `HEADER_BATCH`, with Gmail's thread id
  and the arrival date when asked), flags, a body, flag and Gmail label changes, copy, move, a UID
  expunge of the UIDs named, append (the new UID when the server says it), NOOP, and IDLE with
  `interrupt()` from another thread. Names are as LIST gave them, and the errors are imaplib's
  classes through either adapter: a refusal raises `IMAP4.error`, a lost connection
  `IMAP4.abort` or `OSError`. `move_by_copy` is the move a server without MOVE gets. The adapters
  are `adapters.imaplib_session` (what the old door runs on) and `adapters.imapclient_session`
  (only it imports `imapclient`). `adapters.imap_names` holds `mailbox_arg` and `imap_quote`,
  which `accounts` re-exports, and `gmail_labels`, the one form both send Gmail.
  `mailmind.bootstrap.mailbox` picks each account's adapter: imaplib, unless the account asks for
  IMAPClient (`"imap_adapter": "imapclient"`) and the process offers it, which only the new door's
  `front.build` does (`offer`). The old door ignores the ask and never imports `imapclient`.
  `use(kind)` makes one adapter every account's, as the contract tests do, and an autouse fixture
  resets both before each test. It opens a session (`open_session(acct)`) the same way for both:
  TLS, the pin, login, txguard, `ReadOnlySession` read-only. Every IMAP connection the app opens
  comes from it, and only the imaplib adapter calls `accounts.connect`.
  `tests/test_mail_session_contract.py` runs every case through each against the fake, and
  `tests/test_mail_session_dovecot.py` against Dovecot. The rest of the context is standard
  library only.
- `mailmind.discovery` is the fourth: finding mail by what a person types. `domain.query.QuerySpec` is the search
  grammar parsed, by a parser no input can make raise. `narrows()` is what a bulk command must pass: a folder or an
  account alone spans nearly everything, and a negation never narrows by itself. `adapters.fts.to_sql` turns one into
  a MATCH and bound SQL, and `search.py` is what the legacy callers ask it through. It is standard library only.
- Two checks hold the shape. import-linter's contracts in `pyproject.toml` run in `just lint`:
  bootstrap, then http, then the contexts, then platform, then kernel; within each context,
  adapters over application over domain; no adapter imported outside bootstrap; and the
  `anthropic` and `imapclient` packages each reached only through its adapter. `tests/legacy_ratchet.txt` lists
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
  writer waiting. An IMAP session and each command (`mailbox.open_session`), an SMTP session and
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
- Each must still be idempotent; `init()` runs on every process start. A database with no
  migration to run is left alone, and the base schema runs only on one older than the frozen migrations'
  last (`migrations.BASE_UNTIL`), or it would bring back a table a migration retired.
- A migration that builds a table afresh says so in `migrations.REBUILT`, as m38 does `msg_fts`: the upgrade check
  reports `rebuilt` rather than a change of count, and leaves out the index's own tables.
- A migration that ends a table says so in `migrations.RETIRED`. The upgrade check then reports it
  as retired rather than as rows lost, and `tests/test_db_upgrade.py` holds it to that.
- Before it migrates a database that holds anything, `init` backs it up with SQLite's backup API
  to `backups/mailmind-v{N}-{UTC time}.db` beside it, readable by its owner only, keeping the
  newest 3 (`backup.py` says how to roll back).
- `python3 -m mailmind db check-upgrade` (`just db-check-upgrade`) migrates a read-only copy and
  prints only per-table row counts and integrity verdicts. A foreign-key problem the database
  already held (an orphaned body or summary from before foreign keys were on) is reported by
  table and passes; one the migrations add fails. The owner runs it on the real database before
  an upgrade; no agent does.
- `msg_fts` keeps no copy of the mail (m38: `content=''`, `contentless_delete=1`, SQLite 3.43 or newer, which `init`
  checks). Read rowids and ranks from it and columns from `messages`; its own columns read as NULL, and `snippet()`
  and `highlight()` have nothing to work on. `python3 -m mailmind db compact` (`just db-compact`) gives back freed room.
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
  MAINTENANCE. A run over every account is not work across accounts: `/api/sweep` goes through
  `server._each_account`, one job per account on its own gate, in turn. Reconcile
  (`server._reconcile_folders`) goes further: one job per folder with a `sync_state` row
  (`ingest.reconciled_folders`), so it never holds a gate across folders.
- Background work passes `wait=False` and skips its turn on `gates.Busy`, so it never stalls
  the user. `deadline=` (seconds) raises `Busy` for a job not let in by then, and it never runs.
  A job that waits on a gate from inside a job raises `RuntimeError` instead of hanging; one that
  takes another account's gate with `wait=False` may.
- Tests ask `gates.held(account)`, and hold the gate with `held_elsewhere()` from
  `tests/gate.py`, which runs a job like any caller. `tests/test_chaos.py` holds what one
  account's trouble may do to the rest; `FakeIMAPServer.stall` black-holes a command.

`pool.py` (persistent per-account IMAP sessions) and `warm.py` (multi-folder sync)
are both **default-off** behind `MAILMIND_POOL` / `MAILMIND_WARM_FOLDERS`. Code must
work with them unset.

IDLE's workers follow the configuration. `realtime.reconcile`, which the account events call,
retires the worker of an account removed, disabled or now reached another way (kind, host, port,
TLS, credential), and starts one for each account without one, up to `MAX_IDLE_ACCOUNTS`. An alias
or signature change leaves the worker be. A worker also checks its account after each IDLE cycle,
so an edit of the file, which publishes nothing, still reaches it. `readmutq` gives up a queued
move whose account is gone, as the outbox does a send.

`realtime.stop()` and a retirement only interrupt a worker's session (`MailSession.interrupt`, a
socket shutdown); the worker closes its own connection. Closing a socket another thread may be
about to wait on frees its descriptor number for the next socket opened.

`mailmind.platform.profile` is the only reader of `MAILMIND_PROFILE` (`full`, the default, or
`read-only`) and of the older `MAILMIND_NOSYNC`, which still selects read-only: any value but
empty, `0`, `false`, `no` or `off` turns it on, so a typo fails toward read-only, and read-only
wins when the two disagree. A front door refuses to start on a profile name it doesn't know.
`server.nosync()` asks the profile.

### Writes, undo, and offline

- Mutations go through `mailops.py`. A connection failure raises a member of
  `mailops._UNREACHABLE`, which the route catches and turns into a durable queue row via
  `store.read_mut_insert`. The message's row stays, marked `pending_removal` with the queue
  row's id (migration 23), and every list, search, the conversation view, the brief and the AI's
  inputs leave a marked row out (`AND m.pending_removal IS NULL`); a new list query does too.
  The queue deletes the rows it marked once the move lands. A refusal (`apply_queued_mutation`
  returns `refused` or `no_folder`), a give-up or a removed account puts the row in `error` and
  clears the mark (`store.restore_pending`), and so does Undo of a move not yet applied. A read
  mark or star queues the same way (`_readmut_enqueue_flags`): the local row shows it at once,
  and the queue row's payload keeps the flags it replaced (`was`), which a refusal or a give-up
  puts back.
- `readmutq.py` drains that queue when IMAP returns, using the `applied` flag as an
  exactly-once gate. It and `outbox.py` each run a `DurableQueue`, `QUEUE`, over their table:
  one claim wins a row, a failure backs off from 20 s doubling to 15 minutes, and the fifth
  goes to `error`, where a person's retry (`requeue`) starts the count again. `/api/pending` lists the
  move queue's unapplied rows for the panel the outbox button opens; its Retry (`readmutq.retry`) marks the message or
  shows the flags again, and its Discard (`readmutq.discard`, on the account's gate) gives them back and
  takes the entry off the undo stack (`store.undo_forget_move`). The `forget` job removes rows finished a month
  ago (`store.read_mut_forget_finished`). The worker wakes
  when kicked and when the next queued row comes due (`until_due`), at least every 30 s. The
  outbox publishes `outbox.Failed` for an attempt that failed, as it does `outbox.Sent`. A send the
  server refuses for good (`smtpsend.permanent`: a 5xx, or every recipient refused with one) goes
  to `error` on its first try (`give_up`). The gate column (`applied`, `delivered`) is set the instant the one
  irreversible step returns, so the boot-time reset of running rows never repeats it, and for a move
  before the IMAP gate opens, so an Undo or Discard waiting on it finds it set. A row's
  state changes go through `QUEUE`, not the store. The move is itself an IMAP op, so it runs
  as a job on the account's IMAP gate; the outbox's SMTP send holds none.
- The outbox's queue has a lane for each account (`DurableQueue(lane="account")`): its drain
  runs each account's rows in turn on a thread of their own. The worker's drain doesn't wait
  for them; any other caller's (a test's `outbox.drain()`) waits for every lane it ran.
- `smtpsend.send` tells apart the two sides of a message's last bytes. `before_dot` sets `outbox.wire_begun`
  (migration 32) just before they go, and with no answer after them it raises `smtpsend.Unanswered`: the row goes
  to `error` as `outbox.UNSURE`, listed `unsure`, and only a person's retry sends it again. Any answer clears the
  mark, and so does a failure before them. `DurableQueue(unsure=...)` makes `reset_stuck` do the same for a
  row a crash left with the mark set.
- A Sent copy that fails puts the delivered row back in the queue, its tries counted from the
  delivery, until it is filed or goes to `error`. The retry looks for the copy by Message-ID
  first (`smtpsend.append_to_sent`). `outbox.list_recent` shows such a row as `sent`, or as
  `error` once it gives up, and `cancel_queued` never deletes a row past its gate.
- The read-only profile writes nothing to a mail server or anywhere else outside (decision D4).
  A session opens every mailbox with EXAMINE and raises `profile.ReadOnlyError` on any write,
  before it is sent: `ReadOnlySession` over IMAPClient, and over imaplib `accounts.ReadOnlyIMAP`,
  which refuses any command not known to only read. `smtpsend.smtp_connect` and `_unsub_oneclick_post` refuse too. `ReadOnlyError` is a
  member of `mailops._UNREACHABLE`, so a move or a flag change queues as it does offline, and
  both queues' drains wait for a full run. What nothing queues (a draft, a sweep) answers 409 through
  `H.do_POST`'s catch. A new write path either queues or calls `profile.refuse_writes` first.
- A server NO or BAD means the message didn't move. A session raises it as `IMAP4.error`,
  `_imap_mutate` returns `(False, None)`, and trash, archive, junk and move answer `ok: false`
  and keep the local row. `set_flags` does the same for a star or a read mark. Never send a bulk
  `EXPUNGE`: `MailSession.expunge(uids)` removes the UIDs named and no others, and
  `move_by_copy` flags each original only after its COPY succeeded.
- The queue's idempotency key carries the request's `mutation_id`, or a random one when the
  client sends none, so only a resubmit of the same request dedupes.
- `ingest.ingest_message` (undo's one-message re-ingest) never moves the sync watermark or
  creates a folder's `sync_state` row; only a sync that saw every UID may.
- A folder counts as synced (`ingest.folder_synced`) only when its `scan_state` (migration 24)
  is `synced`: a whole scan sets it, one cut to warming's newest messages sets `recent`, and an
  incremental sync, a failed scan or a one-message re-ingest leaves it be. Anything else, the
  first open scans the folder whole, as a job of its own (`server._folder_sync`, one per folder at
  a time) that the list request waits for up to `FIRST_SYNC_WAIT` seconds. Past that it answers
  with what is stored and `syncing: true`; a scan that failed answers `sync_failed: true`.
  Opening a folder already read, other than INBOX, catches it up the same way, an incremental
  sync waited for up to `CATCH_UP_WAIT` seconds (`catching_up: true` past that), at most once
  every `CATCH_UP_EVERY` seconds unless `server._folder_touched` says MailMind put mail in it.
  Each sync ends by publishing `folder-synced` with how many it stored.
- An incremental sync reads flags again (`ingest._refresh_flags`). On a CONDSTORE server it compares
  the select's HIGHESTMODSEQ with the mark in `sync_state.highest_modseq` (migration 31): nothing if they
  match, else what `uids_changed_since` names, however old. A server without it (Proton Bridge), or one
  refusing the search, has its `FLAG_REFRESH` newest stored UIDs read instead. It reads the stored rows
  before the flags and writes each change only where the row still holds the flags it read (a click may
  land meanwhile, since the sync takes no IMAP gate), skipping rows with a flag change still queued or a
  move pending. The mark stays behind those two and a refused search, so what was skipped is read again.
  A sync that changed any publishes `ingest.FlagsChanged`, and the page reloads an open list on
  `flags-changed`.
- Undo is a LIFO stack in the database (`undo_entries`, migration 25; `store.undo_push`,
  `undo_pop`, `undo_put_back`), so it outlives a restart and is the same through either door
  (decision D3). It keeps the newest `UNDO_MAX` entries, and an entry leaves it, committed, before
  any server is asked to reverse it. Bulk actions push one grouped entry with children. `do_undo` tries every child, newest first; the ones it
  couldn't reverse (a refusal, not found, a raise) go back on the stack as a smaller group, and
  the answer counts `reversed` and `failed`. A reversal reports a server NO as `(False, why)`
  (`mailops.move_by_msgid`, `relabel_by_msgid`), never as done. A group id must be unique for the database's
  life: the frontend's `undoGroup()` is a UUID, since the stack outlives a page load and a restart.
- A bulk command's confirm carries out a plan (`mailmind.assistant.application.plans`,
  `/api/ai/do-plan` → `/api/ai/do-confirm`). The preview freezes what it matched, by message key and folder, and
  the page sends back only the plan's id, so a renumber or mail that came later changes nothing. A plan is
  good for one confirm within ten minutes, and a restart ends it (410). A copy no longer in the folder it was seen
  in is left where it went, and counted as `gone`. Each message goes through its own route (`do_write`, `do_trash`,
  `do_snooze`), under one undo group. A filter reaches Sent, Drafts, Trash and Junk only by naming a folder, a verb
  never reaches its own home (`_DO_APART`, `_DO_ALREADY`), and each message counts once, by the copy search would show.
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
  answers 423 while a queued or sending outbox row holds the draft. A save appends the new copy
  first and only then removes the old one (`smtpsend.append_to_drafts`), wherever it was, so one that
  fails or a crash leaves the old copy, at worst beside the new.
- A message is a draft if the server flags it `\Draft` or its folder is the account's Drafts
  (`server._is_draft`): the role the last LIST gave it, else `roles.names_drafts`, the whole name
  at the top of the tree or under INBOX or Gmail's parent. A name that merely holds "draft" counts
  for nothing, and a removal has no Undo, so Discard, a Send and a replacing save remove a copy only
  if MailMind saved it, the server flags it or its folder is Drafts (`outbox.draft_copy_is_ours`).
  Older `drafts` rows may name received mail: those are forgotten and the mail stays.
- A reply or forward names its original by pk until its first save, whose answer carries `resume`
  (threading, the quote) and a forward's `files`; the composer then works from those, as a reopened
  draft does, and needs no row. The outbox row keeps the original's account, Message-ID and thread
  (`orig_*`, migration 27), so `\Answered` (`mailops.mark_answered`) and the follow-up land after an
  archive deleted the row.
- A copy that leaves the server takes its `messages` row with it, in the step that records the save,
  discard or send, and `reconcile` names the folder so a list of it on screen loads again (its
  catch-up brings the new copy). A copy the server wouldn't remove stays listed.
- A composer's saves go in turn. `composeSaveDraft` chains on `cmp._saving` and names the copy the
  last save left, autosave skips a tick while one is in flight, and Send and Discard wait for it.
  It clears `dirty` only if `cmp.edits` hasn't moved since it began, so call `markEdited(cmp)` at
  every edit, never `cmp.dirty=true`. A closing save that fails goes to `state.failedSend`, with the
  float's Edit. On the server `do_draft` reads the row it replaces under the gate, and a first
  save finds an earlier one's row by the composer's key (`drafts.idempotency_key`, migration 26),
  as `do_send` does when it has no `draft_id`.
- A composer replaced after an `await` (Undo Send, a failed send's Edit → `makeRoom`) must
  close and open in the same task. In the gap `_syncBack`'s MutationObserver calls
  `history.back()`, the new composer's `pushState` lands before that traversal does, and the
  Back trap ends one entry short, so the next close steps the tab off the app.
- **`pk` is not stable.** Reconcile can renumber rows, so anything durable keys on
  `(account, message_id)` — see `store.get_message_by_msgid`. A stored AI answer is checked
  against `mailmind.assistant.domain.fingerprint`, which names messages by Message-ID: a thread
  summary, a reply drafted ahead, the brief, the delta and the review all use it. Migration 22
  moved stored thread summaries onto it. A stored brief and a stored review name their messages by
  key and folder (below), and a read gives the pks they have now (`store.pk_now`).
- **A message's key outlives its rows.** `store.mint_key` gives one key per account and Message-ID
  (`mailmind.mailbox.domain.identity`; a message with none goes by its envelope), and
  `messages.message_key` names it on each copy (migration 30). `store.save_summary` writes what the AI
  said to every copy and under the key (`assessments`), and `store.upsert_message` gives it to a copy that
  arrives, so a move, an Undo, a renumber or Gmail's All Mail copy pays for no second call.
  `needs_summary` offers one copy of each. A key with no copy left waits 30 days (`gone_at`) for one to
  come back, then the `forget` job ends it. `store.init` gives a key to a row an older commit wrote. Message
  rows in the API carry it as `key`, beside `pk`. New durable state about a message keys on it.
- **A snooze outlives its row.** `store.set_snooze` writes `messages.snoozed_until` and a `snoozes` row
  (migration 33, by key and folder), and `upsert_message` gives it to a copy that arrives in that folder, so a
  resync keeps it and a copy filed elsewhere isn't hidden. An Undo of filing brings it back; one come due with
  no copy in its folder ends at `store.due_snoozes`. The undo entry names rows by key and folder too (`where`).
  A message that wakes is unread on the server as well: `server.wake_snoozed` queues the mark for read mail
  (`store.queue_flags`), or the next sync that read its flags would take it back.
- **Muting tells the server too** (decision D6). `store.muted_thread_seen` queues `\\Seen` for the thread's
  unread members and `apply_mute_on_ingest` for mail that arrives later, skipping one with a mark still open
  (a refused one waits in the tray). The undo entry names those members by key and folder, and Undo marks
  unread again only them.
- **A thread id is sticky.** `threads.recompute_threads` mints an `rf:` id from the thread's
  union-find root (the smallest Message-ID it names) once, and keeps it as the thread grows; a
  Gmail thread id gives `gm:` instead. When a thread's rows take another id (a merge, or a thread
  that gained a Gmail id), `threads._rekey` moves what was kept under the id no row holds any
  more, in the same transaction: `muted`, `followups`, `thread_summaries` and `draft_ahead`'s
  `t:` keys. A new table keyed on a thread id joins `_rekey`.

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
`Content-Security-Policy-Report-Only` from `perimeter.page_policy`: script files from this server and nothing else, connections to this server
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
Any other quoted value (a Message-ID, an expiry token) goes through `accounts.imap_quote`, which
refuses CR, LF and NUL with `BadValue`, an `IMAP4.error` too: a sender can fold the Message-ID
header so the ID stored holds a line break.

`CSRF` is a per-process token. `web/index.html` ships a `__CSRF_TOKEN__` placeholder in a
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
`feature`: `triage`, `triage-batch`, `thread`, `brief`, `delta`, `review`, `draft`, `draft-ahead`, `rewrite`,
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

A backlog goes through the Message Batches API at half price (`pricing.batch_micros`, decision D7): `BATCH_MIN`
messages waiting is one, whether a first import or after an outage, and new mail stays synchronous.
`summarize.batch_submit` sends it and `batch_collect` brings the answers in, both from `assess_round`, which
collects first since that sends no mail and the answers are paid for. `python3 -m mailmind assess --batch` does
the same for any size. `MAILMIND_NO_BATCHES=1` keeps the job from sending one, so a backlog goes synchronously as it
did; a batch already out is still brought in.
- One at a time (migration 36: `ai_batches`, and `ai_batch_items` by message key). `needs_summary` leaves out what a
  batch holds, and while one is out everything older than the newest it took (`cutoff_ts`), which waits for the next
  batch rather than going at full price.
- The limit. A batch's cost is only known when its answers are in, so it takes what fits in `1/BATCH_SHARE` of what
  today leaves by `pricing.batch_estimate`, which errs high. `CostGovernor.room` counts the estimates of batches
  still out (`UsageLedger.reserved`) against background work, not against someone asking, so a day with a batch out
  and heavy use can pass the limit by up to that share. Answers count on the day they are collected.
- Sending is never retried: the API has no idempotency key, so another try could pay twice, and the next round tries
  anyway. A row is committed as `submitting` first. One that fails is dropped, and one a crash left is dropped after
  `BATCH_SEND_GIVE_UP`; a batch that reached the API in that window is paid for and never collected.
- A batch holds mail, so `batch_collect` deletes it from the API once its answers are stored, trying again each
  round until it goes. A message with no answer (errored, expired, canceled) waits again.

The brief, the delta and the review each have a table (migration 37): `briefs` and `reviews` by account set
and local day, `deltas` by account set, watermark and fingerprint, so a window that changes is written once
more however large it is. A review's lists and a brief's groups are stored by message key and read back as
the pks those messages have now, and a message that has gone leaves them. A delta's items are the model's own
words, with no message behind them. Each table keeps a week. They are read and written apart:
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
`rules.save` writes the file whole (a temp file, owner-only, renamed over it). A read parses it
again only when it changed; one that won't parse leaves the last rules read well, so a bad edit
never stops a sync, and the next save moves it to `backups/` first. The classifier reads
through `rules.sets()`.

A rule change classifies again the stored mail it covers (`ingest.reclassify`, called by whatever calls
`rules.add` or `rules.remove`): each row a rule decides now or decided before. A row no rule had a say in
stays as ingest judged it, since not all it read then is kept. An edit of the file by hand reaches only new mail.

Ingest keeps a message's unsubscribe headers (`messages.list_unsub_raw` and `list_unsub_method`, through
`mailparse.list_unsub`), `none` included. `server._resolve_unsub` answers for a sender from the newest message
that can say: those columns, a stored body, or for older mail the two headers read off the server, within
`UNSUB_READ_WAIT` on the account's gate. `sender_unsub` (migration 34) keeps the answer past the sender's mail,
so a second try after a cleanup's sweep still knows the way.

## Conventions

- **Don't create `*.bak-*` snapshot files.** The repo used to carry 38 of them; they are
  now gitignored. Use a git branch.
- **No inline scripts or event handlers in the page.** Its content security policy lets script files from this
  server run and nothing else, so an `onclick=` or a `<script>` block is a violation: attach a listener instead
  (delegated, like `#doPanel`'s).
- **Keep the page's files free of raw control bytes.** A single literal NUL byte once
  made `grep` and `ripgrep` treat the file as binary and silently return nothing past it,
  hiding half the frontend from every search tool. Use escape sequences in string
  literals.
- **`ROADMAP.md` lists only work that is still open.** When something ships, summarize it
  into the README's Features section and delete it from the roadmap — no "✅ DONE"
  entries. Verify status against the code, not the doc, and cite functions and symbols
  rather than line numbers (they drift).
