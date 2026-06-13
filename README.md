# MailMind

**A local-first, dependency-free email-intelligence dashboard.** MailMind logs
into your mailboxes over IMAP, throws away the junk, AI-summarizes everything
that matters, and gives you a fast two-pane dashboard to read and curate — so
you read *summaries of what's important*, not your inbox.

- **No `pip install`.** Pure Python standard library + one HTML file.
- **Your data stays local.** Mail is stored in a local SQLite file; the only
  thing that leaves your machine is the text you send to the `claude` CLI for
  summarization.
- **Any mailbox.** Gmail/Workspace, generic IMAP (cPanel/Dovecot/…), and Proton
  (via Bridge) — all configured in one JSON file.
- **It learns.** A keep/block rules layer remembers your decisions so the same
  sender is never re-triaged.

> **Heads-up:** summaries are produced by the local [`claude`](https://docs.claude.com/en/docs/claude-code) CLI on **your** subscription. Without it installed and authenticated, MailMind still ingests and curates mail — it just shows no AI summaries.

---

## How it works

```
mailmind.config.json          (your accounts + how to read their passwords)
        │
   config.py / accounts.py     resolve credentials, open IMAP connections
        │
   ingest.py                   pull INBOX headers + bodies, skip obvious junk
        │
   mailclass.py                classify each message (spam / marketing /
        │                      notifications / receipts / personal / protected)
   store.py  →  SQLite (mailmind.db)
        │
   summarize.py                summarize + score importance 1–5 via `claude -p`
        │
   server.py  +  dashboard.html        two-pane dashboard on http://127.0.0.1:8721
```

The dashboard is a dense **priority list** (left) plus a **reader** pane (right),
with instant search, category filters, a ⚡ To-do focus, per-account stats, and
one-key triage. Curation tools (sweep junk, keep/block rules, reconcile, audit)
live in a slide-in drawer.

## Prerequisites

- **Python 3.10+** (developed/tested on 3.14). No third-party packages.
- **The `claude` CLI** on your `PATH`, installed and authenticated — required
  for summaries (it shells out to `claude -p … --output-format text` and uses
  your Claude subscription). Set `MAILMIND_MODEL` to pick a cheaper/faster model.
- **Optional:** the [`op`](https://developer.1password.com/docs/cli/) (1Password)
  CLI — only if you want to keep passwords in 1Password instead of inline.
- **Optional:** [Proton Mail Bridge](https://proton.me/mail/bridge) — only if you
  add a Proton account (it must be installed and running).

## Quickstart

```bash
git clone https://github.com/<you>/mailmind.git && cd mailmind

# 1. Configure ONE account to start (delete the others in the example)
cp mailmind.config.example.json mailmind.config.json
$EDITOR mailmind.config.json

# 2. Verify the credentials resolve (prints ok/fail per account, no secrets)
python3 config.py check

# 3. Pull recent mail, then summarize it
python3 ingest.py all 90       # last 90 days (use a big number like 3650 for all-time)
python3 summarize.py 2000 3    # summarize up to 2000, 3 workers

# 4. Run the dashboard
python3 server.py              # http://127.0.0.1:8721   (custom port: python3 server.py 9000)
```

## Configuring accounts

Each account in `mailmind.config.json` has a `type` and a `credential`. There
are **two ways** to supply the password — mix freely per account:

```jsonc
{
  "accounts": [
    // Self-contained (no extra tools): put the password right in the file.
    { "email": "you@gmail.com", "type": "gmail", "credential": "abcd efgh ijkl mnop" },

    // Generic hosted IMAP (set host/port; port 143 + "starttls": true for STARTTLS).
    { "email": "you@company.com", "type": "imap", "host": "mail.yourhost.com",
      "port": 993, "trash": "Trash", "credential": { "plain": "your-mailbox-password" } },

    // Keep the secret out of the file — read it from 1Password or an env var.
    { "email": "secure@company.com", "type": "imap", "host": "mail.yourhost.com",
      "credential": { "op": "op://Private/Your Mail Item/password" } },

    // Proton — via the local Bridge.
    { "email": "you@proton.me", "type": "proton", "credential": { "proton_bridge": true } }
  ]
}
```

Credential styles: `"a-string"` or `{"plain": "…"}` (inline) · `{"op": "op://Vault/Item/Field"}`
(1Password) · `{"env": "VAR_NAME"}` (environment variable) · `{"proton_bridge": true}`.

**Per-type notes**
- **Gmail / Workspace** — you need an **App Password** (Google account → 2-Step
  Verification → App passwords), *not* your login password. Defaults to
  `imap.gmail.com:993`, trash `[Gmail]/Trash`.
- **Generic IMAP** — set `host`/`port`. Default is `:993` direct TLS; use
  `"port": 143, "starttls": true` for STARTTLS. The **trash folder name matters** —
  some servers use `INBOX.Trash` instead of `Trash`; a wrong name silently breaks
  sweeps. Run `python3 manage.py inventory you@company.com` to see the real names.
- **Proton** — install and run Proton Mail Bridge, then supply the Bridge
  password via `MAILMIND_PROTON_BRIDGE_PASS`, a 1Password item
  (`MAILMIND_PROTON_OP_ITEM`), or `~/.config/mailmind/proton_bridge_pass`
  (chmod 600). STARTTLS to `127.0.0.1` skips cert verification by design.

## Two ways to use it

**Web dashboard / daemon** — `python3 server.py [port]`. Auto-syncs (incremental
ingest + summarize) every 5 minutes. `MAILMIND_NOSYNC=1 python3 server.py` serves
read-only (no background sync).

**CLI curation** (`manage.py`) — works across every account:

```bash
python3 manage.py status              # INBOX + Trash counts
python3 manage.py inventory all       # every folder + message/unseen counts
python3 manage.py plan all            # classify the live inbox, show what would be trashed
python3 manage.py sweep you@x.com --dry   # preview a junk sweep (ALWAYS dry-run first)
python3 manage.py sweep you@x.com         # move junk → Trash (verified)
python3 manage.py keep example.com        # learned rule: always keep this sender/domain
python3 manage.py block spammer.com       # always trash (writes a rule)
python3 manage.py rules                    # show the learned lists
```

> ⚠️ **`sweep` and `empty-trash` mutate real mailboxes** (move to Trash / purge).
> Always `--dry` first. Every sweep/reconcile is logged to `sweep_audit.jsonl` /
> `reconcile_audit.jsonl`. Moves go to Trash and are recoverable; Gmail purges
> its Trash after 30 days.

## Environment variables

| Variable | Effect |
|---|---|
| `MAILMIND_NOSYNC=1` | Serve the dashboard read-only (no background sync) |
| `MAILMIND_MODEL` | Override the Claude model for summaries (e.g. `claude-haiku-4-5`) |
| `MAILMIND_PROTON_BRIDGE_PASS` | Proton Bridge password (highest precedence) |
| `MAILMIND_PROTON_OP_ITEM` | 1Password item id holding the Bridge password |

Server port is the first CLI arg to `server.py` (default `8721`).

## Where your data lives (all local, all git-ignored)

- `mailmind.db` — SQLite store of messages + summaries.
- `mailmind_rules.json` — your learned keep/block lists.
- `sweep_audit.jsonl` / `reconcile_audit.jsonl` — audit trails.
- `mailmind.config.json` — your accounts + credentials.

All of the above are in `.gitignore`. **Nothing personal is committed.**

## Testing

```bash
python3 tests.py     # behavioral classifier suite; exits non-zero on failure
```

## Privacy & contributing

This is a personal-data app. **Never commit** `mailmind.config.json`,
`mailmind_rules.json` (it holds your personal keep/block entries), `mailmind.db`,
the audit `*.jsonl` files, or screenshots of real mail — the `.gitignore` covers
them; don't `git add -f` around it. PRs welcome; keep it dependency-free.

## License

MIT — see [LICENSE](LICENSE).
