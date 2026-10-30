# MailMind — common commands.  `just` with no argument lists everything.
# Stdlib-only project: there is nothing to install and nothing to build.

py := "python3"

_default:
    @just --list --unsorted

# ── run ───────────────────────────────────────────────────────────────────────

# Run the dashboard (default http://127.0.0.1:8721)
run port="":
    {{py}} server.py {{port}}

# Run read-only: no background sync, no IMAP writes, no AI pre-drafting
dev port="":
    MAILMIND_NOSYNC=1 {{py}} server.py {{port}}

# Run with persistent IMAP connections + multi-folder sync (both default-off)
run-fast port="":
    MAILMIND_POOL=1 MAILMIND_WARM_FOLDERS=1 {{py}} server.py {{port}}

# ── checks ────────────────────────────────────────────────────────────────────

# Every test suite; exits non-zero on the first failure
test: test-classifier test-sanitize test-css test-expire test-thread-file test-claude-api

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

# Claude API engine against a local stub: request shape, refusals, retries, engine choice
test-claude-api:
    {{py}} test_claude_api.py

# Undefined names and unused imports. Fails on anything not in the known baseline.
lint:
    #!/usr/bin/env python3
    import subprocess, sys
    # Known and accepted. readmutq's lambda closes over an `except ... as e` name, which
    # Python unbinds at block exit — safe only because _commit invokes it synchronously.
    # Fixing it is a behaviour change, not a lint fix. Remove this entry if that changes.
    BASELINE = {"readmutq.py:105:67: undefined name 'e'"}
    out = subprocess.run(["pyflakes", *__import__("glob").glob("*.py")],
                         capture_output=True, text=True)
    lines = [l for l in (out.stdout + out.stderr).splitlines() if l.strip()]
    new = [l for l in lines if l not in BASELINE]
    for l in new:
        print(l)
    if new:
        sys.exit(f"{len(new)} new pyflakes finding(s)")
    print(f"lint ok — 0 new findings ({len(BASELINE & set(lines))} known baseline)")

# Byte-compile every module without running it
compile:
    {{py}} -m py_compile *.py && echo "compile ok"

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

# Summarize pending messages with Claude (the API if a key is set, else the claude CLI)
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

# What an export would contain, without writing anything
export-plan:
    {{py}} export.py --format plan

# Lossless per-message .eml files (read-only; never touches IMAP)
export-eml out="/tmp/mailmind-export":
    {{py}} export.py --format eml --out {{out}} --attachments

# Standard mbox archives
export-mbox out="/tmp/mailmind-export":
    {{py}} export.py --format mbox --out {{out}}
