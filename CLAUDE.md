# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

A `justfile` wraps everything below — `just` alone lists the recipes. The raw commands
work identically if `just` isn't installed.

```bash
just verify                       # lint + compile + all tests + frontend check — run before committing
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

Tests — plain scripts, no runner, each exits non-zero on failure:

```bash
python3 tests.py                  # classifier behaviour (27 assertions)
python3 tests_sanitize.py         # HTML sanitizer: 24 attack classes, expects 0 leaks
python3 test_css_sanitizer.py     # CSS sanitizer unit tests (40)
python3 test_expire.py            # expiry rules (15)
```

There is no single-test flag. `tests.py` and `tests_sanitize.py` are linear scripts —
comment out cases or copy one into a REPL. `test_css_sanitizer.py` and `test_expire.py`
collect callables from `globals()` whose names start with `test_`, so you can gate them
by renaming.

There is no build step, no linter config and no dependency manifest. `pyflakes` is the
de-facto lint. `just lint` runs it against a one-entry baseline — a known warning in
`readmutq.py` (a lambda closing over an `except ... as e` name, safe because `_commit`
invokes it synchronously) — and fails on anything new. If you fix that, drop it from
`BASELINE` in the justfile.

`just check-frontend` parses every inline `<script>` with `node --check`, checks CSS
brace balance, and rejects raw control bytes in `dashboard.html` (see Conventions).

## Constraints

- **Standard library only.** No `pip install`, ever. This is the single hardest rule in
  the project — it shapes every design decision below (hand-rolled sanitizer, hand-rolled
  IMAP handling, no web framework, no frontend build).
- **The frontend is one file.** `dashboard.html` (~340KB) holds all HTML, CSS and JS
  inline. No bundler, no modules, no npm. Edit it directly.
- **Nothing personal is committed.** `mailmind.db`, `mailmind.config.json`,
  `mailmind_rules.json`, `mailmind_views.json` and `*.jsonl` audit trails are gitignored.
  The repo is public — treat any real address, employer or message count as a leak.

## Architecture

Pipeline, roughly in data-flow order:

```
mailmind.config.json → config.py/accounts.py → ingest.py → mailclass.py → store.py (SQLite)
                                                                ↓
                                              summarize.py (claude CLI) → server.py + dashboard.html
```

Around that spine: `mailops.py` (IMAP writes), `compose.py`/`smtpsend.py`/`outbox.py`
(send), `search.py` (FTS + operator grammar), `threads.py`, `mailparse.py`,
`sanitize_html.py`, `ics.py`, `realtime.py` (IMAP IDLE → SSE), and the AI modules
(`summarize*.py`, `draft_reply.py`, `rewrite.py`, `ask.py`).

The app presents two modes over the same data: **Briefing** (priority list / daily brief)
and **Mailbox** (real folder-and-account client).

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

### Writes, undo, and offline

- Mutations go through `mailops.py`. A connection failure raises a member of
  `mailops._UNREACHABLE`, which the route catches and turns into a durable queue row via
  `store.read_mut_insert`.
- `readmutq.py` drains that queue when IMAP returns, using the `applied` flag as an
  exactly-once gate. It mirrors `outbox.py` 1:1, except the primary op is itself an IMAP
  op, so it runs under `_ops_lock`.
- Undo is an in-memory LIFO `_undo_stack` — **lost on restart**, by design. Bulk actions
  push one grouped entry with children.
- **`pk` is not stable.** Reconcile can renumber rows, so anything durable keys on
  `(account, message_id)` — see `store.get_message_by_msgid`.

### Security model

Three guards in `server.py`, applied per route:

- `_rebind_guard` — Host/Origin must be localhost (blocks DNS rebinding).
- `_mutation_guard` — rebind check **plus** a `X-CSRF-Token` header compared with
  `secrets.compare_digest`. Required on every mutating POST.
- `_strict_origin_guard` — send routes only; an Origin header is mandatory, not optional.

`CSRF` is a per-process token. `dashboard.html` ships a `__CSRF_TOKEN__` placeholder in a
`<meta>` tag that `server.py` substitutes at serve time; the frontend's `api()` helper
attaches it to every request, GETs included.

Mail bodies render in a sandboxed iframe with a deny-all CSP. `sanitize_html.py` is
defense in depth — **the iframe sandbox is the actual boundary**, so a sanitizer miss
alone cannot execute script. Preserve that ordering when touching either.

### AI calls

Everything routes through `summarize.call_claude_json(prompt, schema)`, which shells out
to the local `claude` CLI with `--output-format json --json-schema` and validates the
result. `MAILMIND_NO_SCHEMA=1` falls back to free-form text for older CLI builds. AI
actions never fire on their own — drafts wait, bulk commands require a preview and
confirm.

Absent or unauthenticated `claude` degrades to no summaries rather than failing; keep
that property.

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
