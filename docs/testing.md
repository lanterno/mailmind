# Testing

## Which tests to run first

Tests — `just test` (`uv run pytest`) runs them all. `just test -k hardening` picks by file or test name, so `-k` also picks a
single test: `just test -k test_a_rebound_name_reads_nothing`. These are the first to run for a change in what they cover, with
the cases pytest collects for each:

```bash
uv run pytest tests/unit/curation/test_classifier.py        # classifier behaviour (35)
uv run pytest tests/security/test_sanitize.py               # HTML sanitizer: 24 attack classes, expects 0 leaks (68)
uv run pytest tests/unit/content/test_css_sanitizer.py      # CSS sanitizer unit tests (45)
uv run pytest tests/unit/curation/test_expire.py            # expiry rules (23)
uv run pytest tests/unit/calendar/test_upcoming.py          # Today & next invites, RRULE expansion + the Waiting view's recipient (77)
uv run pytest tests/unit/calendar/test_invite_text.py       # what an invite's text comes to: who an RSVP goes to, its dates, what's left of a broken one (222)
uv run pytest tests/contract/test_ics_reader_contract.py    # the reader of invites: never raises, plain data, bounded (180)
uv run pytest tests/unit/discovery/test_contacts_known.py   # the "new to you" lookup: who counts as known, exact matching, CSRF (44)
uv run pytest tests/unit/compose/test_send_after.py         # /api/send refuses passed and far-off send times (5)
uv run pytest tests/unit/compose/test_draft_resume.py       # reopening a saved draft: split, files, Bcc, threading, refusals, saves (162)
uv run pytest tests/security/test_hardening.py              # security and data-loss fixes, one test or more per fix (101)
uv run pytest tests/security/test_mail_frame.py             # a mail body's own document: its policy, who may frame it, what it holds (52)
uv run pytest tests/security/test_malformed_input.py        # a request that won't read gets the 400, never the 500: the cases, and a walk of every POST route (198)
uv run pytest tests/unit/http/test_problems.py              # the problem document each of kernel.errors answers (32)
uv run pytest tests/unit/http/test_route.py                 # what a route does around its handler, and what the app answers where none matches, on a toy router (183)
uv run pytest tests/unit/http/test_schema_contract.py       # the test-time hold on answers and on what a use case reads, and every route's own words (148)
uv run pytest tests/unit/http/test_event_stream.py          # what a tab holds, and every way a stream ends, Ctrl-C included (12)
uv run pytest tests/unit/http/test_export_route.py          # the download in chunks, and that no way it ends leaves its folder (17)
```

The composer's send-time checks and reopen helpers are `web/src/features/compose/sendGuard.ts` and `draftResume.ts`, free of DOM
and `state` references, with Vitest suites beside them. The send guard's reads `tests/fixtures/send_guard_words.json`.
Vitest has no DOM, so a suite stubs what its module reaches (`document`, `history`, `EventSource`, a timer) with `vi.stubGlobal`
and the fake timers, and runs a hook through `web/src/lib/testing/renderHook.ts`: React renders it into a stand-in root, which a
component that renders nothing asks no more of. A known bug there is an `it.fails` test stating the right behaviour.

## The pytest suite in `tests/`

`tests/unit/<context>/` holds each context's tests, `contract/` one promise through every adapter (and the fakes against
themselves), `security/` what must never get through, `characterization/` the app as it answers (goldens and, in a folder per context, the
hard-won fixes by subject) and `e2e/` the whole program. `support/` holds the helpers, on pytest's path, so a test says `import problems`.

They pin today's behaviour ahead of the refactor. Each test sees only fictional
data (`example.test` addresses) in a temp directory. `tests/conftest.py` sets that up before any
MailMind module loads:

- It removes any Claude key and any `MAILMIND_HOME` from the environment and sets `TZ=UTC`.
- It installs the tripwire in `tests/support/guard.py`. The tripwire refuses the real data files (at the
  repo root, and in the directory `MAILMIND_HOME` named), any non-loopback host, and the
  `claude`, `op` and `security` programs. A test that trips it fails.

