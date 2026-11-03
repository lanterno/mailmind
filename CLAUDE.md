# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

A `justfile` wraps everything below — `just` alone lists the recipes. The raw commands
work identically if `just` isn't installed. `just lint` and `just test` need
[uv](https://docs.astral.sh/uv/) (`brew install uv`); `uv run` installs the dev tools on first use.

```bash
just verify                       # lint + compile + all tests + frontend check — run before committing
just test-full                    # every test, plus the browser tests in Chromium (Playwright)
just dev                          # read-only server, no background sync (safest for local work)
just run [port]                   # the real thing (default http://127.0.0.1:8721)
```

```bash
python3 server.py [port]          # run the app (default http://127.0.0.1:8721)
MAILMIND_NOSYNC=1 python3 server.py   # serve read-only, no background sync (safe for local dev)

python3 config.py check           # verify credentials resolve, per account (prints ok/fail, no secrets)
python3 ingest.py all 90          # pull last 90 days; `ingest.py <email> <days>` for one account
python3 summarize.py 2000 3       # summarize up to 2000 messages, 3 workers
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
python3 test_claude_api.py        # Claude API engine against a local stub server, and summarize.status (22)
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
`test_thread_file.py`, `test_claude_api.py`, `test_upcoming.py`, `test_contacts_known.py`,
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

- It removes any Claude key and any `MAILMIND_HOME` from the environment, pins
  `MAILMIND_AI=api` and sets `TZ=UTC`.
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
- `app` runs `server.py` in-process. Its CSRF token is fixed, and the clock is frozen at
  `conftest.NOW` (2026-11-02 12:00 UTC); `app.clock.shift(timedelta(...))` moves it.
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
- Console errors fail the test.
- axe's findings are compared with `tests/golden/browser/axe_baseline.json`, which may only go down.

These tests are marked `browser` and skipped unless pytest runs with `--browser`.
`just test-full` installs Chromium if needed and runs everything. `just verify` leaves them out
to stay fast.

There is no build step. `pyproject.toml` declares the runtime packages and, in its dev group,
the dev tools (pytest, ruff, import-linter, Playwright and the like), all locked with hashes in
`uv.lock`, and `.python-version` pins 3.14 for uv and pyenv alike. `just lint` is `ruff check` with pyflakes' rules only (`select = ["F"]`),
then `lint-imports` (import-linter) on the `mailmind` package's layers. Ruff's one accepted
finding carries a `# noqa: F821` in `readmutq.py`: a lambda closing over an `except ... as e`
name, safe because `_commit` invokes it synchronously. New pytest-native tests go in `tests/`;
pytest's `--import-mode=importlib` keeps that directory from shadowing the root's `tests.py`.

`just check-frontend` parses every inline `<script>` with `node --check`, checks CSS
brace balance, and rejects raw control bytes in `dashboard.html` (see Conventions).

## Constraints

- **`python3 server.py` runs with nothing installed.** The app was standard library only until
  the refactor's Phase 2, and that shaped every design decision below (hand-rolled sanitizer,
  hand-rolled IMAP handling, no web framework, no frontend build). Since then it may use
  runtime packages (FastAPI and uvicorn so far), each declared in `pyproject.toml` and locked
  with hashes in `uv.lock`. Each dependency change is its own commit, and the `uv.lock` change
  goes in the next. But `python3 server.py`, the old front door, is the kill switch while the
  new one carries daily use, so nothing it imports may import a third-party package.
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
`sanitize_html.py`, `ics.py`, `realtime.py` (IMAP IDLE → SSE), and the AI modules
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
  `python3 server.py [port]` puts the port in the settings.
- `mailmind.bootstrap.legacy.wire(server)` connects the outbox, the read-mutation queue, the
  thread summarizer and the IDLE layer to the server's lock and callbacks. `server.__main__` passes
  `sys.modules[__name__]`: importing `server` there would load a second copy with its own lock.
- `mailmind.kernel` is empty so far. It stays standard library only and imports nothing else
  from `mailmind`.
