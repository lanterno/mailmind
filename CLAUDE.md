# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

A `justfile` wraps everything below — `just` alone lists the recipes. The raw commands
work identically if `just` isn't installed. `just run`, `just lint` and `just test` need
[uv](https://docs.astral.sh/uv/) (`brew install uv`); `uv run` installs the runtime packages and
the dev tools on first use.

```bash
just verify                       # lint + compile + all tests + frontend check + audit — run before committing
just test-full                    # every test, plus the browser tests in Chromium (Playwright)
just test-dovecot                 # the MailSession contract against Dovecot in Docker
just demo [port]                  # the whole app on fictional mail and fake servers: nothing of yours is read or changed
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

Tests — `just test` (`uv run pytest`) runs them all. `just test -k hardening` picks by file or test name, so `-k` also picks a
single test: `just test -k test_a_rebound_name_reads_nothing`. These are the first to run for a change in what they cover, with
the cases pytest collects for each (a test through `app` counts once per front door):

```bash
uv run pytest tests/unit/curation/test_classifier.py        # classifier behaviour (35)
uv run pytest tests/security/test_sanitize.py               # HTML sanitizer: 24 attack classes, expects 0 leaks (68)
uv run pytest tests/unit/content/test_css_sanitizer.py      # CSS sanitizer unit tests (45)
uv run pytest tests/unit/curation/test_expire.py            # expiry rules (19)
uv run pytest tests/unit/calendar/test_upcoming.py          # Today & next invites, RRULE expansion + the Waiting view's recipient (40)
uv run pytest tests/unit/discovery/test_contacts_known.py   # the "new to you" lookup: who counts as known, exact matching, CSRF (19)
uv run pytest tests/unit/compose/test_send_after.py         # /api/send refuses passed and far-off send times (5)
uv run pytest tests/unit/compose/test_send_guard.py         # the composer's send-time checks, run in node (157)
uv run pytest tests/unit/compose/test_draft_resume.py       # reopening a saved draft: split, files, Bcc, threading, refusals, saves (147)
uv run pytest tests/unit/compose/test_draft_resume_ui.py    # the composer's reopen helpers, run in node (25)
uv run pytest tests/security/test_hardening.py              # security and data-loss fixes, one test or more per fix (138)
uv run pytest tests/security/test_mail_frame.py             # a mail body's own document: its policy, who may frame it, what it holds (58)
```

`test_send_guard.py` and `test_draft_resume_ui.py` slice their block (`/* send-guard:pure:start */` … and
`/* draft-resume:pure:start */` …) out of `web/assets/legacy/pure.js` and run it in node. That file is written from
`web/src/features/compose/sendGuard.ts` and `draftResume.ts`, which stay free of DOM and `state` references and have Vitest
twins beside them; edit those and run `just gen-web`. The send guard's two suites share
`tests/fixtures/send_guard_words.json`.

### The pytest suite in `tests/`

`tests/unit/<context>/` holds each context's tests, `contract/` one promise through every adapter (and the fakes against
themselves), `security/` what must never get through, `characterization/` the app as it answers (goldens and the hard-won
fixes) and `e2e/` the whole program. `tests/MAPPING.md` says where each of the old root suites' tests now lives.

They pin today's behaviour ahead of the refactor. Each test sees only fictional
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
- `app` runs the app in-process through each front door in turn: `door=old` is `mailmind.bootstrap.old_door`'s
  `ThreadingHTTPServer`, `door=new` the app `mailmind.bootstrap.front.build` makes, on uvicorn.
  So every app test runs twice, both runs share one golden file, and `app.door` says which run
  this is. In the world's config Cy's Gmail asks for IMAPClient, so the new door's run reaches it
  through IMAPClient and the old door's through imaplib. Its CSRF token is fixed, and the clock
  is frozen at `conftest.NOW` (2026-11-02 12:00 UTC); `app.clock.shift(timedelta(...))` moves
  it. `tests/e2e/test_front_door.py` runs each door as a program instead.
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

`tests/unit/mailbox/test_queue_state_machine.py` runs Hypothesis over the command path and Undo: random moves, flag
changes, outages, refusals, crashes (in a click and on the worker), syncs, Undo, Retry and Discard. It runs 12 fixed cases in the suite;
`MAILMIND_MACHINE_RUNS=1500` asks for that many with fresh seeds, and `MAILMIND_MACHINE_STEPS` for longer ones.

`tests/e2e/browser/` drives the page in Chromium (Playwright) on the `app` fixture, and `test_next_*.py` the React app, built once for the session into a folder of its own:

- The page may reach only the app's origin.
- Its clock starts at the server's frozen time.
- Console errors fail the test, and so does any content security policy violation.
- axe's findings are compared with `tests/golden/browser/axe_baseline.json`, which may only go down.
- `page.pw.evaluate` has no timeout, so a promise that never settles hangs the run for good. A test waiting on animations leaves out
  the looping ones (`FADES_DONE`).

These tests are marked `browser` and skipped unless pytest runs with `--browser`.
`just test-full` installs Chromium if needed and runs everything. `just verify` leaves them out
to stay fast.

`tests/contract/test_mail_session_dovecot.py` holds the MailSession contract against a real server,
Dovecot 2.3 in Docker (pinned by digest), through each adapter, and checks what each call left
through a connection of its own. Its tests are marked `dovecot` and skipped unless pytest runs
with `--dovecot`; `just test-dovecot` runs them, and needs Docker running. One container serves
the run, and each test logs in as a user of its own, with an empty mailbox.

There is no build step. `pyproject.toml` declares the runtime packages and, in its dev group,
the dev tools (pytest, ruff, import-linter, Playwright and the like), all locked with hashes in
`uv.lock`, and `.python-version` pins 3.14 for uv and pyenv alike. `just lint` is `ruff check` with pyflakes' rules only (`select = ["F"]`),
then `lint-imports` (import-linter) on the `mailmind` package's layers, then `pyright`: basic rules across the package
and strict ones on what `[tool.pyright]` lists, which a package joins once it reaches zero. New tests are pytest-native, in the folder of `tests/` they belong to;
pytest's `--import-mode=importlib` keeps a folder like `tests/unit/calendar` from shadowing a module.

`just check-web` (part of `just verify`) type-checks `web/src` with `tsc`, lints it with ESLint, runs its Vitest suites and checks that
`web/assets/legacy/pure.js` is what `web/src/features/compose` gives; it installs `web/node_modules` from the lockfile if they
are missing. `just gen-web` writes `pure.js`. `just gen-api` runs the whole test suite with `MAILMIND_RECORD_API` set and
writes `web/src/lib/api/types.gen.ts` from the JSON answers it saw, shapes only (`tests/gen_api_types.py`), since the routes
have no schema to generate from: run it when an answer's shape changes, and review the diff.

`web/eslint.config.js` covers `web/src` only: typescript-eslint, `react-hooks` and `jsx-a11y`, each finding an error. A disable sits on its
line with a reason. Three rules are our own:

- Only `features/mailbox/MailFrame.tsx` may write HTML into the DOM (`innerHTML`, `srcDoc`, `dangerouslySetInnerHTML` and their like).
- A feature imports another only through its `index.ts`, which holds what the others use (`lint/slices.js`). `app/` and `main.tsx` compose the features and may import any file. A barrel that grows can close a cycle: nothing at a module's top level may read another feature's binding.
- `machine.ts`, `model.ts`, `sendGuard.ts`, `draftResume.ts`, `linkRisk.ts` and any `*.machine.ts` are pure: no DOM, no fetch, and only each other and types as imports. The machine gets its stores through `Env`.

Never disable `react-hooks/exhaustive-deps` or `rules-of-hooks`: the React Compiler quietly skips a component that does (`react-hooks/rule-suppression` refuses it). What an effect reads but doesn't run for goes through `useEffectEvent`. typescript-eslint needs a TypeScript with a JavaScript API and `tsc` is on 7, which has none, so it and a TypeScript 6 live in the `web/lint` workspace until it supports 7.

`just audit` (part of `just verify`) looks up what `uv.lock` and `web/package-lock.json` pin: `pip-audit` for the first, `osv-scanner` for both.
A finding fails it. With no network it says so and passes. `web/osv-scanner.toml` holds what it may pass over, each with its reason and a date it lapses on.

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
  third-party package; `tests/e2e/test_front_door.py` checks that.
- **The page runs as it stands.** `web/index.html` is the markup, `web/assets/styles/` the CSS (`fonts.css` the
  self-hosted fonts, Latin, Cyrillic and Greek; Japanese glyphs come from system fonts), and `web/assets/legacy/` the
  scripts: `theme.js`, `pure.js` (written from `web/src/features/compose`) and `app.js`, ~310KB of classic script. No build is needed:
  `mailmind.platform.webroot` serves this source to either door with gzip and an ETag, so the old door still needs
  nothing installed. `just build-web` (Vite, Node) writes the same files, named by their content, to
  `mailmind/http/dist` (gitignored), served for good while it is as new as every source file, so an edit never leaves
  the app on old files. A new top-level script joins `CLASSIC` in `web/vite.config.js`. `/assets/` serves only `.css`,
  `.js` and `.woff2`. `/api/events` opens with `hello{build}` (`sse.HELLO`, no id), and a tab that loaded another build
  offers a reload (`onHello`). `web/src` holds strict TypeScript: the pure modules and the API client (`lib/api/client.ts`, typed by `lib/api/types.gen.ts`).
  `web/next.html` and `web/src/main.tsx` are the React app at `/next` (React 19 with the React Compiler, TanStack Query, React Aria Components), beside the page and on its
  stylesheet. Unlike the page it exists only as built: without `just build-web`, `/next` answers 404 and says so, and `/` is untouched. Where you are lives in the URL
  (`app/route.ts`: a `ListSource` and what is open), the event stream invalidates queries (`app/realtime.ts`), `features/mailbox` holds the rail, the list, the reader
  and filing, and `app/commands.ts` the one list of commands that the keys and the palette (⌘K) both read. `features/compose` is the composer: `machine.ts` holds its rules with no DOM (`createComposing(env)`, what
  `machine.test.ts` drives), `compose.ts` binds that to the page, and `Composer.tsx` shows the copy the machine publishes. `features/assistant` is today's brief, the lists the digest answers (`/next/brief/needs`, `tidy`, `topic/<label>`),
  Commitments, Your week, the new-mail pill, and Ask and Do from the search box, `features/status` the sync pill, the account alert, the meeting reminder and the keyboard help, and `features/settings` the connections.
  `features/discovery` is the Feed and the Paper Trail, the contact card and the Document Hub, `features/curation` the Screener, the drawer and a sender's cleanup, and `features/attention` the snooze menu. `lib/backTrap.ts` makes Back close the top layer (`useLayer`), and `features/mailbox/swipe.ts` is a phone's swipe. It does what the page does now, and the top bar links to `/` until it takes the page's place. A modal makes everything outside it inert, and a click goes straight through that, so what must stay in reach over one carries `data-react-aria-top-layer`, as the floats do. Unlike the page's,
  its search box acts on Enter, and its pill names only mail that landed outside the list on screen, since a list refreshes in place. A value hooks read outside React is a `lib/store.ts` store, whose hook is `useValue`, since the compiler
  doesn't take a method named `use` for a hook. The compiler also keeps what a call with no reactive input gave, so a component reads the time or storage in a child mounted when it's needed, or through a store.
- **Nothing personal is committed.** `mailmind.db`, `mailmind.config.json`,
  `mailmind_rules.json`, `mailmind_views.json`, `mailmind.log*` and `*.jsonl` audit trails are gitignored.
  They live in the data directory: the repo root, unless `MAILMIND_HOME` names another.
  The repo is public — treat any real address, employer or message count as a leak.

## Architecture

Pipeline, roughly in data-flow order:

```
mailmind.config.json → mailmind.accounts → the Mailbox's Ingest → curation's classifier → mailmind.mailbox's store (SQLite)
                                                                ↓
                                              mailmind.assistant (Claude API) → mailmind.http + web/
```

Around that spine: `mailmind.mailbox` (IMAP writes, IDLE), `mailmind.compose`
(send), `mailmind.discovery` (FTS + operator grammar), `mailmind.calendar` (invites), and `mailmind.assistant`
(everything asked of Claude).

`discovery.build` reads a date with no zone at this machine's midnight. `discovery.visible` leaves snoozed mail out of
a search and of what a command acts on, unless the query says `is:snoozed`. `discovery.BM25` is the one table of
ranking weights, which Ask uses too. All three come from `mailmind.bootstrap.discovery`, below. A message is one search result
however many folders hold it: among the copies the query found, the one in the Inbox, else any but All Mail's
(`StoredMail._in_folders_playing`, by what the last LIST said and else by name).

`mailmind.content.mime`'s `dec` and `sender_of` are the one way to read a header off a compat32 message: they unfold
it first, since `decode_header` and `parseaddr` both go wrong across a fold. `mime.walk` takes an
attached email as one part of its message, as a reader does; what it holds isn't the outer message's.
`mime.recipient_addrs` reads To and Cc as bare addresses before any encoded word is decoded, so a name
can't pass for one.

`messages.recipients` is the decoded To and Cc as text, for search. `message_recipients` (migration 35,
`store.set_recipients`) holds each address of each copy, written at ingest and again when a body is stored,
and goes with the copy. It is what `discovery.known` asks. Mail stored before it was read from the text,
which was cut at 2000 characters then, or for a column that was cut from the raw message where one is stored;
with none, the whole list comes when a body is stored.

The app is one view, a real folder-and-account client whose Inbox opens on today's brief
(`renderBrief`, built from the `/api/digest` items and the cached `/api/briefing` narrative; its
side column, `briefSideHtml`, from `/api/upcoming` and the `__waiting__` view).
A separate Briefing view existed until it was folded into this one; `body.mode-client`
survives as a CSS specificity hook, not a mode.

### The `mailmind` package

The app lives in `mailmind/`. `server.py`, at the repo root, is only the old front door's entry point.

- `mailmind.platform.settings` is the one source of the data paths and the port. Code calls
  `settings.current()` where it opens a file or checks the port, never once at import, so a test
  can swap the whole data directory with `settings.use(...)`. `MAILMIND_HOME` names the data
  directory; unset, it is the repo root. It must exist, and nothing creates it.
  `python3 server.py [port]` and `python -m mailmind serve [port]` put the port in the settings.
- `mailmind.bootstrap.wiring.wire()` subscribes the app, on `mailmind.platform.bus`, to
  what the workers publish: Compose's `Sent`, `Failed` and `DraftCopyRemoved`, the Mailbox's `Applied` and
  `InboxChanged`.
  It also hands each account event (below) to `realtime.reconcile`. `Restored` (a
  queued move that won't happen) refreshes the tabs as `Applied` does. Wiring again replaces the last wiring, and `unwire()` ends it (the tests' state reset does).
  `wiring.start()` is the startup both doors share: the profile check, the schema, `wire`,
  then the workers. Full, those are the outbox's and the command path's drains, the
  scheduler's jobs and IDLE; read-only, the snooze job alone.
- `python -m mailmind serve` (`mailmind/__main__.py`) is the new front door.
  `mailmind.bootstrap.front.serve` starts the app as above and serves it with FastAPI on uvicorn,
  bound to 127.0.0.1. Its `build` picks the Anthropic SDK and offers IMAPClient to an account
  that asks for it (see the Mailbox paragraph); both doors print at startup which IMAP adapter
  serves how many accounts (`[imap] ...`). Every route runs in `mailmind.http.handler.H` (see Routing):
  `mailmind.http.bridge.HandlerBridge`,
  the FastAPI router's fallback, hands it each request without a socket, on a daemon thread of
  its own, and streams back what it writes, the event stream and the export included.
  `mailmind.http.middleware.Perimeter` runs the perimeter (see Security model) before any of it,
  and before a body is read. `mailmind.http.serving.Server` ends open streams as shutdown begins
  (`HandlerBridge.close`), so Ctrl-C stops at once.
- `mailmind.platform.profile` says how the process runs, `full` or `read-only` (see Writes,
  undo, and offline).
- `mailmind.platform.workers.gates` is the IMAP gate every live-IMAP job passes (see
  Concurrency). `account_worker` beside it holds the gate for each account, and the connection kept
  for one between jobs (`KeptConnections`, `ConnectionPolicy`). `idle_supervisor.IdleSupervisor` keeps
  one worker thread per key in line with what's wanted. Both are standard library only, and know nothing of IMAP.
- `mailmind.platform.workers.scheduler.Scheduler` runs the background jobs. A job is a round (a
  function doing one round of the work) on a lane of its own, a thread named `job:<name>`, so
  its rounds never overlap. A round may return the seconds until its next; one that raises is
  logged and counted, and the job goes on. `mailmind.bootstrap.background.schedule(jobs)` is the job table: `snooze` in
  every profile, then `sync`, `draft-push`, `assess`, `backfill`, `draft-ahead`, `reconcile`, `expire` and `forget`
  in a full one. `assess` triages the mail that has no summary yet, outside the sync's single-flight,
  and a sync that brought new mail kicks it (`background._kick`). With AI off, or triage held back
  by the cost governor (see AI calls), it tries nothing: `assistant.assessments.summarize_pending` returns 0
  at once, and a round (`assistant.assessments.round`) that is only yielding to a click comes back in `ASSESS_YIELD` seconds.
  `/api/debug/jobs` shows each job's timing and last outcome, an error as its class name only.
  A test calls the round itself (`mailbox.syncing.backfill_round()`) or `jobs.run(name)`. It is
  standard library only.
- `mailmind.platform.logging` is the log, on the standard library's `logging`. A module takes
  `from mailmind.platform.logging import get_logger`, never `from mailmind.platform import logging`, so that
  name stays the standard library's, then `log = get_logger("jobs")` and
  `log.warning("%(job)s failed", {"job": name}, exc_info=e)`. A message is a literal, and a test holds the
  package to it. `Redact` withholds what it can't vouch for: a value shows only under a name on `ALLOWED`,
  anything that looks like an address is withheld even there, and of an exception only the class shows. A new
  field joins `ALLOWED` only if its value can never be a person's. A line goes to stdout for someone at a
  terminal (`[jobs] sync failed: ConnectionResetError`), from the moment the module loads, the command line
  included. Once `wiring.start` has called `setup()`, it also goes as a JSON object to `mailmind.log` in the data
  directory, rotated and readable by its owner only, with `request_id` (`H.handle_one_request`) and `job_id`
  (`Scheduler.run`) from `contextvars`. `MAILMIND_LOG_LEVEL=DEBUG` shows debug lines and each exception's text at
  the terminal, never in the file. The `mailmind` logger doesn't propagate. `/api/debug/health` says how the job
  lanes, IDLE's workers, the queues, the breaker and today's AI ledger stand, in ids and counts, and reading it
  doesn't count as someone here. Only a command's own output still prints (`bootstrap/cli.py`, `__main__.py`, `wiring.assess_batch`, `Directory.check` and `materialize`), which a test holds. It is standard library only.
- `mailmind.platform.bus` is an in-process publish and subscribe. An event is a frozen dataclass
  of ids and counts, and its class is its topic. Handlers run in the publisher's thread once the
  change has committed; one that raises is logged by class name and the rest still hear. Nothing
  on it is durable: work that must survive a crash is a queue row written with the change.
- `mailmind.platform.sse` is the hub behind `/api/events`, the live event stream. `sse.publish`
  numbers each event `<run>-<n>` and keeps the last 512. A tab resumes after the `Last-Event-ID`
  its EventSource sends; one the hub can't resume (an earlier run, or further back than it keeps)
  gets one `resync`. `sse.TOPICS` lists every topic and `publish` refuses any other;
  `tests/unit/platform/test_sse.py` checks that `connectEvents` in `app.js` listens for each, and for
  `resync` and `hello`, and nothing else. Data carries ids and counts, never a subject. Golden files write the
  run as `<run>`.
- `mailmind.platform.workers.durable_queue.DurableQueue` is the queue the outbox and the offline
  move queue share (see Writes, undo, and offline). It is standard library only.
- `mailmind.platform.workers.singleflight.SingleFlight` makes each costly AI read once. Only one
  run goes at a time for each key. A caller that finds one going waits, then reads what it
  stored. The leader reads again before it makes, unless `recheck=False` asks for a fresh one.
  The brief and delta, the review and thread summaries each hold one (`_FLIGHTS`), and so does
  drafting ahead (`assistant.drafting.DRAFT_FLIGHTS`, whose `busy(key)` answers "generating"). It is standard
  library only.
- `mailmind.platform.db` opens the database, runs its migrations, backs it up before one and
  checks an upgrade on a copy (see Schema migrations), and holds `uow.UnitOfWork`, how a use case gets a connection. It is
  standard library only, since `python3 server.py` uses it.
- `mailmind.content` is the library every context reads mail through, standard library only, and it knows nothing of
  mailboxes or accounts: `mime` (a raw message as parts and header values), `sanitize` (the HTML sanitizer, verbatim),
  `text` (what a list shows and what search indexes), `headers`, `quote` (the part of a reply you typed, and the
  quote after it) and `frame` (a body as the document its frame loads). A `domain` package may import it.
- `mailmind.kernel` stays standard library only and imports nothing else from `mailmind`. So far it
  holds `when` (`now_iso`, `rel_time`, `ago`), `addresses` (a typed recipient, split, validated and IDNA-normalized), `snoozed` (whether a row is snoozed, as SQL), `assessment` (`Assessment`, what the AI said of a message and how it came to say it, its importance held to 1 to 5) and `attention`, what the AI's verdict on a message asks of you: `needs_you`, `reply_worthy`,
  `commitment_worthy` and `safe_to_file`, and the first two as SQL. Queries ask it, list rows in the API carry
  each answer, and the page reads those and never works them out. `row` (`Row`, a stored row read by column name) and `parsed`
  (`is_list` and `is_object`, the narrowing of JSON as it parsed) are for the types of those that read one.
- `mailmind.assistant` is the first bounded context: what MailMind asks Claude (see AI calls).
  `application` holds the ports, the `Gateway` and the `CostGovernor`, `calls` (`Calls`, the way every call goes), `status` (`Status`, how AI stands, and each call's outcome),
  `assessment` (`Assessments`, a message's verdict, its batches and the assess job's round), `context` (`ContextAssembler`, what the model reads of a conversation: the first message and the
  last 19, trimmed and within the caps, with a missing body asked of the `Mail` port at interactive priority), `thread_digest` (`ThreadDigest`), `brief` (`Briefs`), `review` (`Reviews`),
  `reply_draft` (`ReplyDrafts`), `rewrite` (`Rewriter`), `ask` (`Asker`), `asking` (`Asking`, what a request asks of those), `prepared_replies` (`PreparedReplies`, drafting ahead) and
  `bulk` (`BulkCommands`, a command in words, planned and frozen; `Filing.carry_out` runs its confirm). `domain` holds what it knows about the models and the features, `prompts` (the book of what the model is told, below), `prepared_reply`
  (a reply drafted ahead's rules: its key, what's eligible, when one may be served, the backoff and the three strikes; `reply_worthy` gates what is spent, not what is served),
  `brief`, `review` and `digest` (what is taken from a model's answer, within its caps, and what stands in when it won't do), `citations` (only the ids the model was shown)
  and `action_plan` (what is taken from a plan, and how far a verb may reach), and
  `adapters` the `LanguageModel`s: `AnthropicModel` (the SDK), `UrllibModel` (standard library)
  and `DisabledModel`, and `sqlite` what the AI said (`answers`). `mailmind.bootstrap.assistant` binds them, resolves the key, and is how the routers, the other
  contexts and the tests reach the Assistant (`assistant.calls`, `.status`, `.assessments`, `.context`, `.threads`, `.briefs`, `.reviews`, `.replies`, `.rewriter`, `.asker`, `.asking`, `.drafting`, `.bulk`).
  The mail it reads goes through its `Mail` port and a bulk command's filter through `Search`, both bound there.
- `mailmind.accounts` is the second: the mail accounts and how MailMind reaches each. `mailmind.bootstrap.accounts` binds it, and the app reaches it there: `config`, the `Directory` of the file, `connections`, `health`, `logins`, and `accounts.get(email)`, `accounts.connect(acct)` and the like, the last through `adapters.imap_connection`. `mailmind.accounts.api` names what the other contexts' wiring asks of it (the events, `provider_of`, `TLSPinMismatch`).
  `application.ports` holds `AccountRepository`, `SecretResolver` and `ConnectionProbe`. `adapters.config_file` is the first, which `application.directory.Directory` reads and writes `mailmind.config.json`
  through. `ConfigFile.read` parses the file again only when its inode, size or modification
  time changed, and hands each caller a copy of its own; `write` replaces it atomically, readable
  by its owner only. `adapters.secrets` is the second, caching an `op` read only when it succeeds, and
  `domain.credential.CredentialRef` the one reader of the styles the file allows (plain, `op`, `env`, the Bridge password).
  `Directory` and `mailmind.bootstrap.assistant` (the API key, the daily limit) read through
  both. `ConnectionProbe` is bound in `mailmind.bootstrap.accounts`, and is no adapter of Accounts': a login goes through the Mailbox's sessions, which pick the IMAP adapter.
  An account is still a dict, as the file holds it or as `domain.account.normalized` fills it in, and the whole app reads `acct["host"]`. `Endpoint` (`imap_endpoint`, `smtp_endpoint`) works out a server's host, port and TLS from an entry and its type,
  and an SMTP host left out is the IMAP host, so an account with one endpoint has both. `add_account` refuses an address already there. Both it and `update_account` refuse a credential no login could read, and an endpoint no connection could reach, the last only for what the
  write changes (`refuse_bad_endpoints`): what the file already held is the owner's, and an entry is read however it stands. `validate_new` still judges a whole entry, as it did: an edit of one with an unknown type, a port out of range or a generic account's host gone says so. `domain.draft` says what an unsaved account may try (see Security model).
  `domain.provider.ProviderProfile` says what an account's server does on its own and its
  quirks: Gmail files sent mail itself, archives by dropping `\Inbox` and threads by X-GM-THRID;
  Proton Bridge (or any loopback STARTTLS server) is fragile, recycled sooner in the pool and never
  bulk-expunged, and marks trashed mail read; `with_capabilities` adds what CAPABILITY says.
  `provider.resolve(acct)` replaces checking `kind == "gmail"`. `domain.health` says how an account stands: `error_kind` is what kind of trouble an error names, from its class and never its text, and `AccountHealth` the
  read model. The Mailbox only reports (`syncing.sync_record`, and `accounts.error_kind` through its port), and `application.health.Health` answers `/api/account-health`. `domain.events` holds
  `AccountAdded`, `AccountChanged` and `AccountRemoved`, which `config.add_account`,
  `update_account` and `remove_account` publish on the bus once the file holds the change (a
  refused change publishes nothing), and `CredentialsRejected`, which `mailbox.try_login` publishes when a server says no to a login. `application.logins.Logins` hears it and holds that login back (`domain.login_hold`):
  five minutes, twice that for each refusal after a hold ended, up to an hour, since a server counts them and everything the app does opens a connection of its own. `mailbox.open_session` asks first and raises `LoginUnreachable` with nothing sent, so the sync, IDLE's workers,
  the backfill, the queues and a click all do as they did on the refusal, and health reads the same. A connection lost during LOGIN and `[UNAVAILABLE]` are no refusal. A hold names the server, address and credential together, so another of any of them isn't held, a file edited by hand included. `AccountChanged`,
  `AccountRemoved`, a login that works and the page's Retry (`syncing.do_sync_retry`) end it, and Test goes whatever it says (`mailbox.try_login`); a draft (`mailbox.session_for`) neither asks nor tells. It is process state, wired by `wiring.wire`, so a restart ends it and the command line has none. SMTP has its own: a refused AUTH is a 5xx, and the outbox gives up on its first try.
  `domain.tls_pin` says how a loopback certificate is pinned, and `is_loopback` is the one list of hosts whose certificate checks `tls_context` may skip
  (see Security model). It is standard library only.
- `mailmind.mailbox` is the third: the mail on each server and its folders. So far it holds
  `domain.roles`, which says which folder plays which role (`inbox`, `sent`, `drafts`, `archive`,
  `all`, `junk`, `trash`, else `custom`): INBOX by name, then the SPECIAL-USE attribute, then the
  whole last segment of the name, never part of one ("Bulk orders" is nobody's junk). Where
  several could play a role, an attribute beats a name, a shallower path a deeper one, then the
  lower name, never LIST's order (Proton Bridge shuffles it). `adapters.imap.imap_list` reads an
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
  `IMAP4.abort` or `OSError`. `move_by_copy` is the move a server without MOVE gets.
  `domain.destination` says where a filing goes (`resolve` on a try, `known_without_server` what holds as a command is accepted), and `domain.move_strategy.choose` which way, as a value (`MoveStrategy`):
  `Way.LABELS`, `Way.MOVE` or `Way.COPY`, and whether to read `\Seen` first because the server will set it. It goes by the
  verb, not a folder's role, and takes what the profile says and the session's capabilities as plain values. No way sends a
  bulk EXPUNGE, on any server. `ServerOps.apply` carries it out, and each adapter's `move` (what a sweep and a reversal call) asks
  the same `offers_move`. The adapters
  are `adapters.imap.imaplib_session` (what the old door runs on) and `adapters.imap.imapclient_session`
  (only it imports `imapclient`). `adapters.imap.imap_names` holds `mailbox_arg` and `imap_quote`,
  which `accounts` re-exports, and `gmail_labels`, the one form both send Gmail.
  `adapters.sqlite` holds what is stored of the mail, the command log's rows and the undo stack: the app calls it `store` (`from mailmind.bootstrap.mailbox import store`), and it asks `domain.threading` for thread ids.
  `mailmind.bootstrap.mailbox` picks each account's adapter: imaplib, unless the account asks for
  IMAPClient (`"imap_adapter": "imapclient"`) and the process offers it, which only the new door's
  `front.build` does (`offer`). The old door ignores the ask and never imports `imapclient`.
  `use(kind)` makes one adapter every account's, as the contract tests do, and an autouse fixture
  resets both before each test. It opens a session (`open_session(acct)`) the same way for both:
  TLS, the pin, login, txguard, `ReadOnlySession` read-only, and a login its server refused not at all until its hold ends (`accounts.logins`, above). Every IMAP connection the app opens
  comes from it, and only the imaplib adapter calls `accounts.connect`.
  `tests/contract/test_mail_session_contract.py` runs every case through each against the fake, and
  `tests/contract/test_mail_session_dovecot.py` against Dovecot. `application` also holds `commands` (`MailCommands`, the one path a change to the mail on a server takes), `server_ops` (`ServerOps`, what a try asks of the server, and the reversals), `sync` (`Ingest`, mail into the database), `syncing` (`Syncing`, each account's sync and the body backfill), `reconcile` (`Reconciler`, what the server no longer holds), `folders` (`Folders`, the rail and a folder's sync as it opens), `queries` (`Queries`, what the page lists), `bodies` (`Bodies`, a message opened), `filing` (`Filing`, a person's filing, and the tray), `reversal` (`Reversals`, what reverses a move), `stats` (`LiveStats`), `pool`, `warm` and `realtime` (IDLE's workers), and `domain.events` the events they publish. `mailmind.bootstrap.mailbox` binds them, and it's how the routers and the tests reach them: `commands`, `server_ops`, `ingest`, `syncing`, `reconciler`, `folders`, `queries`, `bodies`, `filing`, `undo`, `stats`, `pool`, `warm` and `realtime`. A snooze, a mute and a block are reversed by their own contexts (`Snoozing.reverse`, `Muting.reverse`, `Cleanup.reverse_block`), and `Undo`, in `platform`, is handed each kind's reverser there. The rest of the context is standard
  library only.
- `mailmind.discovery` is the fourth: finding mail by what a person types. `domain.query.QuerySpec` is the search
  grammar parsed, by a parser no input can make raise. `narrows()` is what a bulk command must pass: a folder or an
  account alone spans nearly everything, and a negation never narrows by itself. `domain` also holds `guard` (a filter
  resolving to 60% of all mail is a sweep of the whole mailbox), `views`, `documents` and `known` (who the stored mail
  vouches for, given the facts). `adapters` holds `fts` (`to_sql` turns a parsed query into a MATCH and bound SQL, and
  `index_message` and `forget_message` keep the index up to date), `sqlite` (`StoredMail`, which reads `messages` and the
  rest as read models and writes nothing) and `views_file`. `application` holds `ports.py` and the use cases `Search`,
  `SavedViews`, `DocumentHub`, `Contacts` and `KnownContacts`. `mailmind.bootstrap.discovery` binds them, and it's how
  the routers and the command line reach Discovery (`discovery.search.answer(qs)`, `discovery.known.ask(addrs)`).
  A folder's role goes through the Mailbox (`mailbox.folders.role`), and the account list through Accounts.
  It is standard library only.
- `mailmind.curation` is the fifth: what stays in your inbox. `domain` holds `classifier`, `rulebook` (one domain
  matcher for the classifier, the Screener and expiry), `expiry`, `live`, `noise` and `unsubscribe`; `adapters` holds `rules_file`,
  `safe_http` (the one-click POST, the one request MailMind makes to an address a message named, vetted as hostile),
  `sqlite` (reads `messages` as a read model, owns `unsub_log` and `sender_unsub`) and `audit_file`; `application` holds
  `ports.py` and the use cases `Cleanup`, `Unsubscribing`, `Screener`, `Sweeps` and `Expiry`. `mailmind.bootstrap.curation`
  binds them, and it's how the routers and the command line reach Curation (`curation.cleanup.execute(body)`,
  `curation.sweeps.sweep(acct)`). Filing goes through the Mailbox (`mailbox.filing`), and a message's rows through `store`. The CSRF token still comes
  from the handler (`H.CSRF`). A sweep's
  `move_verified` is `mailbox.application.bulk_moves`. It is standard library only.
- `mailmind.attention` is what asks for you later: snooze, mute, reply later and follow-ups. `domain` holds `snooze` (a
  wake time from a preset or a date) and `followups` (how long a thread may wait). `adapters.sqlite` holds the SQL of
  `snoozes`, `muted` and `followups`. `application` holds `ports.py` and the use cases
  `Snoozing`, `Muting`, `FollowUps` and `Clocked`, the every-minute job.
  `mailmind.bootstrap.attention` binds them, and it's how the routers reach Attention (`attention.muting.mute(body)`,
  `attention.book.list_waiting(c, account)`). A message's row and `queue_flags` go through `store`, and undo and
  what a change makes stale through the Mailbox (`mailbox.undo`, `mailbox.stats`).
  `mailmind.kernel.attention` is a different module, what a verdict asks of you, which the modules that need both import as `verdict`.
  It is standard library only.
- `mailmind.calendar` is the sixth: invites, recurring series, Today & next and RSVP replies. `domain` holds `invite` (the shape the reader gives), `agenda`
  (which event a message speaks for, and what a cancellation or a moved occurrence does to its series) and `rsvp` (an iMIP
  REPLY and its idempotency key); `adapters` holds `ics_reader` (the hand-rolled reader of an invite's text, `parse` and
  `next_occurrence`, what the proposal's `IcsReader` port names) and `sqlite` (reads stored bodies and the outbox's keys, and
  writes nothing); `application` holds `ports.py` and the use cases `Agenda`, `Invites` and `Rsvp`. `mailmind.bootstrap.calendar`
  binds them, and it's how the routers reach Calendar (`calendar.agenda.upcoming()`, `calendar.rsvp.respond(body)`). An
  RSVP goes through Compose's outbox, and a message through `store` and `ingest` until the Mailbox holds it.
  It is standard library only.
- `mailmind.compose` is the seventh: drafts, building a message, the outbox and delivery. `domain` holds `builder` (`build_message`, which nothing
  else builds with, and `reference_chain` and `thread_ids`), `reply`, `forward`, `resume` (a saved draft split back into your text, its quote and its
  files), `attachments`, `limits`, `outbox_item`, `events` and `errors`. `adapters` holds `smtp` (`SmtpTransport`, the only module that speaks SMTP)
  and `sqlite` (`outbox` and `drafts`). `application` holds `ports.py` and the use cases `Composing` (a request made into a message), `Sending`, `Drafts`
  and `Outbox`, the queue and its worker. `mailmind.bootstrap.compose` binds them, and it's how the routers, the other contexts and the tests reach
  Compose (`compose.sending.send(body)`, `compose.drafts.save(body)`, `compose.outbox.drain()`, `compose.book` for its tables). `mailmind.compose.api` names
  what others use without its wiring (`build_message`, the events). The copies on the server go through the Mailbox's `application.copies.Copies`
  (`mailbox.copies`). A message's row through `store`, and what's kept about folders through `mailbox.folders`. It is standard library only.
- Three checks hold the shape. `tests/unit/test_architecture.py` holds every use case to no SQL, which goes through a port to an adapter, and each table to its owner. import-linter's contracts in `pyproject.toml` run in `just lint`:
  bootstrap, then http, then the contexts, then platform, then content, then kernel; within each context,
  adapters over application over domain; no adapter imported outside bootstrap; and the
  `anthropic` and `imapclient` packages each reached only through its adapter. `tests/legacy_ratchet.txt` lists
  every import of a root module from inside the package, and it is now empty; `tests/unit/test_ratchet.py` fails when the
  code and the list differ.

### Routing

`mailmind.http.handler.H` is one `BaseHTTPRequestHandler` subclass, which both doors serve: the old one on the standard
library's server, the new one through `HandlerBridge`. `do_GET` and `do_POST` run the perimeter and the catches every route
shares, then ask `mailmind.http.routing.Routes` for the route. A route names its path as a **literal string**
(`@r.post("/api/snooze")`), in `mailmind/http/routers/<context>.py` or `http/static.py`, and takes a `Request`. It returns
an object, `(object, code)`, or `ANSWERED` when it wrote the answer itself. A router gets its context's wiring as an
argument (`routes(attention)`), and `mailmind.bootstrap.web` binds them all, since http may not import bootstrap. The
routers run on the old door too, so they are plain tables, standard library only, and hold no schemas yet.

Consequence: to find a route, grep the literal path. A path that matches by shape (`@r.matching`, a message's body or
attachment, `/api/accounts/<action>`) is asked only when no literal path answers.

### Schema migrations

The database's code is `mailmind.platform.db`; `store.conn()` and `store.init()` call into it.

- `database.connect` opens every connection: WAL, `synchronous=FULL` with `fullfsync` (a commit
  survives a power cut, because `delivered` and `applied` are exactly-once gates),
  `busy_timeout` 60 s, `foreign_keys=ON`, which the cascades depend on, and `temp_store=MEMORY`, so what a
  big sort holds of the mail never lands in a temp file.
- No transaction stays open across a network call. `database.connect` tracks each connection for
  the thread that opened it, and `txguard.refuse_network(what)` raises `TxAcrossNetworkError`
  while one has a transaction open, since a write held while a server answers keeps every other
  writer waiting. An IMAP session and each command (`mailbox.open_session`), an SMTP session and
  all it sends (`SmtpTransport.connect`), a Claude API request and a one-click unsubscribe check
  it, in tests and in use. Commit before talking to a server; a new network path calls
  `refuse_network` first, or wraps the connection's method with `txguard.guard_calls`.
- A use case never runs a connection by hand (`tests/unit/test_architecture.py` holds it to that). It gets a
  `UnitOfWork` as its first argument and says what each step is, one connection a block: `with self._uow.read() as c:`, or
  `with self._uow.write() as c:`, which takes `BEGIN IMMEDIATE` up front, commits as the block ends and rolls back if it
  raises. The order is read and let go, then the network, then a short write. A loop that commits midway is a block for each
  commit. A block that writes only sometimes reads first and opens a write only if there is something to do, unless what it
  read decides what it writes, where the read belongs in the write. `read()` raises `WroteInARead` if its block left a
  transaction open, since closing would lose it. `write()` raises `NestedWrite` inside a transaction on the same thread,
  which would otherwise wait out `busy_timeout` on itself. Hand-run connections remain outside use cases: `store.conn()`
  for the command line, the bootstrap and the tests, `Undo`, and `DurableQueue`, which takes `uow.connect`.
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
  as retired rather than as rows lost, and `tests/unit/platform/test_db_upgrade.py` holds it to that.
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
- `tests/unit/platform/test_db_upgrade.py` upgrades a database seeded with data from every version. A
  migration that derives a column on purpose adds it to `DERIVED` there.

### Concurrency

Every job that talks to an account's IMAP server runs through
`gates.run(account, fn, priority=..., wait=...)` (`mailmind.platform.workers.gates`): the
requests that open and file mail, undo, reconcile, expiry, the body backfill, the outbox, the
command path and the thread summarizer. `MAILMIND_WORKERS` picks the gate:

- Unset (or `global`), one process-wide lock is every account's gate, so all live-IMAP work is
  serialized and a black-holed account holds up the rest until its connection times out.
- `account`, each account has a gate of its own. Waiting jobs go by priority, then in turn. An
  `EVERY_ACCOUNT` job waits until no gate is held and holds them all, and a job that would come
  after it waits behind it. The whole suite passes under either; the owner soaks `account`
  before it becomes the default and the flag goes.

A job runs on its caller's thread either way, so context variables (`assistant.allowing_over()`)
and txguard's per-thread record stay with it. The connection pool's per-account lock is taken
*inside* the gate. The 300 s sync and IDLE's syncs don't take it yet. `mailbox.syncing.sync_guarded` is
their single-flight: a sync asked for while one runs is recorded, and the running one goes round
again for it as it ends.

- A job is a function, not a block: it returns what its caller needs instead of setting the
  caller's variables.
- Each names its account (a message's through `server_ops.account_of`), or `gates.EVERY_ACCOUNT`
  for work across accounts, and a `gates.Priority`: INTERACTIVE, AFTER_USER, SYNC or
  MAINTENANCE. A run over every account is not work across accounts: `/api/sweep` goes through
  `Sweeps._each_account`, one job per account on its own gate, in turn. Reconcile
  (`mailbox.reconciler._reconcile_folders`) goes further: one job per folder with a `sync_state` row
  (`reconciler.reconciled_folders`), so it never holds a gate across folders.
- Background work passes `wait=False` and skips its turn on `gates.Busy`, so it never stalls
  the user. `deadline=` (seconds) raises `Busy` for a job not let in by then, and it never runs.
  A job that waits on a gate from inside a job raises `RuntimeError` instead of hanging; one that
  takes another account's gate with `wait=False` may.
- Tests ask `gates.held(account)`, and hold the gate with `held_elsewhere()` from
  `tests/gate.py`, which runs a job like any caller. `tests/e2e/test_chaos.py` holds what one
  account's trouble may do to the rest; `FakeIMAPServer.stall` black-holes a command.

`mailbox.pool` (persistent per-account IMAP sessions) and `mailbox.warm` (multi-folder sync)
are both **default-off** behind `MAILMIND_POOL` / `MAILMIND_WARM_FOLDERS`. Code must
work with them unset. `Pool` is a `KeptConnections`, which does the keeping. `Pool` says which accounts, that a
NOOP checks and a LOGOUT closes, and that the provider gives the numbers (`recycle_seconds`, `recycle_uses`).

IDLE's workers follow the configuration. `Realtime` is an `IdleSupervisor`, which runs them, and says what a
worker does: which accounts, how each is reached, the IDLE loop and what a change does.
`realtime.reconcile`, which the account events call,
retires the worker of an account removed, disabled or now reached another way (kind, host, port,
TLS, credential), and starts one for each account without one, up to `MAX_IDLE_ACCOUNTS`. An alias
or signature change leaves the worker be. A worker also checks its account after each IDLE cycle,
so an edit of the file, which publishes nothing, still reaches it. The command path gives up a queued
move whose account is gone, as the outbox does a send.

`realtime.stop()` and a retirement only interrupt a worker's session (`MailSession.interrupt`, a
socket shutdown); the worker closes its own connection. Closing a socket another thread may be
about to wait on frees its descriptor number for the next socket opened.

`mailmind.platform.profile` is the only reader of `MAILMIND_PROFILE` (`full`, the default, or
`read-only`) and of the older `MAILMIND_NOSYNC`, which still selects read-only: any value but
empty, `0`, `false`, `no` or `off` turns it on, so a typo fails toward read-only, and read-only
wins when the two disagree. A front door refuses to start on a profile name it doesn't know.
`background.nosync()` asks the profile.

### Writes, undo, and offline

- A change a person asks of the mail takes one path, online or offline: `mailbox.commands` (`MailCommands`), over
  `mail_commands` (migration 39). **Accept**, in one transaction: the command's row, and for a move the message's row marked
  `pending_removal` with its id and the undo entry, which names it. Every list, search, the conversation view, the
  brief, the delta and what drafting ahead picks from leave a marked row out (`AND m.pending_removal IS NULL`); a new list query does too. What the AI
  reads of a whole thread doesn't, since its fingerprint would change twice over one move and pay for a summary each time. A
  flag change shows on the local row instead, and `params` keeps the flags it replaced (`was`). **Submit**: a try as a job
  on the account's gate, which a click waits for only `FILE_WAIT` seconds. Past that the worker has it, and the answer's
  `status` is `waiting`. **Settle**, under the same gate and in one transaction: `applied`, where the message went and what
  the server did on the way (`outcome`), and the local rows (`_local_mirror`).
- A connection failure raises a member of `commands.UNREACHABLE`: the worker has the command, and the answer's `status` is
  `queued`. A refusal in a click (`ServerAnswer.won_t`: `refused` or `no_folder`) leaves nothing behind: whoever clicked
  hears of it, the message is as it was, and the undo entry and the row go. On the worker nobody is waiting, so a
  refusal, a give-up or a removed account puts the row in `error`, for the tray, and gives the message back
  (`_give_back`). Undo of a move not yet applied gives it back too.
- `begun` says a try may have reached the server. A first try acts on the UID seen when the command was accepted, if the
  folder's UIDVALIDITY (`params.epoch`) still holds, and searches for nothing. A later try asks the server first
  (`ServerOps._target`, `_already_in`): the Message-ID says where the message is, a folder with none by that id means it
  has left, and a UID from another epoch is never acted on. A send's `\Answered` (`server_ops.mark_answered`) goes
  straight to the server, since the outbox retries it.
- `mailbox.commands` runs the worker when IMAP returns, using the `applied` flag as an
  exactly-once gate. It and Compose's `Outbox` each run a `DurableQueue`, `QUEUE`, over their table:
  one claim wins a row, a failure backs off from 20 s doubling to 15 minutes, and the last allowed attempt goes to `error`,
  where a person's retry (`requeue`) starts the count again: the outbox's fifth, a command's hundred-and-first, about a
  day of tries. `/api/pending` lists the command log's unapplied rows for the panel the outbox button opens; its Retry
  (`commands.retry`) marks the message or shows the flags again, and its Discard (`commands.discard`, on the account's gate)
  gives them back and takes the entry off the undo stack (`undo.forget`). The `forget` job removes rows finished a
  month ago (`store.command_forget_finished`). The worker wakes
  when kicked and when the next queued row comes due (`until_due`), at least every 30 s. The
  outbox publishes `Failed` for an attempt that failed, as it does `Sent`. A send the
  server refuses for good (`SmtpTransport.permanent`: a 5xx, or every recipient refused with one) goes
  to `error` on its first try (`give_up`). The gate column (`applied`, `delivered`) is set the instant the one
  irreversible step returns, so the boot-time reset of running rows never repeats it, and for a command
  before the IMAP gate opens, so an Undo or Discard waiting on it finds it set. A row's
  state changes go through `QUEUE`, not the store. The move is itself an IMAP op, so it runs
  as a job on the account's IMAP gate; the outbox's SMTP send holds none.
- The outbox's queue has a lane for each account (`DurableQueue(lane="account")`): its drain
  runs each account's rows in turn on a thread of their own. The worker's drain doesn't wait
  for them; any other caller's (a test's `outbox.drain()`) waits for every lane it ran.
- `SmtpTransport.send` tells apart the two sides of a message's last bytes. `before_dot` sets `outbox.wire_begun`
  (migration 32) just before they go, and with no answer after them it raises `Unanswered`: the row goes
  to `error` as `outbox_item.UNSURE`, listed `unsure`, and only a person's retry sends it again. Any answer clears the
  mark, and so does a failure before them. `DurableQueue(unsure=...)` makes `reset_stuck` do the same for a
  row a crash left with the mark set.
- `outbox.wait_kind` (migration 41) says what a queued send waits for: `grace` (Undo Send's window), `scheduled` (a time
  someone picked) or `backoff` (a try failed). The list says `scheduled` or `retrying` from it, never from how far off the
  row is due, and a retrying row offers Try now and Cancel.
- What a delivered send still owes is each its own flag: its Sent copy, `\\Answered`, its draft's copy. One that fails
  (a connection lost, not a refusal, which would answer the same again) puts the row back in the queue, its tries counted
  from the delivery, and the next pass does only what's owed, until it is done or goes to `error`. The retry looks for the
  copy by Message-ID first (`Copies.append_to_sent`). `outbox.list_recent` shows such a row as `sent`, or as `error` once a
  Sent copy gives up (what else gives up asks nothing of anyone, so that row stays `sent`), and `cancel_queued` never
  deletes a row past its gate.
- On an account whose server files sent mail itself, a person's Retry of an `unsure` send looks in Sent first
  (`Outbox._in_sent_already`, on the gate for `LOOK_IN_SENT_WAIT`). A copy there marks it delivered, only its bookkeeping
  is left, and the answer says `gone: true`. A look that can't tell sends it again, as they were told it might.
- A message MailMind sends for itself goes through the outbox too: a mailto unsubscribe (`SystemSend`), keyed
  `unsub:<sender>:<account>`, waits out a server that's away and is filed in Sent.
- The read-only profile writes nothing to a mail server or anywhere else outside (decision D4).
  A session opens every mailbox with EXAMINE and raises `profile.ReadOnlyError` on any write,
  before it is sent: `ReadOnlySession` over IMAPClient, and over imaplib `accounts.ReadOnlyIMAP`,
  which refuses any command not known to only read. `SmtpTransport.connect` and `safe_http.one_click_post` refuse too. `ReadOnlyError` is a
  member of `commands.UNREACHABLE`, so a move or a flag change queues as it does offline, and
  both queues' drains wait for a full run. What nothing queues (a sweep) answers 409 through
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
  first open scans the folder whole, as a job of its own (`mailbox.folders._folder_sync`, one per folder at
  a time) that the list request waits for up to `FIRST_SYNC_WAIT` seconds. Past that it answers
  with what is stored and `syncing: true`; a scan that failed answers `sync_failed: true`.
  Opening a folder already read, other than INBOX, catches it up the same way, an incremental
  sync waited for up to `CATCH_UP_WAIT` seconds (`catching_up: true` past that), at most once
  every `CATCH_UP_EVERY` seconds unless `mailbox.folders.touched` says MailMind put mail in it.
  Each sync ends by publishing `folder-synced` with how many it stored.
- What a folder's sync asks the server for is `domain.sync_plan`, a pure value from what the select said and what is
  stored: `plan_folder` the mail to fetch, `plan_flags` how to read flags. `Ingest` does the asking and the storing.
- An incremental sync reads flags again (`ingest._refresh_flags`). On a CONDSTORE server it compares
  the select's HIGHESTMODSEQ with the mark in `sync_state.highest_modseq` (migration 31): nothing if they
  match, else what `uids_changed_since` names, however old. A server without it (Proton Bridge), or one
  refusing the search, has its `FLAG_REFRESH` newest stored UIDs read instead. It reads the stored rows
  before the flags and writes each change only where the row still holds the flags it read (a click may
  land meanwhile, since the sync takes no IMAP gate), skipping rows with a flag change still queued or a
  move pending. The mark stays behind those two and a refused search, so what was skipped is read again.
  A sync that changed any publishes `FlagsChanged`, and the page reloads an open list on
  `flags-changed`.
- The body backfill (`syncing.backfill_round`) goes by `domain.body_backfill`. Each account takes its turn at a round's
  dozen, so no account's backlog, outage or busy gate holds another's mail back. A busy gate ends only that account's
  turn. A lost connection sets its message aside, a minute doubling up to six hours, and rests only its account for a
  minute. A body the server didn't give waits six hours. That is kept in memory, so a restart tries everything again.
- Undo is a LIFO stack in the database (`undo_entries`, migration 25; `mailmind.platform.undo`),
  so it outlives a restart and is the same through either door (decision D3). It holds 50 groups
  (`Undo.UNDO_MAX`, an action alone counts as one) or 24 hours of them (`undo.KEEP_FOR`), whichever comes first, and an entry leaves it, committed, before
  any server is asked to reverse it. It knows nothing of what an entry means: `mailmind.bootstrap.mailbox` hands it a reverser for each kind, the Mailbox's in `application.reversal` (`Reversals`). Bulk actions push one grouped entry with children. `mailbox.undo.undo_last` tries every child, newest first; the ones it
  couldn't reverse (a refusal, not found, a raise) go back on the stack as a smaller group, and
  the answer counts `reversed` and `failed`. A reversal reports a server NO as `(False, why)`
  (`server_ops.move_by_msgid`, `relabel_by_msgid`), never as done. A group id must be unique for the database's
  life: the frontend's `undoGroup()` is a UUID, since the stack outlives a page load and a restart.
- A bulk command's confirm carries out a plan (`mailmind.assistant.application.plans`,
  `/api/ai/do-plan` → `/api/ai/do-confirm`). The preview freezes what it matched, by message key and folder, and
  the page sends back only the plan's id, so a renumber or mail that came later changes nothing. A plan is
  good for one confirm within ten minutes, and a restart ends it (410). A copy no longer in the folder it was seen
  in is left where it went, and counted as `gone`. `Filing.carry_out` sends each message through its own route (`filing.do_write`, `do_trash`,
  `attention.snoozing.snooze`), under one undo group. A filter reaches Sent, Drafts, Trash and Junk only by naming a folder, a verb
  never reaches its own home (`action_plan.out_of_reach`), and each message counts once, by the copy search would show.
- Whole-conversation archive/trash (`/api/thread/archive|trash` → `filing.do_thread_file`) resolves
  the members server-side — the anchor's `thread_id` within the anchor's folder, or the anchor alone
  with no thread id — and accepts them all in one transaction (`commands.accept_move_within`, which a
  caller holding a write uses), under one undo group, so a crash can't leave half of them marked. Each
  is then tried on its own once that commits: a refusal gives back only its own message, and a try
  that raises counts as queued, with the rest still tried.
- Marked rows go as one request (`/api/bulk/file` → `filing.file_many`, up to `BULK_MAX`; the page sends more as more requests
  under one undo group): one transaction accepts every row, a `whole` one as its conversation, and then each is tried on its
  own. A click on many waits about as long as on one, there and for a conversation: past `FILE_WAIT` the rest go to the worker
  (`commands.leave_to_worker`), and every row has left the lists by then.
- Every account's Inbox is one list and one query (`/api/inboxes`, `queries.all_inboxes`), read from what is stored: an account
  on its first scan starts one and holds up no one (`syncing: true`). So is who you wait on (`/api/waiting-on-others`). The page
  still asks each account for both, a page of each; the React app doesn't.
- Send-time checks: `composeSend` runs `guardCheck` synchronously, before `composeClose(true)`,
  so nothing is awaited before the composer closes and a double submit still can't happen.
  `/api/contacts/known` (the "new" marks) is a read-only POST: pure SQL, no IMAP gate.
  `Sending.send` refuses a `send_after` that has passed instead of sending now.
- A draft is local first (decision D5, migration 40). `Drafts.save` writes a save to `draft_journal`, one row a draft
  (the newest replaces the last), and that alone makes it saved, whatever the server is doing. The row is what the draft
  still asks of its server: a save's bytes and record, or `discard`. An autosave (`autosave: true`) comes due a minute after
  the last push (`PUSH_EVERY`). Any other save tries at once, on the account's gate for `PUSH_WAIT`, and answers
  `pushed: true`, or else `pushed: false` and the `draft-push` job (`Drafts.push_round`) has it, backing off from 20 s to 15
  minutes for as long as it takes. A push that lands deletes the row only if no newer save has replaced it. Read-only the job
  doesn't run, so saves and discards wait for a full run.
- A try sets `tried` to its Message-ID before the APPEND. The next try looks for that copy in Drafts: it stands if it is
  this very save, and goes if a newer one has taken over. A discard takes it too, and the row leaves only after the copy
  has (a server that refuses has the last word: the row goes, and the copy stays listed).
- A save names the `version` it saw (`base_version`). One that never saw the latest gets a 409 with `conflict: true`, unless
  the last save was this composer's (`saved_by`), whose answer was lost. Autosave then stops, a closing save fails into
  `state.failedSend`, and only a save someone asks for leaves `base_version` out and overwrites.
- A send about to go holds its draft: the job leaves it, a save answers 423, and delivery removes the copy and the row, the
  journal with it. One scheduled more than a minute off lets the copy catch up.
- Reopening prefers the journal when the copy opened is the one pushed. Where another app changed it, `Drafts._keep_apart`
  gives the waiting save a row of its own, so both stay. `/api/outbox` also answers `drafts` (`Drafts.waiting`), what should
  have reached its server and hasn't, for the tray, and `/api/draft/resume` opens one by `draft_id`.
- Reopening a draft (`/api/draft/resume` → `Drafts.resume`) reads the draft's own bytes, never
  the original. At each push `Drafts._push` records beside the `drafts` row what the composer held
  (migration 20: `own_text`, `tail_text`, `tail_html`, `bcc`, `raw_sha`). That record is
  trusted only for the very copy it describes, the same UID or the same sha256. The composer
  sends `resume` (threading and the quote's text) on every save and send. `Composing.spec`
  then builds `own + tail`, with HTML only where the journal or the row vouches for that exact tail (`book.draft_quote`). The
  original is looked up by `(account, Message-ID)` for `\Answered` only. `Drafts._live`, the
  "is this copy still there" probe, is a stateless 10-second connection outside the IMAP gate.
  Under the gate `Drafts.resume` re-reads the `drafts` row and answers 409 if it is gone or moved,
  so `Drafts._push` and the outbox's draft cleanup also move that row's copy only under the gate. Edit
  answers 423 while a queued or sending outbox row holds the draft. A save appends the new copy
  first and only then removes the old one (`Copies.append_to_drafts`), wherever it was, so one that
  fails or a crash leaves the old copy, at worst beside the new.
- A message is a draft if the server flags it `\Draft` or its folder is the account's Drafts
  (`Drafts.is_draft`): the role the last LIST gave it, else `roles.names_drafts`, the whole name
  at the top of the tree or under INBOX or Gmail's parent. A name that merely holds "draft" counts
  for nothing, and a removal has no Undo, so Discard, a Send and a replacing save remove a copy only
  if MailMind saved it, the server flags it or its folder is Drafts (`Outbox.draft_copy_is_ours`).
  Older `drafts` rows may name received mail: those are forgotten and the mail stays.
- A reply or forward names its original by pk until its first save, whose answer carries `resume`
  (threading, the quote) and a forward's `files`; the composer then works from those, as a reopened
  draft does, and needs no row. The outbox row keeps the original's account, Message-ID and thread
  (`orig_*`, migration 27), so `\Answered` (`server_ops.mark_answered`) and the follow-up land after an
  archive deleted the row.
- A copy that leaves the server takes its `messages` row with it, in the step that records the save,
  discard or send, and `reconcile` names the folder so a list of it on screen loads again (its
  catch-up brings the new copy). A copy the server wouldn't remove stays listed.
- A composer's saves go in turn. `composeSaveDraft` chains on `cmp._saving` and names the copy the
  last save left, autosave skips a tick while one is in flight, and Send and Discard wait for it.
  It clears `dirty` only if `cmp.edits` hasn't moved since it began, so call `markEdited(cmp)` at
  every edit, never `cmp.dirty=true`. A closing save that fails goes to `state.failedSend`, with the
  float's Edit. On the server a first
  save finds an earlier one's row by the composer's key (`drafts.idempotency_key`, migration 26),
  as `Sending.send` does when it has no `draft_id`.
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
  `outbox.in_reply_to_pk`, `drafts.in_reply_to_pk`, `draft_ahead.src_pk` and `followups.anchor_pk` are still written, for a
  rollback, and go unread: a send goes by `orig_*`, a draft by its own In-Reply-To, the other two by thread or Message-ID.
  `tests/unit/test_architecture.py` holds that, and names the one read left (`outbox_item.pk_of_original`): a send with no
  Message-ID to go by, queued before migration 27 or replying to a message that has none.
- **A message's key outlives its rows.** `store.mint_key` gives one key per account and Message-ID
  (`mailmind.mailbox.domain.identity`; a message with none goes by its envelope), and
  `messages.message_key` names it on each copy (migration 30). `store.save_summary` writes what the AI
  said under the key (`assessments`), and every query joins that on `message_key`, so a move, an Undo, a
  renumber or Gmail's All Mail copy pays for no second call. `needs_summary` offers one copy of each, and no
  row without a key. `summaries`, a row for each copy, stays written (`save_summary`, `upsert_message`) only
  for a rollback to an older commit, until it is dropped: read it nowhere. A key with no copy left waits 30
  days (`gone_at`) for one to come back, then the `forget` job ends it. `store.init` (`store.repair_keys`)
  gives a key to a row an older commit wrote, and brings what such a run wrote only to `summaries` or
  `messages.snoozed_until` across, the one read of those two outside the migrations
  (`tests/unit/test_architecture.py` holds that). Message rows in the API carry the key as `key`, beside
  `pk`. New durable state about a message keys on it.
- **A snooze outlives its row.** `attention.book.set_snooze` writes a `snoozes` row (migration 33, by key and
  folder), and every query asks that (`kernel.snoozed`: `not_snoozed_sql`, `snoozed_sql`), so a resync keeps it,
  each copy in that folder is hidden and a copy filed elsewhere isn't. `messages.snoozed_until` stays written only
  for a rollback, until it is dropped: read it nowhere, `row["snoozed_until"]` on a `SELECT *` included. A row read
  alone says `snooze_until` (`store.get_message`), and the Snoozed view's row has the old name as an alias.
  An Undo of filing brings it back; one come due with
  no copy in its folder ends at `attention.book.due_snoozes`. The undo entry names rows by key and folder too (`where`).
  A message that wakes is unread on the server as well: `Snoozing.wake_due` queues the mark for read mail
  (`store.queue_flags`), or the next sync that read its flags would take it back.
- **Muting tells the server too** (decision D6). `Muting.mute` queues `\\Seen` for the thread's
  unread members and `Muting.on_ingest` for mail that arrives later, skipping one with a mark still open
  (a refused one waits in the tray). The undo entry names those members by key and folder, and Undo marks
  unread again only them.
- **A thread id is sticky.** A sync hands `store.thread_stored` what it stored, and any of the account's mail with no
  id yet goes along (a sync that died, `mailmind sync`). `threading.Threader` (`mailbox/domain`) mints an `rf:` id from the
  union-find root of what arrived for the thread (the smallest Message-ID it names) once, and keeps it as the thread grows;
  a Gmail thread id gives `gm:` instead. `thread_links` (migration 42) holds each id a threaded copy names, and goes with
  the copy, so a sync reads what its mail names and the threads that touches, and a reply writes only its own row. An id
  goes only when a merge absorbs it (two threads joined, or one that gained a Gmail id). The survivor is a `gm:` id over an
  `rf:` one, the smallest of several `rf:` ones, and of two Gmail threads joined by headers the one that comes later by
  folder, date and row. Every row holding the absorbed id takes the survivor, and `ConversationsMerged` goes to whoever
  keeps something under it, on the merge's connection, so a failure rolls the merge back: `muted`, `followups`,
  `thread_summaries`, `draft_ahead`'s `t:` keys and `outbox.orig_thread_id`. It isn't on the bus, whose handlers run after
  the commit. A new table keyed on a thread id gets a `rekey_thread` in its owner's adapter, called from
  `_conversations_merged` in `mailmind.bootstrap.mailbox`. A split doesn't split: mail left unconnected by a copy going, or
  by one stored again naming something else, keeps its one id. `store.recompute_threads` regroups a whole account and is
  the repair (`python3 -m mailmind db rethread`), which parts what no longer reaches a Gmail copy; no sync takes it.

### Security model

Every request passes `mailmind.platform.perimeter` before any route, through either door:
`H.do_GET` and `H.do_POST` call it, and so does the new door's `Perimeter` middleware.
It is standard library only. Callers ask the package by these names (`perimeter.refuse_host`); inside it
`host`, `origin`, `fetch_metadata`, `csrf` and `limits` hold the checks, `headers` and `csp` what an answer
carries, `errors` the `Refusal`, and `tokens` a confirm token, which a use case imports for itself.

- `refuse_host` — the Host must be this server's (127.0.0.1 or localhost on its port), and an
  Origin, when sent, its own. Blocks DNS rebinding. Every request, `/` included.
- `refuse_cross_site` — under `/api/` only `Sec-Fetch-Site: same-origin` passes, so another
  site's no-cors `<img>`, a typed URL and a client that says nothing are all refused. `/` also
  opens from a typed URL or another site's top-level link.
- `refuse_body` — a Content-Length over the route's cap (`handler.post_body_cap`:
  `MAX_SEND_POST` for send and draft, `MAX_POST` otherwise), judged before the body is read.
- `refuse_mutation` — every POST: the two checks above, then the `X-CSRF-Token` header
  compared with `secrets.compare_digest`, then an Origin, which must be this server's.

The new door puts every method but GET and HEAD through the POST checks, and refuses before it
reads a body; the old one reads the body first.

Every answer, a refusal included, carries `perimeter.answer_headers`: nosniff,
`Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, COOP and CORP `same-origin`, a
Permissions-Policy, and under `/api/` `Cache-Control: no-store`. `H.end_headers` adds each one
an answer didn't set itself, and so does the middleware. The page also carries
`Content-Security-Policy` from `perimeter.page_policy`: script files from this server and nothing else, connections to this server
only, framed by no one. The React app at `/next` carries `perimeter.next_page_policy`, the same with nothing in it for mail: no
`https:` images and no `data:` fonts. The browser tests fail on any violation.

A failed GET or POST answers a fixed message and logs its path and the error's class. Every TLS connection, IMAP
and SMTP alike, STARTTLS or implicit, takes `accounts.tls_context(host)`: verified everywhere but
loopback, where Proton Bridge's certificate is self-signed. There `accounts.check_pin` stands in,
after the handshake and before LOGIN or AUTH: the first certificate an account meets at a loopback
`host:port` is pinned (`config.tls_pin`, the account's `tls_pins`, `sha256:` of its DER bytes), and
any other after it raises `TLSPinMismatch` (a `ConnectionError`, so a move queues) with nothing
sent. Health calls it `TLS_PIN`; `/api/accounts/trust-cert` forgets the account's pins and syncs it.
An unsaved draft pins nothing. A new TLS connection calls `check_pin` too.

A credential goes only to its own account's server. `/api/accounts/test` logs a saved account in
at its saved endpoint, and tries a draft (an account not yet saved) only with a password typed
into it, at the host typed with it (`domain.draft.throwaway_account`). A draft naming a stored
secret (`op`, `env`, the Bridge password) is refused, since the request names the host.

Folder names reach IMAP only through `accounts.mailbox_arg`. It wraps a name in the form LIST
gave it and refuses one that isn't a valid quoted-string body (CR, LF, NUL, a bare quote, a
trailing backslash) with `accounts.BadMailbox`, an `IMAP4.error`. Never build `f'"{folder}"'`.
Any other quoted value (a Message-ID, an expiry token) goes through `accounts.imap_quote`, which
refuses CR, LF and NUL with `BadValue`, an `IMAP4.error` too: a sender can fold the Message-ID
header so the ID stored holds a line break.

`CSRF` is a per-process token. `web/index.html` ships a `__CSRF_TOKEN__` placeholder in a
`<meta>` tag that `webroot.page` substitutes at serve time; the frontend's `api()` helper
attaches it to every request, GETs included.

Mail bodies render in a sandboxed iframe with a deny-all CSP. `mailmind.content.sanitize` is
defense in depth — **the iframe sandbox is the actual boundary**, so a sanitizer miss
alone cannot execute script. Preserve that ordering when touching either.

The page's frame is a srcdoc, which inherits the page's policy. The React app's loads `GET /api/message/<pk>/body.html?key=<key>&images=0|1`
(`Bodies.as_document`), the same sanitized HTML as `/body` or the plain text escaped, under `perimeter.mail_frame_headers`: a policy
of its own, with a `sandbox` that holds even if the frame's attribute goes (a `<meta>` can't do that), and the one answer this origin
may frame. The pk names the copy, since one key spans a message's copies, and a sender who reuses a Message-ID shares another's. The
key must be that row's, so a renumbered pk never shows another message under the bar the page drew. A failure answers as a
document too, or the frame would show a browser's error page. The document holds no style: `MailFrame` gives it its look
(`mailDoc.lookOf`) as soon as it is parsed, folds a plain mail's quote and takes its link clicks through `contentDocument`, so a
reading setting fetches nothing. Load images is a new frame on `images=1`, since moving a loaded frame would add a history entry.

