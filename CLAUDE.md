# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

A `justfile` wraps everything below — `just` alone lists the recipes. The raw commands
work identically if `just` isn't installed. The recipes and every command need
[uv](https://docs.astral.sh/uv/) (`brew install uv`); `uv run` installs the runtime packages and
the dev tools on first use.

```bash
just verify                       # lint + compile + all tests + frontend check + audit — run before committing
just test-full                    # every test, plus the browser tests in Chromium (Playwright)
just test-dovecot                 # the MailSession contract against Dovecot in Docker
just demo [port]                  # the whole app on fictional mail and fake servers: nothing of yours is read or changed
just dev                          # read-only: writes nothing to a mail server, no background sync
just run [port]                   # build the app, then the real thing (default http://127.0.0.1:8721)
just setup                        # install the Python packages, node_modules and Chromium
```

```bash
uv run python -m mailmind serve [port]   # run the app as last built (default http://127.0.0.1:8721)
MAILMIND_PROFILE=read-only uv run python -m mailmind serve   # the same, read-only

uv run python -m mailmind check         # verify credentials resolve, per account (prints ok/fail, no secrets)
uv run python -m mailmind sync all 90   # pull last 90 days; `sync <email> <days>` for one account
uv run python -m mailmind assess 2000 3 # summarize up to 2000 messages, 3 workers
uv run python -m mailmind rules         # the learned rules; `rules keep|block|remove|expire ...` change them
uv run python -m mailmind sweep --dry   # what a junk sweep would trash; also status, inventory, plan, expire
uv run python -m mailmind export        # what an export would hold; `--format eml|mbox|db --out DIR` writes it
uv run python -m mailmind db check-upgrade   # owner only: try the next upgrade on a copy; counts and checks
uv run python -m mailmind db compact         # owner only, MailMind stopped: give back the room freed in the file
uv run python -m mailmind ai usage --days 7  # what the Claude API cost each day, by feature; reads only
uv run python -m mailmind assess --batch     # send what has no summary as a batch; brings in one that ended
```

`just test` (`uv run pytest -n auto`) runs every test, a process for each core, and `just test -k hardening` picks by file or
test name; `just test -n0` runs them in one process.
[docs/testing.md](docs/testing.md) lists what to run first for a change.

## Constraints

- **Runtime packages are declared and locked.** The app uses FastAPI, uvicorn, the Anthropic SDK, IMAPClient, icalendar and
  recurring-ical-events so far, each declared in `pyproject.toml` and locked with hashes in `uv.lock`. Each dependency change is its
  own commit, and the `uv.lock` change goes in the next. The sanitizer, the IMAP handling and the route tables are hand-rolled on the
  standard library, and most of the package still is.
- **The app exists only as built.** `web/index.html` and `web/src/main.tsx` are the React app, and `just build-web` writes it to
  `mailmind/http/dist` (gitignored). `just run` builds it first, and an edit reaches the app only through a build. See
  [docs/frontend.md](docs/frontend.md).
- **Nothing personal is committed.** `mailmind.db`, `mailmind.config.json`,
  `mailmind_rules.json`, `mailmind_views.json`, `mailmind_snippets.json`, `mailmind.log*` and `*.jsonl` audit trails are gitignored.
  They live in the data directory: the repo root, unless `MAILMIND_HOME` names another.
  The repo is public — treat any real address, employer or message count as a leak.


## The shape of the code

`mailmind/` runs in layers, each importing only those below it: `bootstrap`, `http`, the eight contexts, `platform`, `content`, `kernel`.
A context is `api.py`, `domain/`, `application/` and `adapters/`, with adapters over application over domain. Contexts never import each
other: `bootstrap/` binds them, and a context says what it needs in its `application/ports.py`. import-linter's contracts in
`pyproject.toml` hold this in `just lint`, and `tests/unit/test_architecture.py` holds what the import graph can't, among it that other contexts' wiring reaches the Mailbox's store only through `stored_mail`.

| Context | What it is |
| --- | --- |
| `accounts` | The mail accounts and how MailMind reaches each |
| `mailbox` | The mail on each server and its folders: sync, the store, filing, undo's reversals |
| `discovery` | Finding mail by what a person types |
| `curation` | What stays in your inbox: the classifier, rules, the Screener, sweeps, expiry, unsubscribe |
| `attention` | What asks for you later: snooze, mute, reply later, follow-ups |
| `calendar` | Invites, recurring series, Today & next, RSVP |
| `compose` | Drafts, building a message, the outbox and delivery |
| `assistant` | Everything asked of Claude |

The page is `web/src`: `app/` composes the features, `features/<x>/` each hold one area and offer what others use through an `index.ts`,
and `lib/` is what they share. The features stand in one order (`ORDER` in `web/lint/slices.js`) and a feature imports only those before it.

## Rules that hold everywhere

The docs below say why. These are the ones a change can break without noticing:

- **The environment and paths.** Only `mailmind.platform.environment` reads the environment; a new variable is declared there and named
  in the README's table. Code calls `settings.current()` where it opens a file, never once at import.
- **Logging.** `from mailmind.platform.logging import get_logger`. A message is a literal, and a new field joins `ALLOWED` only if its
  value can never be a person's.
- **The database.** A use case holds no SQL and gets a `UnitOfWork`, one connection a block. No transaction stays open across a network
  call: commit first, and a new network path calls `txguard.refuse_network`.
- **Migrations are append-only.** A schema change is a new file after the last. Never edit the frozen `migrations/m0001_m0020.py` and the
  copies beside it, never renumber or delete one. Rename `tests/golden/schema_v<N>.sql` in the commit that adds migration N.
- **Identity.** `pk` is not stable. Durable state keys on the message key, and the API names no pk: a copy is key, folder and uid.
- **Changing mail on a server.** Every change a person asks for goes through `mailbox.commands`, and every job that talks IMAP goes through
  `gates.run`. A new list query leaves out `pending_removal` rows. A new write path queues or calls `profile.refuse_writes` first. Never
  send a bulk `EXPUNGE`.
- **What reaches IMAP.** Folder names go only through `accounts.mailbox_arg` and other quoted values through `accounts.imap_quote`. Never
  build `f'"{folder}"'`.
- **Routes.** A handler is a plain `def` on a literal path, so grep the literal path to find one. A use case that can't do what was asked
  raises one of `mailmind.kernel.errors`: no refusal answers a 200.
- **AI calls.** Everything goes through `assistant.calls`, naming a `feature`. A caller lets `NotSent` through ahead of its own
  `except Exception`. AI actions never fire on their own, and a new AI route spends on a POST.
- **Mail bodies.** They render in a sandboxed iframe with a deny-all policy. The sandbox is the boundary and the sanitizer is defence in
  depth: preserve that order.
- **The page.** It never keeps its own copy of a closed set or a limit the server owns; run `just gen-api` when a schema, route or limit
  changes, and commit the diff on its own. Never disable `react-hooks/exhaustive-deps` or `rules-of-hooks`. Only
  `features/mailbox/MailFrame.tsx` writes HTML into the DOM.
- **Tests.** Every test sees only fictional data (`example.test`) in a temp directory, and a tripwire fails any that reaches real data, a
  non-loopback host or the `claude`, `op` and `security` programs. A known bug is an `xfail(strict=True)` test stating the right behaviour.

## Read before you change

| You're touching | Read |
| --- | --- |
| Tests, fixtures, goldens, what `just verify` runs | [docs/testing.md](docs/testing.md) |
| The page, its generated types, ESLint rules | [docs/frontend.md](docs/frontend.md) |
| The layers, `platform`, `kernel`, `content`, `bootstrap` | [docs/architecture.md](docs/architecture.md) |
| One context's model and adapters | [docs/contexts.md](docs/contexts.md) |
| Routes, schemas, problem documents | [docs/routing.md](docs/routing.md) |
| The database, migrations, upgrades | [docs/migrations.md](docs/migrations.md) |
| IMAP gates, workers, IDLE, the pool | [docs/concurrency.md](docs/concurrency.md) |
| Moves, flags, Undo, the outbox, drafts, offline | [docs/writes-and-undo.md](docs/writes-and-undo.md) |
| The perimeter, TLS, the mail frame | [docs/security.md](docs/security.md) |
| Claude calls, the cost governor, batches | [docs/ai.md](docs/ai.md) |
| The classifier, rules, unsubscribe | [docs/classification.md](docs/classification.md) |

## Conventions

- **Don't create `*.bak-*` snapshot files.** The repo used to carry 38 of them; they are
  now gitignored. Use a git branch.
- **No inline scripts or event handlers in the page.** Its content security policy lets script files from this
  server run and nothing else, so an `onclick=` or a `<script>` block in `web/index.html` is a violation.
- **Keep the page's files free of raw control bytes.** A single literal NUL byte once
  made `grep` and `ripgrep` treat the file as binary and silently return nothing past it,
  hiding half the frontend from every search tool. Use escape sequences in string
  literals.
- **`ROADMAP.md` lists only work that is still open.** When something ships, summarize it
  into the README's Features section and delete it from the roadmap — no "✅ DONE"
  entries. Verify status against the code, not the doc, and cite functions and symbols
  rather than line numbers (they drift).
