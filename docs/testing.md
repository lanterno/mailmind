# Testing

This says how to run the tests that exist. [test-strategy.md](test-strategy.md) says what to test, at which ring and why.

## Which tests to run first

Tests — `just test` (`uv run pytest -n auto`) runs them all, in a process for each core (pytest-xdist). `just test -n0`, or
`uv run pytest`, runs them in one. `just test -k hardening` picks by file or test name, so `-k` also picks a
single test: `just test -k test_a_rebound_name_reads_nothing`. For a few tests one process is quicker, as the workers take a
few seconds to start. These are the first to run for a change in what they cover, with
the cases pytest collects for each:

```bash
uv run pytest tests/unit/curation/test_classifier.py        # classifier behaviour (79)
uv run pytest tests/security/test_sanitize.py               # HTML sanitizer: 24 attack classes, expects 0 leaks (68)
uv run pytest tests/unit/content/test_css_sanitizer.py      # CSS sanitizer unit tests (66)
uv run pytest tests/unit/curation/test_expire.py            # expiry rules (23)
uv run pytest tests/unit/calendar/test_upcoming.py          # Today & next invites, RRULE expansion + the Waiting view's recipient (77)
uv run pytest tests/unit/calendar/test_invite_text.py       # what an invite's text comes to: who an RSVP goes to, its dates, what's left of a broken one (222)
uv run pytest tests/contract/test_ics_reader_contract.py    # the reader of invites: never raises, plain data, bounded (180)
uv run pytest tests/{unit,integration}/discovery/test_contacts_known.py   # the "new to you" lookup: who counts as known, exact matching, CSRF (44)
uv run pytest tests/unit/compose/test_send_after.py         # /api/send refuses passed and far-off send times (5)
uv run pytest tests/{unit,integration}/compose/test_draft_resume.py   # reopening a saved draft: split, files, Bcc, threading, refusals, saves (162)
uv run pytest tests/security/test_hardening.py              # security and data-loss fixes, one test or more per fix (101)
uv run pytest tests/security/test_mail_frame.py             # a mail body's own document: its policy, who may frame it, what it holds (52)
uv run pytest tests/security/test_malformed_input.py        # a request that won't read gets the 400, never the 500: the cases, and a walk of every POST route (198)
uv run pytest tests/{unit,integration}/http/test_problems.py   # the problem document each of kernel.errors answers (40)
uv run pytest tests/unit/http/test_route.py                 # what a route does around its handler, and what the app answers where none matches, on a toy router (183)
uv run pytest tests/unit/http/test_schema_contract.py       # the test-time hold on answers and on what a use case reads, and every route's own words (148)
uv run pytest tests/unit/http/test_event_stream.py          # what a tab holds, and every way a stream ends, Ctrl-C included (12)
uv run pytest tests/{unit,integration}/http/test_export_route.py   # the download in chunks, and that no way it ends leaves its folder (15)
```

The composer's send-time checks and reopen helpers are `web/src/features/compose/sendGuard.ts` and `draftResume.ts`, free of DOM
and `state` references, with Vitest suites beside them. The send guard's reads `tests/fixtures/send_guard_words.json`.
Vitest has no DOM, so a suite stubs what its module reaches (`document`, `history`, `EventSource`, a timer) with `vi.stubGlobal`
and the fake timers, and runs a hook through `web/src/lib/testing/renderHook.ts`: React renders it into a stand-in root, which a
component that renders nothing asks no more of. A known bug there is an `it.fails` test stating the right behaviour.

## The pytest suite in `tests/`

`tests/unit/<context>/` holds each context's tests that run without the app: a use case over fakes behind its ports, a domain
rule, an adapter against a fake server. `tests/integration/<context>/` holds the ones that start it (the `app`, `world` or
`golden` fixture, directly or through another): a route as a browser calls it, or the app over the fake servers. A file whose
tests go both ways keeps its name in each, so `tests/{unit,integration}/http/test_problems.py` runs both halves. A rule a use case
decides is tested in `unit/`, and `integration/` holds the route that reaches it. `contract/` holds one promise through every
adapter (and the fakes against themselves), `security/` what must never get through, `api/` what any MailMind server answers
over HTTP (the golden suites, run against the server as a program), `characterization/` the app as it answers, in a folder per
context (the hard-won fixes by subject), and `e2e/` the whole program. `support/` holds the helpers, on pytest's path, so a test
says `import problems`.