### AI calls

Everything routes through `assistant.calls.call_claude_json(prompt, schema)` (or
`call_claude` for plain text), which call `mailmind.bootstrap.assistant.gateway()`. The
Claude API is the only engine (decision D8). The `Gateway` builds each request from
`mailmind.assistant.domain`: effort where the model takes one (`models.takes_effort`), the
refusal fallback where it takes that (`models.takes_fallback`), the schema in structured
output's form (`schema.structured`) and the system prompt. It sends it through a `LanguageModel` and gets back a
`Reply`: the text, the model that served it, its `Usage` and its stop reason. A call with no
reply raises `ModelError` (a `RuntimeError` with `status` and `permanent`).

The new front door talks through the Anthropic SDK (`front.build` calls
`assistant.use("sdk")`); `python3 server.py`, the scripts and the root suites through
`UrllibModel`, since the old door runs with nothing installed. Only
`mailmind/assistant/adapters/anthropic_sdk.py` imports `anthropic`, and only the new door loads
it. The two share the wire format (`adapters.wire`) and must behave the same:
`tests/contract/test_ai_contract.py` runs every case through each. The key is
`assistant.config_key()` (top-level `"anthropic_api_key"` in `mailmind.config.json`, any
credential style) and only then `ANTHROPIC_API_KEY`; a config key that won't resolve
means no key, never a fall-through to the environment's. With no key the model is
`DisabledModel`: `assistant.calls.engine()` is `"none"`, and a call raises `AIOff` (from
`application.ports`) before anything is sent.
`MAILMIND_NO_SCHEMA=1` falls back to free-form text. AI actions never
fire on their own — drafts wait, bulk commands require a preview and confirm.