- Two checks hold the shape. import-linter's layers contract in `pyproject.toml` (bootstrap,
  then platform, then kernel) runs in `just lint`. `tests/legacy_ratchet.txt` lists every import
  of a legacy module from inside the package; `tests/test_ratchet.py` fails when the code and the
  list differ. Only `mailmind.bootstrap` may appear in it, and it should only shrink.

### Routing — there is no router

`server.py` is one `BaseHTTPRequestHandler` subclass. `do_GET` and `do_POST` are long
`if/elif` chains comparing `u.path` against **literal strings**. There is no route table,
no decorator registry, and no `getattr`-based dispatch anywhere in the file.

Consequence: to find a route, grep the literal path. To confirm a handler is dead, grep
its name — nothing can reach it dynamically.

### Schema migrations

`store.py` holds a `SCHEMA` executescript plus `_MIGRATIONS`, a list of
`(target_user_version, callable)` run against `PRAGMA user_version` in `init()`.

- Migrations are **append-only**. Never renumber or delete one — the version number is
  the on-disk contract, and retired features leave a historical no-op behind (see `_m14`
  notes → `_m18_drop_notes`).
- Each must be idempotent; `init()` runs on every process start.
- `_LEGACY_COLS` handles pre-versioning DBs via idempotent column adds.

### Concurrency

One process-wide `_ops_lock` in `server.py` serializes all live-IMAP work — reads,
mutations, undo, reconcile, the outbox worker. It is coarse and a known bottleneck
(see ROADMAP.md); the connection pool's per-account lock is taken *inside* it.

`pool.py` (persistent per-account IMAP connections) and `warm.py` (multi-folder sync)
are both **default-off** behind `MAILMIND_POOL` / `MAILMIND_WARM_FOLDERS`. Code must
work with them unset.

`realtime.stop()` only shuts each IDLE worker's socket down; the worker closes its own
connection. Closing a socket another thread may be about to wait on frees its descriptor number
for the next socket opened.

`server.nosync()` is the only reader of `MAILMIND_NOSYNC`. Any value but empty, `0`, `false`,
`no` or `off` turns it on, so a typo fails toward read-only.

### Writes, undo, and offline

- Mutations go through `mailops.py`. A connection failure raises a member of
  `mailops._UNREACHABLE`, which the route catches and turns into a durable queue row via
  `store.read_mut_insert`.
- `readmutq.py` drains that queue when IMAP returns, using the `applied` flag as an
  exactly-once gate. It mirrors `outbox.py` 1:1, except the primary op is itself an IMAP
  op, so it runs under `_ops_lock`.
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
  `/api/contacts/known` (the "new" marks) is a read-only POST: pure SQL, no `_ops_lock`.
  `do_send` refuses a `send_after` that has passed instead of sending now.
