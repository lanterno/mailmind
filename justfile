# MailMind — common commands. `just` with no argument lists everything.
# Everything runs through uv, which installs the runtime packages and dev tools on first use, and
# through `uv run python -m mailmind`, which says what each command does.

py := "uv run python"

_default:
    @just --list --unsorted

# Install everything the checks and the app use: the Python packages, node_modules, Chromium, osv-scanner
setup: _web-deps
    @command -v cargo >/dev/null || { echo 'Rust builds the leaves the app shares with the core: install it from https://rustup.rs' >&2; exit 1; }
    uv sync
    uv run playwright install chromium
    command -v osv-scanner >/dev/null || brew install osv-scanner

# node_modules from the lockfile, when they are missing or older than it
_web-deps:
    cd web && (test node_modules/.package-lock.json -nt package-lock.json || npm ci --no-audit --no-fund)

# ── run ───────────────────────────────────────────────────────────────────────

# Build the app, then run it (default http://127.0.0.1:8721)
run port="": build-web
    {{py}} -m mailmind serve {{port}}

# The whole app on fictional mail, with fake servers and a temp data directory: nothing of yours is read or changed
demo port="": build-web
    {{py}} tests/support/demo.py {{port}}

# Run read-only: nothing written to a mail server (sends and moves wait), no background sync or AI
dev port="": build-web
    MAILMIND_PROFILE=read-only {{py}} -m mailmind serve {{port}}

# Run with persistent IMAP connections + multi-folder sync (both default-off)
run-fast port="": build-web
    MAILMIND_POOL=1 MAILMIND_WARM_FOLDERS=1 {{py}} -m mailmind serve {{port}}

# ── checks ────────────────────────────────────────────────────────────────────

# Everything a change should pass before committing
verify: lint compile test check-frontend check-web core-verify test-rust audit

# Every test but the browser's and Dovecot's, a process for each core (pytest-xdist). Pick some with `just test -k hardening`;
# `just test -n0` runs them in this one process
test *args:
    uv run pytest -n auto {{args}}

# Every test, the browser's included, four processes at a time: each runs a Chromium, which keeps cores of its own busy, and
# with a process for each core the browser tests' waits run out. `just test-full -n0` runs them in one
test-full *args: setup
    uv run pytest -n 4 --browser {{args}}

# Only the browser tests, which drive the app in Chromium, four processes at a time as test-full runs them
test-browser *args: setup
    uv run pytest -n 4 --browser tests/e2e/browser {{args}}

# The MailSession contract against a real IMAP server, Dovecot in Docker, through each adapter.
# Needs Docker running; the first run pulls the image. One process, as one container serves the run.
test-dovecot *args:
    uv run pytest --dovecot tests/contract/test_mail_session_dovecot.py {{args}}

# The served tests on the Rust server's ledger (tests/support/rust_ledger.txt), against the Rust server: mm-served, the
# launcher that runs core/crates/mm-server behind the suites' line protocol
test-rust *args:
    #!/usr/bin/env bash
    set -euo pipefail
    cargo build -q --manifest-path core/Cargo.toml -p mm-served
    export MAILMIND_TEST_SERVER="$PWD/core/target/debug/mm-served" MAILMIND_TEST_LEDGER="$PWD/tests/support/rust_ledger.txt"
    listed=()
    while IFS= read -r test; do listed+=("$test"); done < <(grep -v '^#' tests/support/rust_ledger.txt | grep .)
    uv run pytest -n 4 --browser "${listed[@]}" {{args}}

# Every served test (tests/api and tests/e2e/browser) against the Rust server, with those off the ledger
# expected to fail: one that passes errors with XPASS(strict), and goes on the ledger
test-rust-all *args: setup
    #!/usr/bin/env bash
    set -euo pipefail
    cargo build -q --manifest-path core/Cargo.toml -p mm-served
    export MAILMIND_TEST_SERVER="$PWD/core/target/debug/mm-served" MAILMIND_TEST_LEDGER="$PWD/tests/support/rust_ledger.txt"
    uv run pytest -n 4 --browser -rfE tests/api tests/e2e/browser {{args}}

# Undefined names, unused imports and pyflakes' other checks, run by ruff; then the import rules
# between the mailmind package's layers (import-linter; the contracts are in pyproject.toml), then types (pyright)
lint:
    uv run ruff check
    uv run lint-imports --no-cache --no-logo
    uv run pyright

# Byte-compile the mailmind package without running it
compile:
    {{py}} -m compileall -q mailmind && echo "compile ok"