An assessment keeps how it came to be (migration 43, `kernel.assessment`): `served_by`, the model `Reply.model` names, which
a server-side fallback can make another; `prompt_version`; and `outcome`, which is `assessed`, or `unusable` or `refused` for
the stand-in stored so a message doesn't go round for ever. `assessments.model` stays the label shown beside a summary, the
model asked for or `parse-error`. `Calls.reply_to` is the whole `Reply` for a caller that keeps these. A batch's answers take
the version its row was sent with, since they can come in after an upgrade. A row from before has all three NULL, and
`answers.assessment` reads them as None: unknown, never worked out. So far nothing but that read looks at them.

`domain.prompts` is every word the model is told. `SYSTEM` goes on every call, a batch's requests included, and is the
one statement that the mail is untrusted data. Each kind of call has a `Template` and a version, which says what to do and
leaves that to `SYSTEM`; only the plan's adds a word about its command, which is typed and not mail. `Template.fill` fills the
slots in one pass, so a subject holding `{blocks}` stays those words, and a template's own braces need no doubling. Words
that change raise the version (all of them, for `SYSTEM`), and `tests/unit/assistant/test_prompts.py` holds the two together.

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
(`except NotSent: raise`) ahead of its own `except Exception`, and so does a route's own
catch for `OverLimit` and `AIOff`, so `H.do_GET` and `H.do_POST` can answer
them: `OverLimit` as a 402 (below), `AIOff` as a 409 with `ai_off: true`, which the page shows as
"AI is off" rather than an error to retry. `call_claude`, `call_claude_json` and `reply_to` go through `Status.noting`,
which records each call's outcome for `assistant.status.report()` (not an `AIOff`: no call was made) — served on `/api/digest` as
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
- `assistant.status.report()` carries `spent_today_micros`, which the sync menu's AI line shows.
- `/api/debug/ai?days=N` (30 by default) lists each day by feature.
- `python3 -m mailmind ai usage` prints the same. It reads the ledger only and never migrates.