- Reopening a draft (`/api/draft/resume` → `draft_resume`) reads the draft's own bytes, never
  the original. At each save `do_draft` records beside the `drafts` row what the composer held
  (migration 20: `own_text`, `tail_text`, `tail_html`, `bcc`, `raw_sha`). That record is
  trusted only for the very copy it describes, the same UID or the same sha256. The composer
  sends `resume` (threading and the quote's text) on every save and send. `_compose_spec`
  then builds `own + tail`, with HTML only where the row vouches for that exact tail. The
  original is looked up by `(account, Message-ID)` for `\Answered` only. `_draft_live`, the
  "is this copy still there" probe, is a stateless 10-second connection outside `_ops_lock`.
  Under the lock `draft_resume` re-reads the `drafts` row and answers 409 if it is gone or moved, so
  `do_draft` and the outbox's draft cleanup also change that row only under `_ops_lock`. Edit
  answers 423 while a queued or sending outbox row holds the draft. A copy in another account
  or folder is removed only after the new APPEND succeeds, and only if MailMind saved it or
  the server flags it `\Draft`.
- A composer replaced after an `await` (Undo Send, a failed send's Edit → `makeRoom`) must
  close and open in the same task. In the gap `_syncBack`'s MutationObserver calls
  `history.back()`, the new composer's `pushState` lands before that traversal does, and the
  Back trap ends one entry short, so the next close steps the tab off the app.
- **`pk` is not stable.** Reconcile can renumber rows, so anything durable keys on
  `(account, message_id)` — see `store.get_message_by_msgid`.

### Security model

Four guards in `server.py`:

- `_rebind_guard` — Host/Origin must be localhost (blocks DNS rebinding). It is the first
  check in `do_GET`, so it covers every GET, `/` included; POSTs get it via `_mutation_guard`.
- `_cross_site` — every GET: refuses one the browser marks `Sec-Fetch-Site` cross-site or
  same-site, which a no-cors `<img>` from any page would otherwise pass. A top-level visit to
  `/` is still allowed.
- `_mutation_guard` — rebind check **plus** a `X-CSRF-Token` header compared with
  `secrets.compare_digest`. Required on every mutating POST.
- `_strict_origin_guard` — send routes only; an Origin header is mandatory, not optional.

Every `_send` answer carries `X-Frame-Options: DENY`. A failed GET or POST answers a fixed
message and logs the detail. STARTTLS, IMAP and SMTP alike, takes `accounts.tls_context(host)`:
verified everywhere but loopback, where Proton Bridge's certificate is self-signed.

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
`call_claude` for plain text). `summarize.engine()` picks the backend: the Messages API
via `claude_api.py` when a key is set, else the local `claude` CLI with
`--output-format json --json-schema`. `MAILMIND_AI=api|cli` overrides it. The key is
`config.anthropic_key()` (top-level `"anthropic_api_key"` in `mailmind.config.json`, any
credential style) and only then `ANTHROPIC_API_KEY`; a config key that won't resolve
means no key, never a fall-through to the environment's.
`MAILMIND_NO_SCHEMA=1` falls back to free-form text on either engine. AI actions never
fire on their own — drafts wait, bulk commands require a preview and confirm.

`claude_api.py` is urllib, not the SDK (stdlib-only rule). `api_schema` drops the
keywords structured output rejects (`minimum`, `maximum`, `maxLength`) and closes every
object, so callers must keep clamping what they read. It sends `fallbacks: "default"` on
the models that accept it, reads a refusal as an empty reply, and retries
408/409/429/5xx twice.

Both engines keep one contract: a failed call raises `RuntimeError` (the caller retries
later, nothing is cached); a successful but unusable reply returns `{}` (the caller
stores its fallback). No key and no `claude` degrades to no summaries rather than
failing; keep that property. `call_claude` and `call_claude_json` are wrapped by `_noted`,
which records each call's outcome for `summarize.status()` — served on `/api/digest` as
`ai`, and the source of the brief's "AI is off" card and the sync menu's AI line. A new
entry point that bypasses them won't show up there.

### Classification

`mailclass.py` assigns one of seven buckets (`SPAM`, `BLOCKED`, `MKT`, `NOTIF`,
`RECEIPT`, `PROTECTED`, `PERSONAL`); `JUNK_BUCKETS` decides what ingest drops.
`rules.py` layers a learned per-sender/domain keep/block list on top, persisted to
`mailmind_rules.json` — that file is personal data and stays gitignored. The same file
holds expiry rules (`expire_hours`: sender/domain → hours), enforced by
`manage.expire_account` from the CLI and by `server.expire_loop` hourly (not under NOSYNC).
`rules.remove` deliberately leaves expiry alone; clear it with `set_expiry(token, 0)`.

## Conventions

- **Don't create `*.bak-*` snapshot files.** The repo used to carry 38 of them; they are
  now gitignored. Use a git branch.
- **Keep `dashboard.html` free of raw control bytes.** A single literal NUL byte once
  made `grep` and `ripgrep` treat the file as binary and silently return nothing past it,
  hiding half the frontend from every search tool. Use escape sequences in string
  literals.
- **`ROADMAP.md` lists only work that is still open.** When something ships, summarize it
  into the README's Features section and delete it from the roadmap — no "✅ DONE"
  entries. Verify status against the code, not the doc, and cite functions and symbols
  rather than line numbers (they drift).