The fixtures, from the bottom up:

- `data_dir` makes a fresh temp directory the app's data directory (`settings.use`) and creates
  the schema there.
- `world` is a fictional 25-message corpus (`tests/fixtures/corpus/`, three accounts) served by
  `tests/fakes/imap_server.py`. The app's own ingest synced it into the temp database.
  `tests/fakes/smtp_server.py` receives what the accounts send. Both fakes can refuse, drop or
  renumber on cue (`server.fail`, `server.drop`, `smtp.fail`, ...).
- `app` runs the app in-process: what `mailmind.bootstrap.front.build` makes, on uvicorn. Every account in the world
  goes through imaplib, as a real one does unless it asks otherwise.
  `world.ask_for_imapclient` has an account ask for IMAPClient (`tests/unit/mailbox/test_gmail_through_imapclient.py`).
  Its CSRF token is fixed, and the clock is frozen at `conftest.NOW` (2026-11-02 12:00 UTC); `app.clock.shift(timedelta(...))`
  moves it. The page it answers with is a tiny stand-in build (`_a_build`, `tests/fixtures/tiny_build.py`), so these tests need
  no node. `tests/e2e/test_front_door.py` runs the app as a program instead.
- `ai` gives the fake Claude API (`tests/fakes/anthropic_api.py`) a key it accepts. It answers
  each call from the call's own schema unless the test sets `ai.queue(...)` or `ai.responder`.
  Without `ai` there is no key, and AI calls get a 401.
- `golden` records exchanges in `tests/golden/<suite>/<test>.json`, after it normalizes ports,
  paths and random ids. `uv run pytest --update-golden` rewrites the files; review the diff like
  code. `tests/golden/schema_v<N>.sql` is the schema snapshot: rename it in the commit that adds
  migration N.

A known bug is a `xfail(strict=True)` test that states the right behaviour, so fixing the bug
fails the test until the marker goes. `uv run pytest --runxfail -k <name>` shows where each fails.

`tests/unit/mailbox/test_queue_state_machine.py` runs Hypothesis over the command path and Undo: random moves, flag
changes, outages, refusals, crashes (in a click and on the worker), syncs, Undo, Retry and Discard. It runs 12 fixed cases in the suite;
`MAILMIND_MACHINE_RUNS=1500` asks for that many with fresh seeds, and `MAILMIND_MACHINE_STEPS` for longer ones.

`tests/e2e/browser/` drives the app in Chromium (Playwright) on the `app` fixture, serving the real build (`serve_the_build`), made once for the session into a folder of its own:

- The page may reach only the app's origin.
- Its clock starts at the server's frozen time.
- Console errors fail the test, and so does any content security policy violation.
- A view's `axe` test expects no findings.
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

The Python has no build step. `pyproject.toml` declares the runtime packages and, in its dev group,
the dev tools (pytest, ruff, import-linter, Playwright and the like), all locked with hashes in
`uv.lock`, and `.python-version` pins 3.14 for uv and pyenv alike. `just lint` is `ruff check` with pyflakes' rules only (`select = ["F"]`),
then `lint-imports` (import-linter) on the `mailmind` package's layers, then `pyright`: basic rules across the package
and strict ones on what `[tool.pyright]` lists, which a package joins once it reaches zero. New tests are pytest-native, in the folder of `tests/` they belong to;
pytest's `--import-mode=importlib` keeps a folder like `tests/unit/calendar` from shadowing a module.

## Supply chain

`just audit` (part of `just verify`) looks up what `uv.lock` and `web/package-lock.json` pin: `pip-audit` for the first, `osv-scanner` for both.
A finding fails it. With no network it says so and passes. `web/osv-scanner.toml` holds what it may pass over, each with its reason and a date it lapses on.