# Reject raw control bytes in the app's source and an inline script in its page, parse theme.js (needs node) and balance the CSS
check-frontend:
    #!/usr/bin/env python3
    import glob, re, subprocess, sys
    html, theme, styles = "web/index.html", "web/assets/theme.js", glob.glob("web/assets/styles/*.css") + glob.glob("web/src/**/*.css", recursive=True)
    code = glob.glob("web/src/**/*.ts", recursive=True) + glob.glob("web/src/**/*.tsx", recursive=True)
    for path in (html, theme, *styles, *code):
        raw = open(path, "rb").read()
        bad = [i for i, b in enumerate(raw) if b < 0x09 or b in (0x0b, 0x0c) or 0x0e <= b < 0x20]
        if bad:
            sys.exit(f"raw control byte(s) in {path} at offset(s) {bad[:5]}: they make grep treat the file as binary")
    if re.search(r"<script(?![^>]*\bsrc=)", open(html, encoding="utf-8").read()):
        sys.exit(f"an inline script in {html}: the page's content security policy runs script files only")
    r = subprocess.run(["node", "--check", theme], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"JS syntax error in {theme}:\n{r.stderr}")
    for path in styles:
        css = open(path, encoding="utf-8").read()
        if css.count("{") != css.count("}"):
            sys.exit(f"unbalanced CSS braces in {path}")
    print(f"frontend ok: theme.js parses, {len(styles)} stylesheets balanced, no inline script, no control bytes")

# Check the API's types still match the routes' schemas, then type-check the TypeScript, lint it and run its tests (needs node)
check-web: _web-deps
    #!/usr/bin/env bash
    set -euo pipefail
    fresh="$(mktemp -d)"
    trap 'rm -rf "$fresh"' EXIT
    {{just_executable()}} _api-types "$fresh"
    for gen in schema.gen.ts limits.gen.ts; do
        if ! cmp -s "$fresh/$gen" "web/src/lib/api/$gen"; then
            echo "web/src/lib/api/$gen doesn't match the server's: run just gen-api, and review the diff" >&2
            exit 1
        fi
    done
    cd web && npx tsc --noEmit && npx eslint --max-warnings 0 && npx vitest run --silent

# ── web ───────────────────────────────────────────────────────────────────────

# Known vulnerabilities in what the lockfiles pin. With no network it says so and passes.
audit:
    #!/usr/bin/env sh
    set -eu
    if ! curl -sS -m 5 -o /dev/null https://api.osv.dev/; then
        echo "audit: skipped, no network"
        exit 0
    fi
    command -v osv-scanner >/dev/null || { echo "audit: osv-scanner is missing: brew install osv-scanner" >&2; exit 1; }
    req=$(mktemp)
    trap 'rm -f "$req"' EXIT
    # Not the leaves built from core/crates/mm-py, which has no hash to check: their Rust is in core/Cargo.lock below.
    uv export --frozen --format requirements-txt --all-groups --no-emit-project --no-emit-local -q -o "$req"
    uv run pip-audit --strict --require-hashes --disable-pip --progress-spinner off -r "$req"
    osv-scanner scan source --lockfile uv.lock --lockfile web/package-lock.json --lockfile core/Cargo.lock \
        --lockfile shells/web/package-lock.json --lockfile shells/desktop/package-lock.json \
        --lockfile shells/desktop/src-tauri/Cargo.lock

# Build the app, its files named by their content, into mailmind/http/dist (needs node). It doesn't exist without this:
# with nothing built, `/` answers 404 and says to run this.
build-web: _web-deps
    cd web && npx vite build

# Write web/src/lib/api/schema.gen.ts, the API's types, from the routes' schemas: their OpenAPI document, through openapi-typescript;
# and web/src/lib/api/limits.gen.ts, the limits the page holds a person to, from mailmind/http/page_limits.py
gen-api: _web-deps
    {{just_executable()}} _api-types web/src/lib/api

# The API's types and the page's limits, written into `dir`: tests/support/openapi_doc.py writes the document and limits.gen.ts, and
# openapi-typescript (web/gen-api) reads the document
_api-types dir:
    #!/usr/bin/env bash
    set -euo pipefail
    doc="$(mktemp -d)"
    trap 'rm -rf "$doc"' EXIT
    uv run python tests/support/openapi_doc.py "$doc/openapi.json" "{{dir}}/limits.gen.ts"
    web/gen-api/node_modules/.bin/openapi-typescript "$doc/openapi.json" -o "{{dir}}/schema.gen.ts" --default-non-nullable false

# ── mail ──────────────────────────────────────────────────────────────────────

# Verify credentials resolve, per account (prints ok/fail, never secrets)
check-config:
    {{py}} -m mailmind check

# The connected accounts
accounts:
    {{py}} -m mailmind accounts

# Pull mail.  just sync all 90   |   just sync you@x.com 30
sync target="all" days="90":
    {{py}} -m mailmind sync {{target}} {{days}}

# Summarize mail with no summary yet, at full price (with no API key, AI is off)
assess limit="2000" workers="3":
    {{py}} -m mailmind assess {{limit}} {{workers}}

# Send the mail with no summary yet as a batch, at half price; run again to bring in its answers
backfill:
    {{py}} -m mailmind assess --batch

