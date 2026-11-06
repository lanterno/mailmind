# MailMind — common commands. `just` with no argument lists everything.
# Everything runs through uv, which installs the runtime packages and dev tools on first use, and
# through `python -m mailmind`, which says what each command does. `run-old` is the one exception:
# the old front door, `python3 server.py`, needs nothing installed, and it's the kill switch.

py := "uv run python"

_default:
    @just --list --unsorted

# Install everything the checks and the React app use: the Python packages, node_modules, Chromium
setup: _web-deps
    uv sync
    uv run playwright install chromium

# node_modules from the lockfile, when they are missing or older than it
_web-deps:
    cd web && (test node_modules/.package-lock.json -nt package-lock.json || npm ci --no-audit --no-fund)

# ── run ───────────────────────────────────────────────────────────────────────

# Run the app (default http://127.0.0.1:8721)
run port="":
    {{py}} -m mailmind serve {{port}}

# Build the React app, then run; it's at http://127.0.0.1:8721/next, and doesn't exist unbuilt
run-next port="": build-web
    {{py}} -m mailmind serve {{port}}

# Run read-only: nothing written to a mail server (sends and moves wait), no background sync or AI
dev port="":
    MAILMIND_PROFILE=read-only {{py}} -m mailmind serve {{port}}

# Run with persistent IMAP connections + multi-folder sync (both default-off)
run-fast port="":
    MAILMIND_POOL=1 MAILMIND_WARM_FOLDERS=1 {{py}} -m mailmind serve {{port}}

# Run through the old front door, `python3 server.py`, which needs nothing installed
run-old port="":
    python3 server.py {{port}}

# ── checks ────────────────────────────────────────────────────────────────────

# Everything a change should pass before committing
verify: lint compile test check-frontend check-web

# Every test but the browser's and Dovecot's. Pick some with `just test -k hardening`
test *args:
    uv run pytest {{args}}

# Every test, the browser's included
test-full *args: setup
    uv run pytest --browser {{args}}

# Only the browser tests, which drive the page and the React app in Chromium through both front doors
test-browser *args: setup
    uv run pytest --browser tests/e2e/browser {{args}}

# The MailSession contract against a real IMAP server, Dovecot in Docker, through each adapter.
# Needs Docker running; the first run pulls the image.
test-dovecot *args:
    uv run pytest --dovecot tests/contract/test_mail_session_dovecot.py {{args}}

# Undefined names, unused imports and pyflakes' other checks, run by ruff; then the import rules
# between the mailmind package's layers (import-linter; the contracts are in pyproject.toml), then types (pyright)
lint:
    uv run ruff check
    uv run lint-imports --no-cache --no-logo
    uv run pyright

# Byte-compile every module, and the mailmind package, without running them
compile:
    {{py}} -m py_compile *.py && {{py}} -m compileall -q mailmind && echo "compile ok"

# Syntax-check the page's scripts (needs node), balance its CSS, and reject an inline script or raw control bytes
check-frontend:
    #!/usr/bin/env python3
    import glob, re, subprocess, sys
    html, scripts, styles = "web/index.html", glob.glob("web/assets/legacy/*.js"), glob.glob("web/assets/styles/*.css")
    for path in (html, *scripts, *styles):
        raw = open(path, "rb").read()
        bad = [i for i, b in enumerate(raw) if b < 0x09 or b in (0x0b, 0x0c) or 0x0e <= b < 0x20]
        if bad:
            sys.exit(f"raw control byte(s) in {path} at offset(s) {bad[:5]}: they make grep treat the file as binary")
    if re.search(r"<script(?![^>]*\bsrc=)", open(html, encoding="utf-8").read()):
        sys.exit(f"an inline script in {html}: the page's content security policy runs script files only")
    for path in scripts:
        r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"JS syntax error in {path}:\n{r.stderr}")
    for path in styles:
        css = open(path, encoding="utf-8").read()
        if css.count("{") != css.count("}"):
            sys.exit(f"unbalanced CSS braces in {path}")
    print(f"frontend ok: {len(scripts)} scripts parse, {len(styles)} stylesheets balanced, no inline script, no control bytes")

# Type-check the TypeScript, run its tests, and check that the page's generated script is up to date (needs node)
check-web: _web-deps
    cd web && npx tsc --noEmit && npx vitest run --silent && node scripts/gen-legacy.mjs --check

# ── web ───────────────────────────────────────────────────────────────────────

# Build the page's files and the React app, named by their content, into mailmind/http/dist (needs node).
# The page runs without this; the React app doesn't.
build-web: _web-deps
    cd web && npx vite build

# Write the pure TypeScript modules as the classic script the page runs with no build
gen-web:
    node web/scripts/gen-legacy.mjs

# Write web/src/lib/api/types.gen.ts from the JSON answers the whole test suite sees (about 7 minutes)
gen-api:
    #!/usr/bin/env bash
    set -euo pipefail
    notes="$(mktemp)"
    MAILMIND_RECORD_API="$notes" uv run pytest -q -p no:randomly
    uv run python tests/gen_api_types.py "$notes" web/src/lib/api/types.gen.ts
    rm -f "$notes"

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

# Move junk to Trash. Mutates real mailboxes; logged to sweep_audit.jsonl
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