They pin today's behaviour ahead of the refactor. Each test sees only fictional
data (`example.test` addresses) in a temp directory. `tests/conftest.py` sets that up before any
MailMind module loads:

- It removes any Claude key and any `MAILMIND_HOME` from the environment and sets `TZ=UTC`. It sets `MAILMIND_DB_KEY` to
  the test key (`TEST_DB_KEY`), so every database a test or a program it starts makes is encrypted, and none asks the
  Keychain. A test opens a database file with `database.keyed(path)` and takes SQLite's names from `platform.db.driver`:
  the standard library's `sqlite3` reads no encrypted file, and is for a test that writes a plaintext one on purpose.
- It installs the tripwire in `tests/support/guard.py`. The tripwire refuses the real data files (at the
  repo root, and in the directory `MAILMIND_HOME` named), any non-loopback host, the ports on loopback the model servers
  on this Mac listen on (11434, 1234, 8080, 8000, 1337 and 4891), and the `claude`, `op` and `security` programs. A test
  that trips it fails. It also wraps SQLCipher's `connect`, which raises no audit event as the standard library's does.
- The tripwire puts a stand-in in the login Keychain's place (`tests/support/fake_keychain.py`), handing it to
  `mailmind.platform.keychain.use`, in every test process and every program a test starts with the tripwire installed. The
  autouse fixture `fake_keychain` empties it around each test, and a test takes it to see what was kept. A program shares its
  starter's when `MAILMIND_TEST_KEYCHAIN` names a file, as `just demo` does. The world's accounts keep their passwords there,
  as `{"keychain": "account:<address>"}`.

The fixtures, from the bottom up:

- Every test starts from a new set of the app's objects: `_fresh_app` runs `mailmind.bootstrap.wiring.assemble()` before
  it, so no cache, undo stack, sync status or subscription one test leaves reaches the next, and nothing lists which module
  keeps what. A test reads one through its module (`mailbox.ingest`), never by a name imported at the top of the file,
  which would keep the first set's. `app_client.restart()` is a process restart in the middle of a test.
- `data_dir` makes a fresh temp directory the app's data directory (`settings.use`) and puts a new
  database there: a copy of one `store.init` made once for the session, which is quicker than running every migration.
- `world` is a fictional 25-message corpus (`tests/fixtures/corpus/`, three accounts) served by
  `tests/fakes/imap_server.py`. The app's own ingest synced it into the temp database.
  `tests/fakes/smtp_server.py` receives what the accounts send. Both fakes can refuse, drop or
  renumber on cue (`server.fail`, `server.drop`, `smtp.fail`, ...).
- `app` runs the app in-process: what `mailmind.bootstrap.front.build` makes, on uvicorn. Every account in the world
  goes through imaplib, as a real one does unless it asks otherwise.
  `world.ask_for_imapclient` has an account ask for IMAPClient (`tests/integration/mailbox/test_gmail_through_imapclient.py`).
  Its CSRF token is fixed, and the clock is frozen at `conftest.NOW` (2026-11-02 12:00 UTC); `app.clock.shift(timedelta(...))`
  moves it. The page it answers with is a tiny stand-in build (`_a_build`, `tests/fixtures/tiny_build.py`), so these tests need
  no node. `tests/e2e/test_front_door.py` runs the app as a program instead.
- `ai` gives the fake Claude API (`tests/fakes/anthropic_api.py`) a key it accepts. It answers
  each call from the call's own schema unless the test sets `ai.queue(...)` or `ai.responder`.
  Without `ai` there is no key, so AI mode starts off (`turned-off`) and every AI call raises `AIOff` with nothing sent,
  which a route answers as a 409 with `ai_off`. A test turns AI mode on (`ai_enabled`) to see `no-key`.