The `Gateway` asks the cost governor (`application.governor.CostGovernor`,
`assistant.governor()`) before every call it sends. `domain.features.kind` sorts each feature:
`triage` is background, `draft-ahead` speculative, and every other feature interactive.
- Slots. Two interactive calls at a time, and two background or speculative ones beside them.
- Yielding. Background and speculative calls don't start while an interactive one runs, or for
  45 seconds after one ends (`YIELD_SECONDS`).
- Presence. A speculative call also needs someone to have used the app in the last 45 minutes.
  `H._ui_touch` calls `saw_someone()` for every `/api/` request a person makes, except the
  polling routes in `_UI_QUIET`. A start counts as someone here.
- The daily limit is `assistant.limit_micros()`: the top-level `"ai_daily_limit_usd"`, a
  positive number, or $15 (decision D7). It counts everything the ledger holds for today, and a
  ledger that can't be read counts as over. At the limit, background and speculative calls raise
  `Deferred("limit")`. An interactive call raises `OverLimit`, which `H.do_GET` and `H.do_POST`
  answer as a 402 with `over_limit: true`. The page's `api()` asks (`askOverLimit`), then sends
  the request again with `X-MailMind-AI-Over-Limit: allow` (`handler.AI_OVER_HEADER`), which
  runs it inside `assistant.allowing_over()`.
