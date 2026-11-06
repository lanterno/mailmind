# MailMind — common commands.  `just` with no argument lists everything.
# The app runs through its front door, `python -m mailmind serve` (FastAPI on uvicorn); `uv run`
# installs its packages on first use, and the dev tools that lint and tests use. `run-old` runs the
# old front door, `python3 server.py`, which needs nothing installed: the kill switch.

py := "python3"

_default:
    @just --list --unsorted

# ── run ───────────────────────────────────────────────────────────────────────

# Run the dashboard (default http://127.0.0.1:8721)
run port="":
    uv run python -m mailmind serve {{port}}

# Run read-only: nothing written to a mail server (sends and moves wait), no background sync or AI
dev port="":
    MAILMIND_PROFILE=read-only uv run python -m mailmind serve {{port}}

# Run with persistent IMAP connections + multi-folder sync (both default-off)
run-fast port="":
    MAILMIND_POOL=1 MAILMIND_WARM_FOLDERS=1 uv run python -m mailmind serve {{port}}

# Run through the old front door, `python3 server.py`, which needs nothing installed
run-old port="":
    {{py}} server.py {{port}}

# ── checks ────────────────────────────────────────────────────────────────────

# Every test suite, under pytest; each script suite runs alone, as `python3 <suite>.py` does
test *args:
    uv run pytest {{args}}

# Every suite plus the browser tests, which drive the page in Chromium (Playwright).
# The first run downloads Playwright's Chromium; later runs find it cached.
test-full *args:
    uv run playwright install chromium
    uv run pytest --browser {{args}}

# The MailSession contract against a real IMAP server, Dovecot in Docker, through each adapter.
# Needs Docker running; the first run pulls the image.
test-dovecot *args:
    uv run pytest --dovecot tests/test_mail_session_dovecot.py {{args}}

# Classifier behaviour
test-classifier:
    {{py}} tests.py

# HTML sanitizer: 24 attack classes, expects 0 leaks
test-sanitize:
    {{py}} tests_sanitize.py

# CSS sanitizer unit tests
test-css:
    {{py}} test_css_sanitizer.py

# Expiry rules: storage, sender/domain matching, age selection
test-expire:
    {{py}} test_expire.py

# Whole-conversation archive/trash: membership, one undo group, partial failure, offline queue
test-thread-file:
    uv run pytest tests/test_thread_filing.py

# The language model's contract, through the SDK and through urllib, against the fake Claude API:
# request shape, refusals, retries, the key, AI off without one
test-claude-api:
    uv run pytest tests/test_ai_contract.py

# Today & next: which invites in fetched mail are listed, updates, cancellations, local times
test-upcoming:
    {{py}} test_upcoming.py

# Composer "new to you" lookup: who counts as known, exact matching, drafts never vouch, CSRF
test-contacts-known:
    {{py}} test_contacts_known.py

# Send-later times: a passed, far-off or malformed one is refused before anything is built
test-send-after:
    {{py}} test_send_after.py

# Reopening a saved draft: split into your text and its quote, sent with each part once
test-draft-resume:
    {{py}} test_draft_resume.py

# Reopening a saved draft in the composer: options, payload fields, notes (needs node)
test-draft-resume-ui:
    {{py}} test_draft_resume_ui.py

# Phase 0 hardening: live security and data-loss fixes, one test per fix
test-hardening:
    {{py}} test_hardening.py

# Send-time checks in the composer: attachment wording, typed text, subject, reply-all size (needs node)
test-send-guard:
    {{py}} test_send_guard.py

# Undefined names, unused imports and pyflakes' other checks, run by ruff; then the import rules
# between the mailmind package's layers (import-linter; the contracts are in pyproject.toml)
lint:
    uv run ruff check
    uv run lint-imports --no-cache --no-logo

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

# Build the page's files, named by their content, into mailmind/http/dist (needs node). The app runs without this.
build-web:
    cd web && npm ci --no-audit --no-fund && npx vite build

# Type-check the TypeScript, run its tests, and check that the page's generated script is up to date (needs node)
check-web:
    cd web && (test -d node_modules || npm ci --no-audit --no-fund) && npx tsc --noEmit && npx vitest run --silent && node scripts/gen-legacy.mjs --check

# Write the pure TypeScript modules as the classic script the page runs with no build
gen-web:
    node web/scripts/gen-legacy.mjs

# Write web/src/api/types.gen.ts from the JSON answers the whole test suite sees (about 7 minutes)
gen-api:
    #!/usr/bin/env bash
    set -euo pipefail
    notes="$(mktemp)"
    MAILMIND_RECORD_API="$notes" uv run pytest -q -p no:randomly
    uv run python tests/gen_api_types.py "$notes" web/src/api/types.gen.ts
    rm -f "$notes"

# Everything a change should pass before committing
verify: lint compile test check-frontend check-web

# ── mail ──────────────────────────────────────────────────────────────────────

# Verify credentials resolve, per account (prints ok/fail, never secrets)
check-config:
    {{py}} config.py check

# Pull mail.  just ingest all 90   |   just ingest you@x.com 30
ingest target="all" days="90":
    {{py}} ingest.py {{target}} {{days}}

# Summarize pending messages with Claude (the API; with no key, AI is off)
summarize limit="2000" workers="3":
    {{py}} summarize.py {{limit}} {{workers}}

# Send the mail with no summary yet as a batch, at half price; run again to bring in its answers
backfill:
    {{py}} -m mailmind assess --batch

# INBOX + Trash counts
status target="all":
    {{py}} manage.py status {{target}}

# Every folder with message/unseen counts
inventory target="all":
    {{py}} manage.py inventory {{target}}

# Classify the live inbox and show what a sweep would trash
plan target="all":
    {{py}} manage.py plan {{target}}

# Preview a junk sweep — ALWAYS run this before `sweep`
sweep-dry target="all":
    {{py}} manage.py sweep {{target}} --dry

# Move junk to Trash. Mutates real mailboxes; logged to sweep_audit.jsonl
sweep target:
    {{py}} manage.py sweep {{target}}

# Learned rules
keep +tokens:
    {{py}} manage.py keep {{tokens}}

block +tokens:
    {{py}} manage.py block {{tokens}}

rules:
    {{py}} manage.py rules

# Expire a sender/domain's INBOX mail after N hours (0 clears).  just expire-after github.com 72
expire-after token hours:
    {{py}} rules.py expire {{token}} {{hours}}

# Preview what the expiry rules would move to Trash right now
expire-dry target="all":
    {{py}} manage.py expire {{target}} --dry

# Move INBOX mail past its expiry rule to Trash (the server also does this hourly)
expire target="all":
    {{py}} manage.py expire {{target}}

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
    {{py}} export.py --format plan

# Lossless per-message .eml files (read-only; never touches IMAP)
export-eml out="/tmp/mailmind-export":
    {{py}} export.py --format eml --out {{out}} --attachments

# Standard mbox archives
export-mbox out="/tmp/mailmind-export":
    {{py}} export.py --format mbox --out {{out}}