- `dns` starts a nameserver on loopback (`tests/fakes/dns_server.py`) whose MX records a test sets (`dns.mx(domain, hosts)`),
  the one a domain's mail servers are asked of: by the app in the test's process, and by a served one, which
  `MAILMIND_TEST_DNS` names it to (`mm-served` hands it to the Rust server through its seams). Without it no test asks any
  nameserver, nor reads which this computer's are, and every domain's mail servers are none.
- `local_ai` starts fake model servers on this Mac (`tests/fakes/openai_api.py`, taking any request) named Ollama and
  LM Studio, with llama.cpp between them at a port nothing listens on: the only ones `GET /api/ai/local` asks, by the app
  in the test's process and by a served one, which `MAILMIND_TEST_LOCAL_AI` names them to (`name=url` pairs between `;`;
  `mm-served` hands it to the Rust server through its seams). Without it an autouse fixture sets that variable empty and
  no test asks a model server, so none running on the machine is ever reached. `just demo` finds none either.
- `openai_api` is a fake OpenAI-compatible API (`tests/fakes/openai_api.py`) on loopback, for every other provider: a test
  chooses the provider with `ai_base_url` at its `.url` and keeps its `KEY` (`.key = None` takes any, as a model on this
  Mac may). A test that chooses a model on this Mac gives it that URL first, so nothing reaches one running on the
  machine.
- `golden` records exchanges in `tests/golden/<suite>/<test>.json`, after it normalizes ports,
  paths and random ids. `uv run pytest --update-golden` rewrites the files; review the diff like
  code. `tests/golden/schema_v<N>.sql` is the schema snapshot: rename it in the commit that adds
  migration N.

`tests/api/` holds the golden suites as a contract any MailMind server keeps, so a server written in another language can be held
to the Python one's answers (docs/clients.md, step 1). Its `app` fixture (`tests/api/conftest.py`) starts the server as a program
over the test's world: `tests/support/serve_python.py`, or the command `MAILMIND_TEST_SERVER` names. The test reaches it over HTTP,
with the CSRF token its page carries, and over a line protocol on its standard input that every such server speaks:

- `clock` moves its frozen time, beside the test's own (`app.clock.shift`).
- `work` runs one pass of a background job, since no worker runs on its own there: `app.work("outbox")`, `"commands"`,
  `"drafts"`, `"snoozes"`, `"follow-ups"`, `"assess"` (triage what AI hasn't summarized), `"tidy"`, `"draft-ahead"`
  (draft one reply ahead, once a test turns `draft_ahead` on), `"forget"` (what went a month ago let go), `"backfill"` (a
  round of the body backfill), `"warm"` (a round of warming folders beyond the Inbox), `"retry"` (a try of each account
  whose server didn't answer, while its trouble may pass), `app.work("reconcile", account)`
  (the ghost pruner's round), or `app.work("sync", account, folder)`, which `world.deliver` runs after delivering.
  `app.work("kicked")` runs a pass of each job the server kicked since the last, as its worker would have run it then (a
  kick of one no worker runs is kept by name, `Jobs::kicked` and `background.kicked`), so a test shows that something
  asked for a round now: a first sync asking for its summaries. One that takes arguments, a folder's sync, is let go.
  `tests/support/serve_python.py` says what each does.
- `settle` waits for the work requests started (`app.settle()`), and `check` fails the test on what the server's own checks refused:
  a schema an answer broke, the tripwire.
- `unsanitized on` (`app.unsanitized()`) has the bodies the reader and its frame show come back as they were given, as if the
  sanitizer had missed everything, so a test shows the frame's sandbox and policy hold alone; `off` sanitizes them again. The
  Python launcher patches its own process; `mm-served` hands the Rust server a sanitizer it can switch off, through its seams.
- The end of its input stops it; `app.restart()` starts it again on the same port, over what it stored.

A test there may read the database file, whose schema the golden `schema_v<N>.sql` pins, and change it only while the server is
stopped (`with app.stopped():`), as the world was made before it started. It never calls or patches a MailMind module to make the
server do something: the server is another process, which that never reaches. A test that can only pin the
Python app's insides stays in-process, in `tests/characterization/`. Each test starts a server, which takes about a second.

The Rust server (`core/crates/mm-server`) is held to the same suites through `core/crates/mm-served`, its launcher for them: the
frozen clock, a tripwire equal to `guard.py` but for the model servers' ports (`Outside` is told a host, never a port),
`fake_keychain`'s file and a sanitizer `unsanitized on` takes away, each handed to the server through its seams.
`mailmind-server`'s seams (`real::seams`) have the sanitizer itself, and nothing in it can take it away. What the Rust
server answers yet is on the ledger, `tests/support/rust_ledger.txt`, one test id a line. `just test-rust`
builds the launcher and runs the ledger's tests against it; `just verify` runs it. When `MAILMIND_TEST_LEDGER` names the ledger,
every served test (`tests/api` and `tests/e2e/browser`) not on it is `xfail(strict=True, reason="not on the Rust server yet")`, so
`just test-rust-all` runs them all against the Rust server and one that passes errors with `XPASS(strict)`: put it on the
ledger. A test goes on the ledger only once it passes against both servers, and the `served` workflow runs both on one commit,
in a job for each server. A job that fails keeps what each of its servers printed (`server.log`) on the run's page, and a test
whose server ended or stopped answering says the last of it.

The IMAP a common action costs (a move, marking read, a sync, a first sync's bytes, a reply filed in Sent) is held to the
`[counts]` in `budgets.toml` by `tests/api/test_budget_counts.py`, on both servers, from what the fake IMAP server recorded.

Under `-n`, each worker is a pytest session of its own: it installs the tripwire, runs its own fake Claude API and makes its own
`world` template and new database, under a temp directory of its own. A test keeps to its temp directory and every server takes
a port the system picks, so no two workers share a file or a port, and a test passes in any worker, in any company.

A known bug is a `xfail(strict=True)` test that states the right behaviour, so fixing the bug
fails the test until the marker goes. `uv run pytest --runxfail -k <name>` shows where each fails.

A test passes or fails on what happened, never on how long it took. Other suites share the machine, so a fixed sleep or a
"finished within N s" fails under load:

- Wait for a condition with `wait_until(cond)` from `tests/support/waiting.py`. It polls for up to 30 s and returns whether
  `cond` came true, so assert it. `blocked_in(thread, fn)` says a thread waits inside `fn` on a lock, a Condition or an
  Event. That lets a test act once a thread has queued. `tests/support/gate.py` builds `waits_for_a_gate(thread)` and
  `waiting_for_a_gate()` on it, and `tests/support/at_once.py` builds `overlap`: two changes to a kept document at once, the first
  held after its read until the second has read too or waits for the write lock.
- Read time from a clock the test moves: the `app` fixture's `app.clock`, `time_machine` around code that runs outside it,
  or an injected `now`. Test speed through what the code does: a call counted, an Event set, a thread still alive after a
  join long past its due time. A wake shows up against a wait made long, such as an hour, rather than one timed short.
- If a bound must stay, make it one-sided and generous: well past what the step takes, and short of the wait it must not
  make. `tests/unit/compose/test_markdown.py` holds the renderer to linear time with a ratio of CPU time at two lengths.
- A sleep that is the behaviour under test (a stream left silent, a page that must stop growing) may stay. It needs a
  comment and must not lead to an assertion about wall time.
- In the browser, wait with Playwright's `expect` and `wait_for`. Move the page's timers with `page.clock.run_for`.
  `page.heard_all_asked()` comes before saying that a request wasn't made.

A leaf the Rust core now computes for the Python app (identity, the search grammar, the classifier, threading, MIME, the
sanitizer) has a `test_<leaf>_in_rust.py` beside its tests, which keeps the Python it replaced as the oracle and holds
the core to its answers with Hypothesis, over every character where Python and Rust read text differently. A behaviour
change to a leaf is made in the core, and changes the oracle in the same commit as the test that states it.

`tests/integration/mailbox/test_queue_state_machine.py` runs Hypothesis over the command path and Undo: random moves, flag
changes, outages, refusals, crashes (in a click and on the worker), syncs, Undo, Retry and Discard. It runs 12 fixed cases in the suite;
`MAILMIND_MACHINE_RUNS=1500` asks for that many with fresh seeds, and `MAILMIND_MACHINE_STEPS` for longer ones.

`tests/e2e/browser/` drives the app in Chromium (Playwright), serving the real build, made once for the session into a folder of
its own. Its `app` is the server as a program, as in `tests/api/` and under the same rules, so the page's tests hold any server to
what the Python one does. The mail frame's tests that take `unsanitized`, and the `wall` cases without a sanitizer, have the
server take its sanitizer away through the protocol (`app.unsanitized()`), to show the sandbox holds alone. In every test:

- The page may reach only the app's origin.
- Its clock starts at the server's frozen time.
- Console errors fail the test, and so does any content security policy violation.
- A view's `axe` test expects no findings.
- `page.pw.evaluate` has no timeout, so a promise that never settles hangs the run for good. A test waiting on animations leaves out
  the looping ones (`FADES_DONE`).

These tests are marked `browser` and skipped unless pytest runs with `--browser`.
`just test-full` installs Chromium if needed and runs everything. `just verify` runs the ones on the Rust server's
ledger, against that server (`just test-rust`), and leaves the rest out to stay fast. `just test-full` and `just test-browser` run four processes at a time (`-n 4`), each with a Chromium and a build of
its own: a Chromium keeps cores of its own busy, and with a process for each core the tests' waits for the page run out.

`tests/contract/test_mail_session_dovecot.py` holds the MailSession contract against a real server,
Dovecot 2.3 in Docker (pinned by digest), through each adapter, and checks what each call left
through a connection of its own. Its tests are marked `dovecot` and skipped unless pytest runs
with `--dovecot`; `just test-dovecot` runs them, in one process, and needs Docker running. One container serves
the run, and each test logs in as a user of its own, with an empty mailbox.

The Python has no build step. `pyproject.toml` declares the runtime packages and, in its dev group,
the dev tools (pytest, ruff, import-linter, Playwright and the like), all locked with hashes in
`uv.lock`, and `.python-version` pins 3.14 for uv and pyenv alike. `just lint` is `ruff check` with pyflakes' rules only (`select = ["F"]`),
then `lint-imports` (import-linter) on the `mailmind` package's layers, then `pyright`: basic rules across the package
and strict ones on what `[tool.pyright]` lists, which a package joins once it reaches zero. New tests are pytest-native, in the folder of `tests/` they belong to;
pytest's `--import-mode=importlib` keeps a folder like `tests/unit/calendar` from shadowing a module.

## The end-to-end scenarios and `mm`

[test-architecture.md](test-architecture.md) is the framework; what exists of it is step 1, the page. A scenario
drives the real build in Chromium, served by the Rust server (`mailmind-server` behind `mm-served`), over a world of
fictional mail on the fake servers, and reads like a person's account of what they did:

```python
from mmtest import mail

def test_a_reply_answers_the_sender_and_threads(world, app):
    note = world.deliver(mail.from_("lin").subject("Lunch on Friday?"))
    app.inbox.show_tab("Everything")
    app.inbox.row(subject="Lunch on Friday?").open()
    app.reader.reply().type("See you there.")
    app.composer.send()
    world.clock.advance(seconds=11)                 # past Undo Send's window
    assert world.smtp.wait_for_one().message["In-Reply-To"] == note.message_id
```

```bash
just e2e                                         # every scenario, four processes at a time (builds mm-served first)
just e2e -m compose                              # one feature: the folder's name under tests/scenarios
just e2e tests/scenarios/compose/test_reply.py --keep   # a failure stays up for `just mm attach` until `just mm down`
just e2e catalogue                               # and write build/catalogue.md: each scenario's sentence and steps
```

- **Where.** Scenarios are `tests/scenarios/<feature>/test_*.py`, named as sentences; the framework is
  `tests/framework/mmtest/` (`world.py`, `page.py`, `shown.py`, `screens.py`, `trace.py`, `plugin.py`), loaded by
  `tests/conftest.py`. They are marked `scenario` and skipped unless pytest runs with `--e2e`.
- **The world** is a temp home with a copy of the corpus's database, the fake IMAP, SMTP and Claude servers, and the
  server on it. Its clock stands at `conftest.NOW` and moves only by `world.clock.advance(...)`, which moves the
  server's and the page's together. Background jobs are paused: `world.run("outbox")` pumps one, and `world.smtp.wait_for_one()`
  pumps the outbox. `world.judge(note, importance=5)` stands in for the AI's triage, `world.first_run()` for a person
  the guide hasn't been offered to.
- **Each action settles** before the next line: the page has applied the core's last event, nothing is in flight, no
  short delay or animation is pending, and the jobs the app kicked have run. A step that can't settle fails saying what
  was busy. Don't wait or sleep in a scenario.
- **What a person sees** is `app.shown()`, a text snapshot (its `text()` is what `mm shown` prints), with `rows`,
  `chosen`, `open`, `composer`, `floats`, `toast`, `tray`, `focus` and `count(tab)`. Assert on it, or on the world
  (`world.where(subject)`, `world.smtp.sent`), never on the page's DOM.
- **Finding things** is by role and label within a region (`app.find("button", "Reply", within="reader")`). The few
  anchors are `data-mm` attributes, listed in `tests/framework/anchors.toml`; `tests/unit/test_anchors.py` holds the
  page to that list. Add one only where role and label can't work, and list it there.
- **A failure** prints a one-line repro (`just e2e <test> --client page`), what was shown when it failed, and every
  step; `build/traces/<test>/` keeps `trace.txt`, `trace.jsonl`, `shown.txt`, `picture.png` and `server.log`. The run
  ends with its slowest steps. The page's own problems (an uncaught error, a console error, a request off its origin)
  and the server's tripwire fail a scenario too.

`mm` is the same framework by hand, on a world of its own in a temp home, under the same tripwire:

```bash
just mm up page          # a world and the page on it, then an `mm>` prompt (--detach: none; --headed: see Chromium)
just mm shown            # one verb at a time, as an agent drives it; `just mm help` lists them all
just mm click tab Everything in list
just mm press j
just mm shown --diff     # only what changed
just mm deliver Venue for Thursday --from lin
just mm clock +7h
just mm save-step "open the first row"   # what you did since the last save, appended to build/mm/draft_scenario.py
just mm down
```

The session is this worktree's (its socket and record are named from the worktree's path, in the system's temp
folder), so agents in other worktrees each have their own.

## The Mac app's runs

`just apple test` runs the Mac app's tests in a test bundle of their own, where no app is launched, each over a suite
of defaults of its own. `just apple demo`, its `smoke`, `measure` and `picture`, `just apple smoke` and `measure`, the
release check and CI's `mac-floor` launch the app itself, and every build of it is `io.github.lanterno.mailmind`, the
person's own MailMind's identifier, whose defaults macOS would hand it. So a run that isn't a person's (one that names
a home with `MAILMIND_HOME`, the keychain's stand-in with `MAILMIND_TEST_KEYCHAIN`, or a measure mode with
`MAILMIND_MEASURE`) keeps its preferences apart by itself, however it was started: the first thing it does
(`Sources/main.swift`, `RunPreferences`) makes its user defaults volatile, so it reads and writes nothing of the
person's MailMind (its settings, the read-only switch, the window frames AppKit saves, a window's saved state). It
carries in the Mac's own settings (Dark Mode, language and region), and keeps its own in its home, as
`Library/Preferences/io.github.lanterno.mailmind.plist`; one that names no home keeps them only while it runs.
`demo_home.py` checks after each run that the file is there (`tests/unit/test_mac_demo_home.py`).

To open the app by path, as an agent might, name a temp home, the keychain's stand-in in it and a `MAILMIND_DB_KEY` of
the run's own, and read the run's preferences in that home, never with `defaults` on the app's domain, which is the
person's. `CFFIXED_USER_HOME` is no help: on macOS 26 it moves the home folder, but not the defaults.

## Supply chain

`just audit` (part of `just verify`) looks up what `uv.lock` and `web/package-lock.json` pin: `pip-audit` for the first, `osv-scanner` for both.
A finding fails it. With no network it says so and passes. `web/osv-scanner.toml` holds what it may pass over, each with its reason and a date it lapses on.