# INBOX + Trash counts
status target="all":
    {{py}} -m mailmind status {{target}}

# Every folder with message/unseen counts
inventory target="all":
    {{py}} -m mailmind inventory {{target}}

# Classify the live inbox and show what a sweep would trash
plan target="all":
    {{py}} -m mailmind plan {{target}}

# Preview a junk sweep — ALWAYS run this before `sweep`
sweep-dry target="all":
    {{py}} -m mailmind sweep {{target}} --dry

# Move junk to Trash. Mutates real mailboxes; recorded in the audit trail, in the database
sweep target:
    {{py}} -m mailmind sweep {{target}}

# Learned rules
rules:
    {{py}} -m mailmind rules

# Always keep a sender or domain
keep +tokens:
    {{py}} -m mailmind rules keep {{tokens}}

# Always trash a sender or domain
block +tokens:
    {{py}} -m mailmind rules block {{tokens}}

# Forget a keep or block; expiry stays
unrule +tokens:
    {{py}} -m mailmind rules remove {{tokens}}

# Expire a sender/domain's INBOX mail after N hours (0 clears).  just expire-after github.com 72
expire-after token hours:
    {{py}} -m mailmind rules expire {{token}} {{hours}}

# Preview what the expiry rules would move to Trash right now
expire-dry target="all":
    {{py}} -m mailmind expire {{target}} --dry

# Move INBOX mail past its expiry rule to Trash (the server also does this hourly)
expire target="all":
    {{py}} -m mailmind expire {{target}}

# The saved views, built-ins marked *
views:
    {{py}} -m mailmind views

# ── data ──────────────────────────────────────────────────────────────────────

# Try the next start's migrations on a copy of the database: counts and checks only, never contents
db-check-upgrade *args:
    {{py}} -m mailmind db check-upgrade {{args}}

# Give back the room freed in the database file, with MailMind stopped (owner only)
db-compact *args:
    {{py}} -m mailmind db compact {{args}}

# What the Claude API cost each day, by feature, from the usage ledger (`just ai-usage --days 30`)
ai-usage *args:
    {{py}} -m mailmind ai usage {{args}}

# What an export would contain, without writing anything
export-plan:
    {{py}} -m mailmind export --format plan

# Lossless per-message .eml files (read-only; never touches IMAP)
export-eml out="/tmp/mailmind-export":
    {{py}} -m mailmind export --format eml --out {{out}} --attachments

# Standard mbox archives
export-mbox out="/tmp/mailmind-export":
    {{py}} -m mailmind export --format mbox --out {{out}}

# A consistent snapshot of the database
export-db out="/tmp/mailmind-export":
    {{py}} -m mailmind export --format db --out {{out}}

# ── the clients ───────────────────────────────────────────────────────────────
# The Rust core every client runs (core/). Needs Rust: the toolchain is pinned in core/rust-toolchain.toml.
# Each shell has recipes of its own: `just apple`, `just android`, `just web` and `just desktop` list them.

mod? apple 'shells/apple/justfile'
mod? android 'shells/android/justfile'
mod? web 'shells/web/justfile'
mod? desktop 'shells/desktop/justfile'

# The core's checks: formatting, lints as errors, and its tests
core-verify:
    cd core && cargo fmt --all --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace

# Measure the core against budgets.toml on 100,000 fictional messages, in a release build; fails when one is over
budgets:
    cd core && cargo run --release -q -p mm-budgets

# A fictional mailbox at `path`, keyed with the fixture key: `just fixtures /tmp/inbox.db 5000`
fixtures path count="100000":
    cd core && cargo run --release -q -p mm-fixtures -- "{{absolute_path(path)}}" {{count}}

# The core as a program on a fictional mailbox, speaking JSON lines on standard input and output (mm_app::wire)
core-serve count="100000":
    #!/usr/bin/env bash
    set -euo pipefail
    cd core
    db=target/fixtures/inbox-{{count}}.db
    mkdir -p target/fixtures
    test -f "$db" || cargo run --release -q -p mm-fixtures -- "$db" {{count}}
    # The fixture key (mm_fixtures::FIXTURE_KEY_HEX): it guards only fictional mail
    MAILMIND_DB_KEY=f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7f1c7 cargo run --release -q -p mm-daemon -- "$db"

# The Swift and Kotlin bindings from the core, into each shell's generated folder
gen-bindings:
    #!/usr/bin/env bash
    set -euo pipefail
    cd core
    cargo build -q -p mm-ffi
    lib=target/debug/libmailmind_core.dylib
    test -f "$lib" || lib=target/debug/libmailmind_core.so
    cargo run -q -p mm-ffi --bin uniffi-bindgen -- generate --library "$lib" --language swift --out-dir ../shells/apple/Generated
    cargo run -q -p mm-ffi --bin uniffi-bindgen -- generate --library "$lib" --language kotlin --no-format --out-dir ../shells/android/core/src/main/kotlin
