# MailMind

**A local-first, dependency-free email client with an AI layer.** MailMind logs
into your mailboxes over IMAP, throws away the junk, AI-summarizes everything
that matters, and gives you a fast dashboard to read, triage, search and *send* —
so you read *summaries of what's important*, not your inbox.

It started as a summarizer with a reading pane and is now a full mail client:
compose and SMTP send with a durable outbox and Undo Send, conversation
threading, attachments, full-text search with an operator grammar, IMAP IDLE
push, snooze/mute/waiting, and a set of AI actions that never fire on their own.
See [Features](#features) for what ships today and
[ROADMAP.md](ROADMAP.md) for what doesn't yet.

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
   server.py  +  dashboard.html        the app on http://127.0.0.1:8721
```

Around that spine sit the client modules: `mailops.py` (IMAP writes),
`compose.py` + `smtpsend.py` + `outbox.py` (send), `search.py` (FTS + operator
grammar), `threads.py`, `mailparse.py`, `sanitize_html.py` (the reader's
whitelist sanitizer), `ics.py` (invites), `realtime.py` (IMAP IDLE → SSE), and
the AI modules (`summarize*.py`, `draft_reply.py`, `rewrite.py`, `ask.py`).

The app runs in two modes over the same data: a **Briefing** — a dense priority
list plus reader, or a single-stream daily brief — and a **Mailbox** — a real
folder-and-account client. Curation tools (sweep junk, keep/block rules,
reconcile, audit) live in a slide-in drawer.

## Features

MailMind started as a summarizer with a reading pane. It is now a full local-first
mail client — everything below ships today and runs against your local SQLite store
and the `claude` CLI on your own machine.

### Reading

- **A calm reading surface.** Desktop is a two-pane quiet worklist (360px) beside a
  wide reading column, with folders and accounts summoned into an off-canvas drawer;
  `☰ Classic` restores the original three-pane layout and phones stay single-column.
- **The daily brief as a single stream.** Read one message-card at a time with an
  AI narrative intro, clickable topic chips, `j`/`k` navigation and a progress rail
  across the top — or flip to the classic two-pane **List** at any moment. The choice
  is remembered.
- **Messages open instantly.** The reader paints subject, sender and folder from the
  list immediately behind a shimmer skeleton, and escalates to an honest "first open
  of an older message can take a minute" hint if the server is slow.
- **Reading stays instant during triage.** Opening a message quietly warms the next
  one or two bodies — and every member of an open thread — into the local cache.
- **Skimming no longer burns unread.** A message is marked read only after it stays
  open ~1.2 seconds, so fast `j`/`k` scanning leaves the list intact.
- **Newsletters render in their real design.** Inline styles and `<style>` blocks
  survive a whitelist sanitizer while scripts, remote fetches, trackers and
  viewport-curtain phishing are blocked — with an un-spoofable
  `External · domain · Sandboxed · N trackers blocked` strip rendered *outside* the
  frame, and per-link risk checks against the sender's domain.
- **Quoted reply chains fold** behind a `···` toggle in both HTML and plain text, so
  you see the new message, not the thread's whole history.
- **Every message renders in a warm dark surface** that matches the app, with a
  one-click **Original colors** toggle for HTML mail that remembers your choice.
- **Calendar invites become a card.** A `.ics` attachment renders above the message
  as title, local time with timezone, human-readable recurrence, location, organizer
  and attendees, with `＋ Add to calendar` for your OS — and **Accept / Maybe /
  Decline** mails a standards-compliant iMIP reply to the organizer through the
  outbox, so it lands inside the Undo-Send window like any other message.
- **Click any sender for a relationship card** — how many emails they've sent, how
  often you replied, first and last contact, which of your accounts they write to,
  and their recent threads, with quick Compose and search actions. Pure local SQL,
  no enrichment API.

### Triage & organization

- **One-key triage with real undo.** `e` archives *everywhere* (it never trashes),
  destructive verbs live on `#`/Delete/Backspace, and `z` — or a floating Undo
  button — reverses archive, delete, junk, move, snooze and mute, server-side.
- **Bulk triage on demand.** Mark rows with `x` or the row checkboxes, then archive,
  delete, read, unread, star, junk, snooze or mute the whole set as **one** undo group.
- **Snooze, mute, reply-later and Waiting.** A numbered snooze preset popover (plus a
  native date-time picker for custom times) with its own Snoozed folder; a 📌 reply-later
  stack; thread mute that silences future replies at ingest and drops the thread out of
  the inbox while keeping it searchable; and a **Waiting** folder that tracks what you
  owe and are owed ("Sent 3d ago · no reply", "Awaiting your reply") and auto-resolves
  when a reply lands.
- **The Screener.** First-contact senders are gated one decision at a time — Allow
  keeps, Screen-out blocks — and each decision becomes a rule, so the same sender is
  never re-litigated.
- **Purpose-built lanes.** **Feed** reads as a calm newsletter river of summary cards
  with read-and-archive; **Paper trail** reads as a money ledger with extracted amounts,
  refunds and monthly totals.
- **Saved searches as smart folders.** Any search can be pinned to the rail with one
  key. Ships with Unread, Flagged, Has attachments and Last 7 days, plus five
  auto-label lanes — Feed, Paper trail, Notifications, Action needed, Finance —
  which are just saved queries over the existing classifier buckets.
- **One "All inboxes" view** merges every connected account's INBOX by date with
  per-row account chips, replacing the eight-click morning ritual.
- **Document Hub.** Every real attachment across every account in one searchable
  overlay — type facets, live filename/sender/subject search, direct download and
  click-through to the source email, with the same file synced to two accounts merged.
- **Unsubscribe & sender cleanup.** A ranked "senders flooding you" lane where one
  confirm blocks the sender, sweeps their INBOX copies to Trash (one `z` reverses
  both) and fires one standards-compliant unsubscribe.
- **Lists that heal.** Rows collapse and fade as you triage, folder lists and search
  results paginate with **Load 200 more**, capped counts read honestly as `N+`, and
  switching accounts renders the folder rail from cache instead of blanking on a
  live IMAP `LIST`.

### Search

- **Full-text search across every account by default**, with the scope persisted
  between sessions. A polite background worker backfills bodies for mail ingested
  before bodies were stored — yielding instantly to anything you're doing — so search
  covers the whole mailbox, not just recent mail.
- **A safe operator grammar:** `from:` `to:` `cc:` `subject:` `body:`,
  `is:unread|read|flagged|answered|junk|newsletter|receipt|notification`,
  `bucket:` `category:`, `has:attachment`, `in:` `account:`,
  `before:`/`after:`, `"phrases"` and `-exclusion`. `to:`/`cc:` hit real indexed
  recipients rather than the body text. The grammar is advertised in the search
  placeholder and documented in the `?` legend.
- **Search that persists.** Opening a result keeps the results as your working list —
  archive, snooze, mute, star, bulk and swipe all heal the results in place and
  auto-advance to the next hit.

### AI intelligence

Everything here runs through the local `claude` CLI on your subscription and returns
**schema-validated** structured output, so malformed-response errors don't reach the
UI. Nothing is ever sent automatically.

- **Summaries and importance scores** per message, plus a per-thread "catch me up"
  TL;DR with key points.
- **A synthesized "Today" card** above the list, in prose, with quick-jump chips that
  set the filters for you — computed once a day and cached across restarts.
- **"Since you last looked."** The `▾ N new` pill is augmented with a two-to-three
  sentence summary of what actually arrived; the plain count is always the floor.
- **Weekly Review** — an AI retrospective of the last 7 days: sent/received stats, who
  is still waiting on you, who you're waiting on, and three concrete things to start
  next week with. Every item is clickable into the thread.
- **✦ Draft.** One click writes an editable reply into the composer from the whole
  thread. A background pre-drafter can have it ready *before* you press Reply — the
  Reply button reads **✦ Draft ready** — and pre-drafts survive restarts.
- **✦ Rewrite toolbar** over the composer: Shorten, Elaborate, Formal, Friendly, Fix
  grammar, Translate — acting on your selection or the whole draft, with one-step Undo.
- **Ask your inbox.** Prefix a search with `?` and get a grounded plain-English answer
  with cited source cards you can click into. Retrieval is local FTS; citations are
  clamped to messages actually read.
- **Natural-language bulk commands.** Type `!archive all newsletters older than a week`
  and get a mandatory preview — the count, the resolved filter and a sample — before
  anything runs, with blast-radius caps, a second arming click for trash and junk, and
  a single undo. Anything unsafe or ambiguous degrades into an ordinary search that
  touches nothing.
- **Tidy.** One strip offers to file every low-importance info/other message to
  Archive, with a Review mode to inspect them first and one undo to reverse the batch.
  User-triggered, never silent.
- **Commitments.** Every AI-extracted next-step across the inbox as one checkable
  agenda, each row with inline done / later / open and a click into the message.

### Compose & send

- **A durable outbox.** Queued mail survives a restart, retries with backoff, and
  surfaces failures with a Retry button that reopens the composer intact.
- **Undo Send** — a 10-second grace window with a live countdown that pulls the
  message back before it hits the wire and restores everything you'd written.
- **Scheduled send** — pick a preset or an exact time; the row shows
  `Scheduled · <time>` with **Send now** and **Cancel**.
- **Recipient autocomplete** drawn entirely from your own mail history, ranked by how
  often and how recently you've corresponded, fully keyboard-driven.
- **Per-account signatures** with a global default — auto-inserted into new composes
  and live-swapped when you change the From account, without clobbering your edits.
- **Drafts that don't litter.** A compose is only "dirty" once you actually edit the
  subject or body, autosave runs every 8 seconds, and a signature-only body is never
  saved to your Drafts folder.

### Sync, reliability & performance

- **Live updates without a refresh.** An SSE stream pushes new mail, snooze wake-ups,
  mutes and ready AI drafts into the open list behind a sticky `N new` pill that never
  yanks the view mid-triage — with polling as an automatic fallback.
- **Background notifications.** An opt-in 🔔 fires a desktop notification and keeps an
  unread count in the tab title when MailMind isn't focused.
- **Triage survives an outage.** Archive, trash, junk and move queue durably with an
  optimistic local update and an undo, then re-apply exactly once when IMAP returns.
- **Phantom messages clean themselves up.** A background loop reconciles one account
  per tick against the server, yields instantly to anything you're doing, and pushes an
  event so open lists drop messages deleted in another client.
- **Persistent per-account IMAP connections** make repeated reads dramatically faster,
  with connection validation, age/use recycling and extra-conservative handling for
  Proton Bridge's low connection cap.
- **Per-account sync health.** Settings shows last successful sync, sync count, or a
  coarse error kind per connection, with a `↻ Retry` button — plus a dismissible
  banner naming any accounts that couldn't connect.
- **Optional multi-folder warming.** Set `MAILMIND_WARM_FOLDERS=1` to also pull each
  account's Drafts / Archive / Sent alongside INBOX so search and threading cover more
  than the inbox — throttled, bounded, and never delaying new mail.
- **A UI that stays responsive during IMAP work** — folder badges are cached and
  invalidated on real mutations, the Curate drawer reads the local DB instead of
  scanning every inbox, and adding your first account starts a sync immediately
  instead of leaving you staring at an empty list.

### Data ownership & trust

- **One-click local backup** of your whole mailbox as a consistent `.db` snapshot from
  Settings, plus a CLI that exports lossless per-message `.eml` files or standard
  `mbox` archives (`python3 export.py --format eml --out DIR`). Read-only — it never
  touches IMAP.
- **Bodies render in a locked sandbox** with an explicit deny-all CSP, remote images
  blocked by default, and a runtime guard that strips scripting permission from any
  reader frame.
- **Nothing is ever sent or deleted behind your back.** AI drafts wait for you, bulk
  actions require a preview and a confirm, and every destructive action is reversible.
- **You always know what's happening** — a first-run welcome with a Connect-account
  button, an honest "reading your inbox for the first time" state, an explicit "the
  `claude` CLI isn't reachable" message rather than an endless spinner, and an inline
  Retry on every error panel.

### Keyboard, theming & accessibility

- **Full keyboard control** — `j`/`k`/`n`/`p` to move, `Enter`/`o` to open, `e`/`d`/`b`/
  `s`/`u`/`x` to triage, `l` reply-later, `w` waiting, `v` save a view, `z` undo,
  Gmail-style `g`+letter folder chords, and a `?` legend reachable from every state.
- **A ⌘K command palette** that fuzzy-matches every account, every folder across every
  account, saved views, the actions available on the selected message, and app commands.
- **Two full themes** — a cool-graphite **Slate** dark system where amber means "act on
  this" and periwinkle means "the AI said this", and a warm cream **Paper** light mode.
  Applied before first paint, so there's no flash, and the mail body follows the app.
- **On a phone:** single-pane list-to-reader swap, an off-canvas folder drawer, swipe
  left to archive / right to snooze to tomorrow 8am with a live badge, chevrons to walk
  the brief, and a Back gesture that unwinds reader → list → modal instead of leaving
  the app.
- **Accessibility is wired in** — modal focus traps with focus restore and the rest of
  the page inert, accessible names on every mail frame, an aria-live region announcing
  new mail and AI results, WCAG AA contrast for secondary text in both themes, a
  reduced-motion mode, and a "disable single-key shortcuts" toggle for voice and
  switch users.

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

You can manage connections two ways: the dashboard's **⚙ Settings** panel
(add / edit / remove accounts, set a friendly **alias** per mailbox, and test a
connection — stored passwords are never sent back to the browser), or by editing
`mailmind.config.json` directly as below.

Each account has a `type` and a `credential`. There are **two ways** to supply
the password — mix freely per account:

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
| `MAILMIND_POOL=1` | Reuse persistent per-account IMAP connections instead of connecting per op — **much** faster repeated reads. Off by default; set to `1` for all accounts, or a comma-separated list of emails. Proton Bridge is excluded from the `1` catch-all and must be listed explicitly. |
| `MAILMIND_WARM_FOLDERS=1` | Also sync each account's Drafts / Archive / Sent alongside INBOX, so search and threading cover more than the inbox. Off by default; same `1`-or-list form. |
| `MAILMIND_RECONCILE_SECS` | Target seconds for one full ghost-prune cycle across all accounts (default `1800`; `0` disables). |
| `MAILMIND_NO_DRAFT_TICK=1` | Disable the background reply pre-drafter (also implied by `MAILMIND_NOSYNC`). |
| `MAILMIND_NO_SCHEMA=1` | Ask the model for free-form JSON instead of using structured-output schemas — a fallback for older `claude` CLI builds. |
| `MAILMIND_PROTON_BRIDGE_PASS` | Proton Bridge password (highest precedence) |
| `MAILMIND_PROTON_OP_ITEM` | 1Password item id holding the Bridge password |

Server port is the first CLI arg to `server.py` (default `8721`).

`MAILMIND_POOL` and `MAILMIND_WARM_FOLDERS` are both **off by default** and
nothing in the repo sets them, so a plain `python3 server.py` connects fresh for
every operation and syncs INBOX only. Export them from your shell profile or a
launcher script if you want them on.

## Where your data lives (all local, all git-ignored)

- `mailmind.db` — SQLite store of messages, bodies, attachments + summaries.
- `mailmind_rules.json` — your learned keep/block lists.
- `mailmind_views.json` — your saved searches / smart-folder lanes.
- `sweep_audit.jsonl` / `reconcile_audit.jsonl` — audit trails.
- `mailmind.config.json` — your accounts + credentials.

All of the above are in `.gitignore`. **Nothing personal is committed.**

## Testing

```bash
python3 tests.py               # behavioral classifier suite
python3 tests_sanitize.py      # HTML sanitizer: 24 attack classes, expects 0 leaks
python3 test_css_sanitizer.py  # CSS sanitizer unit tests
```

Each exits non-zero on failure. Separately, `scripts/backfill.sh` is a resilient
summarization backfill — it re-runs `summarize.py` until every non-junk message
has a summary, backing off when the `claude` CLI rate-limits.

## Privacy & contributing

This is a personal-data app. **Never commit** `mailmind.config.json`,
`mailmind_rules.json` (it holds your personal keep/block entries), `mailmind.db`,
the audit `*.jsonl` files, or screenshots of real mail — the `.gitignore` covers
them; don't `git add -f` around it. PRs welcome; keep it dependency-free.

## License

MIT — see [LICENSE](LICENSE).
