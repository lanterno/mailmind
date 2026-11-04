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

# Every suite plus the browser tests, which drive dashboard.html in Chromium (Playwright).
# The first run downloads Playwright's Chromium; later runs find it cached.
test-full *args:
    uv run playwright install chromium
    uv run pytest --browser {{args}}

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
    {{py}} test_thread_file.py

# Claude API engine against a local stub: request shape, refusals, retries, AI off without a key
test-claude-api:
    {{py}} test_claude_api.py

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

# Syntax-check the inline JS in dashboard.html (needs node) and reject raw control bytes
check-frontend:
    #!/usr/bin/env python3
    import re, subprocess, tempfile, os, sys
    raw = open("dashboard.html", "rb").read()
    bad = [i for i, b in enumerate(raw) if b < 0x09 or b in (0x0b, 0x0c) or 0x0e <= b < 0x20]
    if bad:
        sys.exit(f"raw control byte(s) at offset(s) {bad[:5]} — they make grep treat the file as binary")
    s = raw.decode("utf-8")
    blocks = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', s, re.S)
    for i, b in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(b); p = f.name
        r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
        os.unlink(p)
        if r.returncode:
            sys.exit(f"JS syntax error in script block {i}:\n{r.stderr}")
    for i, blk in enumerate(re.findall(r'<style[^>]*>(.*?)</style>', s, re.S)):
        if blk.count("{") != blk.count("}"):
            sys.exit(f"unbalanced CSS braces in style block {i}")
    print(f"frontend ok — {len(blocks)} script blocks parse, CSS balanced, no control bytes")

# Everything a change should pass before committing
verify: lint compile test check-frontend

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

# Keep summarizing until nothing is pending, backing off on rate limits
backfill:
    scripts/backfill.sh

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

# What an export would contain, without writing anything
export-plan:
    {{py}} export.py --format plan

# Lossless per-message .eml files (read-only; never touches IMAP)
export-eml out="/tmp/mailmind-export":
    {{py}} export.py --format eml --out {{out}} --attachments

# Standard mbox archives
export-mbox out="/tmp/mailmind-export":
    {{py}} export.py --format mbox --out {{out}}