- The breaker. A 401 or 403 stops every background and speculative call, and three permanent
  4xx in a row for one feature and model stop that pair. It stays open until the model in use
  changes, which a new key, base URL or transport does. Interactive calls still go.

Background work asks `gateway().held_back(feature)` before a batch, and a call that was held
back anyway raises `Deferred(reason)`. Nothing is counted for it, and `Status.noting` doesn't record it
as a failure. Nothing in the governor is durable: a restart closes the breaker, and the limit
holds across one because the ledger does. `/api/debug/ai` shows its `snapshot()`, and the
tests start each one afresh with `forget()`.

A backlog goes through the Message Batches API at half price (`pricing.batch_micros`, decision D7): `BATCH_MIN`
messages waiting is one, whether a first import or after an outage, and new mail stays synchronous.
`assistant.assessments.batch_submit` sends it and `batch_collect` brings the answers in, both from its `round`, which
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

`curation.domain.classifier` assigns one of seven buckets (`SPAM`, `BLOCKED`, `MKT`, `NOTIF`,
`RECEIPT`, `PROTECTED`, `PERSONAL`); `JUNK_BUCKETS` decides what ingest drops. It's a pure function of a
message's headers and the rule book's lists, and `bootstrap.curation.classify` calls it with the rules as they stand.
`curation.domain.rulebook` is a learned per-sender/domain keep/block list on top, persisted to
`mailmind_rules.json` — that file is personal data and stays gitignored. The same file
holds expiry rules (`expire_hours`: sender/domain → hours), enforced by
`Expiry.account` from the CLI and by the server's `expire` job hourly (not read-only).
`rules.remove` deliberately leaves expiry alone; clear it with `set_expiry(token, 0)`.
`curation.adapters.rules_file` (`bootstrap.curation.rules`, the file's one reader) writes it whole (a temp file,
owner-only, renamed over it). A read parses it again only when it changed; one that won't parse leaves the last
rules read well, so a bad edit never stops a sync, and the next save moves it to `backups/` first. The classifier
reads through `rules.sets()`.

A rule change classifies again the stored mail it covers (`ingest.reclassify`, called by whatever calls
`rules.add` or `rules.remove`): each row a rule decides now or decided before. A row no rule had a say in
stays as ingest judged it, since not all it read then is kept. An edit of the file by hand reaches only new mail.

Ingest keeps a message's unsubscribe headers (`messages.list_unsub_raw` and `list_unsub_method`, through
`mime.list_unsub`), `none` included. `Unsubscribing.resolve` answers for a sender from the newest message
that can say: those columns, a stored body, or for older mail the two headers read off the server, within
`UNSUB_READ_WAIT` on the account's gate. `sender_unsub` (migration 34) keeps the answer past the sender's mail,
so a second try after a cleanup's sweep still knows the way.

The preview's `confirm_token` (`perimeter.tokens`) is `<time>.<hmac>`, over the purpose, the sender, the way out it showed
and where that goes (`unsubscribe.preview_said`), under the process's secret. It stands for `TOKEN_LIFE`, ten minutes. A cleanup with one out of date
or naming another way out sends nothing and answers `no_token`, and its block and sweep stand.

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
