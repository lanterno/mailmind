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
  thing that leaves your machine is the text of the mail Claude works on, sent to
  Anthropic through the Claude API or the `claude` CLI.
- **Any mailbox.** Gmail/Workspace, generic IMAP (cPanel/Dovecot/…), and Proton
  (via Bridge) — all configured in one JSON file.
- **It learns.** A keep/block rules layer remembers your decisions so the same
  sender is never re-triaged.

> **Heads-up:** summaries and the other AI features need Claude. Put a Claude API key in `mailmind.config.json` (`"anthropic_api_key"`) or `ANTHROPIC_API_KEY` to call the Claude API directly (billed per token to that key's organization), or install and sign in to the [`claude`](https://docs.claude.com/en/docs/claude-code) CLI to use **your** subscription. With neither, MailMind still ingests and curates mail — it just shows no AI summaries, and the brief says so and how to turn AI on.

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
   summarize.py                summarize + score importance 1–5 with Claude (API or CLI)
        │
   server.py  +  dashboard.html        the app on http://127.0.0.1:8721
```

Around that spine sit the client modules: `mailops.py` (IMAP writes),
`compose.py` + `smtpsend.py` + `outbox.py` (send), `search.py` (FTS + operator
grammar), `threads.py`, `mailparse.py`, `sanitize_html.py` (the reader's
whitelist sanitizer), `ics.py` (invites), `realtime.py` (IMAP IDLE → SSE), and
the AI modules (`summarize*.py`, `draft_reply.py`, `rewrite.py`, `ask.py`).

The app is one view: a real folder-and-account client whose Inbox opens on
today's AI brief. Curation tools (sweep junk, keep/block rules, reconcile, audit)
live in a slide-in drawer.

## Features

MailMind started as a summarizer with a reading pane. It is now a full local-first
mail client — everything below ships today and runs against your local SQLite store
and Claude (the Claude API, or the `claude` CLI on your own machine).

### Reading

- **Everything on one screen.** Folders, list and reading pane sit side by side as
  paper panels. The folder rail is icons or icons with names (the sidebar button or
  `[`), and becomes a drawer on narrower windows; drag the list's edge (or focus it and
  use ←/→) to set its width. Both choices are remembered. Phones stay single-column.
- **A reading pane built for going through mail.** The message sits in a centred
  column with the action bar pinned above it — previous/next, your place in the list
  ("3 of 48") and every verb — and an **Up next** box at the end names the next
  message, with **Archive & next** one click away. `↵` switches to focus reading,
  which hides folders and list; `Esc` brings them back. As the pane narrows, down to
  a phone, the bar stays one row: rarer verbs turn to icons, then move under **⋯**.
- **Summaries before you open anything.** List rows show the stored AI summary under
  the subject, and a gold chip when it names a next step ("Pay 240.00 by 29 Nov");
  opening the message shows the same summary at the top, with no extra AI call, and
  `i` asks for the full one. Rows are grouped by day.
- **Today's brief opens the Inbox.** With no message open, the reading pane
  shows the day's AI brief on a painted landscape (sun by day, moon and stars by
  night), tallies for what needs you, what's waiting and your
  commitments, and the messages that need you, each with its summary and next step.
  Every card has its verbs on it: **Use draft** when a reply is already pre-written
  (else **Reply**), **Read**, and snooze, archive and delete without opening it.
  Beside them on a wide pane (below them on a narrow one), **Today & next** lists the
  coming events from the calendar invites in your mail — opened or not, since ingest
  keeps an invite's body — with a repeating meeting at its next occurrence and a moved
  or cancelled occurrence honoured, and **Waiting on others** the threads you sent that
  are still owed a reply.
  A pinned row at the top of the Inbox (or `k` from the first message) brings it back,
  so opening the Inbox marks nothing read.
- **The Inbox in three tabs.** The list is headed by the folder's name, and on the
  Inbox by **Needs you** (the default, the brief's to-do test), **Everything** and
  **Unread**, each with its count; `j`/`k` walk the tab you're on, and the choice is
  remembered.
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
- **Reading settings, under Aa.** Set the column width (narrow, medium, wide) and a
  face (sans, serif or mono) and size for plain-text mail; rich mail keeps the sender's
  own type. Mail colors are **Auto** (rich mail in its own colors, plain text in the
  app's), **Dark** or **Original**. A change applies to the open message at once,
  without reloading it, and is remembered.
- **Calendar invites become a card.** An invite — an attached `.ics`, or the inline
  calendar part Outlook sends on its own — renders above the message
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
- **Triage straight from the list.** Hovering a row offers Archive, Delete and Snooze
  without opening it.
- **Conversations file whole.** On a conversation — its row, the thread reader, `e`/`d`
  or a bulk selection — Archive and Delete take every message of it in that folder (your
  replies in Sent stay put), and one Undo brings them all back.
- **Bulk triage on demand.** Mark rows with `x` or the checkbox on each avatar, then archive,
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
  per-row account chips, replacing the eight-click morning ritual. The switcher at the
  top of the rail shows each account as a colored dot (stacked for All inboxes) and
  says in words when one can't connect.
- **Document Hub.** Every real attachment across every account in one searchable
  overlay — type facets, live filename/sender/subject search, direct download and
  click-through to the source email, with the same file synced to two accounts merged.
- **Unsubscribe & sender cleanup.** A ranked "senders flooding you" lane where one
  confirm blocks the sender, sweeps their INBOX copies to Trash (one `z` reverses
  both) and fires one standards-compliant unsubscribe.
- **Expiry rules.** Give a sender or domain a shelf life — `python3 rules.py expire
  github.com 72` — and its INBOX mail moves to Trash once it is older than that. The
  server checks hourly; `manage.py expire --dry` previews. Expiry is independent of
  keep/block, so a kept sender can still age out.
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

Everything here runs through Claude and returns **schema-validated** structured
output, so malformed-response errors don't reach the UI. Nothing is ever sent
automatically.

- **Claude API or CLI.** With an API key set, MailMind calls the Messages API
  directly (`claude-opus-5-5` unless `MAILMIND_MODEL` names another; per-message triage
  runs at low effort, everything else at medium). Without a key it uses the local
  `claude` CLI on your subscription. A safety-classifier decline on a mail body is
  re-run on another model server-side, and rate limits and overloads are retried.
  The key goes in `mailmind.config.json` as `"anthropic_api_key"` (inline, or
  `{"op": …}` / `{"env": …}` like an account password), and that wins over
  `ANTHROPIC_API_KEY`, so a key your shell exports for other tools isn't used by
  accident. The server's startup line names the engine, the model and where the key
  came from, and so does the sync menu, in red with the reason ("the API key was
  rejected", "the claude command isn't signed in") when the last call failed. With no
  AI at all, the brief says so calmly and the composer drops Draft and Rewrite; both
  places say that message text goes to Anthropic only when it is summarized, briefed or
  drafted.

- **Summaries and importance scores** per message, plus a per-thread "catch me up"
  TL;DR with key points.
- **A synthesized daily brief** in prose, with topic chips that list the messages
  behind each topic — computed once a day and cached across restarts.
- **"Since you last looked."** The `▾ N new` pill is augmented with a two-to-three
  sentence summary of what actually arrived; the plain count is always the floor.
- **Weekly Review** — an AI retrospective of the last 7 days: sent/received stats, who
  is still waiting on you, who you're waiting on, and three concrete things to start
  next week with. Every item is clickable into the thread.
- **Draft.** One click writes an editable reply into the composer from the whole
  thread. For mail the AI rates important or action-needed, a background pre-drafter can
  have it ready *before* you press Reply — the Reply button reads **Draft ready** — and
  pre-drafts survive restarts. The ▾ beside Draft picks a tone (Brief, Balanced, Warm or
  Formal, remembered) and takes a line of what to say ("yes, but not before 3 pm"); a draft
  over text you'd written leaves it one Undo away.
- **Quick replies.** Under a message from a person — not a no-reply address, junk or a
  receipt — **Yes, that works**, **Can't, sorry**, **Thanks!** and **Ask for a time** open
  the reply with a one-line answer at once, and swap in a short AI version when it comes
  unless you've started editing. With no AI the line simply stays; nothing sends itself.
- **Rewrite toolbar** over the composer: Shorten, Elaborate, Formal, Friendly, Fix
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
  message back before it hits the wire and restores everything you'd written. If you're
  writing another message by then, it is saved to Drafts before the undone one takes its
  place, and if that save fails it stays open while the undone one waits behind Edit.
- **Scheduled send** — pick a preset or an exact time; the row shows
  `Scheduled · <time>` with **Send now** and **Cancel**.
- **Send-time checks.** Send, ⌘/Ctrl+Enter and the Later presets first look the message
  over. A gold strip holds the send when there's no subject, when you wrote "attached" (or
  *ci-joint*, *te adjunto*, *em anexo*, *anbei*, *in allegato*, *bijgevoegd*, *مرفق*: eight
  languages, read from what you typed, not the quote or signature) with nothing attached, or
  when a reply-all would reach more than five people, with one button that drops everyone
  copied in from the original. In a reply only wording that presents a file of yours counts:
  "thanks for the attachment" is about theirs. Each line has a fix button. Esc returns you to
  where you were. **Send anyway** only works after a short pause, so a double-click can't
  confirm it. A file still being read, or a scheduled time that has passed, can't be waved
  through. A Later time the strip held back stays picked: the footer reads **Schedule ▸**
  until it goes out, and the Later menu offers **Send now instead**. What you waved through
  stays that way after Undo. RSVP replies, unsubscribe mail and the outbox's Retry / Send now
  don't go through the composer, so they aren't checked.
- **"New" recipients.** An address you've never exchanged mail with gets a small **new** tag
  on its chip. That comes from one batched, local lookup per pause in typing, never one
  request per chip. People already on the thread you're replying to aren't marked, and
  neither is anyone while the database is too young to tell.
- **Recipient autocomplete** drawn entirely from your own mail history, ranked by how
  often and how recently you've corresponded, fully keyboard-driven.
- **Per-account signatures** with a global default — auto-inserted into new composes
  and live-swapped when you change the From account, without clobbering your edits.
- **Drafts that don't litter.** A compose is only "dirty" once you actually edit the
  subject or body, autosave runs every 8 seconds, and a signature-only body is never
  saved to your Drafts folder.
- **Drafts reopen as you left them.** Edit on a saved reply or forward brings back your text,
  with the quote or forwarded section folded below it, plus every file, the Bcc and the thread.
  All of it is read from the draft's own copy on the server, so sending it never needs the
  original, even once that has been archived or deleted. A draft another app saved opens whole
  as plain text, with a note in the composer on anything that changed. Edit refuses, and points
  you back to the app that made it, when a draft has:
  - an email or a calendar invite inside it;
  - formatted text only;
  - a part MailMind can't show;
  - more than 25 MB of files.

  A copy that was replaced after the list loaded is caught before it can overwrite the newer one,
  and a draft whose send is still waiting in the outbox stays shut until you cancel that send.

### Sync, reliability & performance

- **Live updates without a refresh.** An SSE stream pushes new mail, snooze wake-ups,
  mutes and ready AI drafts into the open list behind a sticky `N new` pill that never
  yanks the view mid-triage — with polling as an automatic fallback.
- **Background notifications.** An opt-in 🔔 fires a desktop notification and keeps an
  unread count in the tab title when MailMind isn't focused.
- **A meeting nudge.** Ten minutes before a timed event from your invites, a sunlit pill
  beside the sync status reads "Studio sync in 8 min"; it opens the invite or dismisses,
  and turns into one desktop notification when the tab is in the background.
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
  button, an explicit "Claude isn't reachable" message rather than an endless spinner,
  and an inline Retry on every error panel.

### Keyboard, theming & accessibility

- **Full keyboard control** — `j`/`k`/`n`/`p` to move, `o` to open, `↵` for focus reading,
  `[` to show or hide folder names, `e`/`d`/`b`/`s`/`u`/`x` to triage, `l` reply-later,
  `w` waiting, `v` save a view, `z` undo,
  Gmail-style `g`+letter folder chords, and a `?` legend reachable from every state.
- **A ⌘K command palette** that fuzzy-matches every account, every folder across every
  account, saved views, the actions available on the selected message, and app commands.
- **Three themes in the Meadow palette** — a cream **Day** (the default), a warm
  **Dusk** and a deep-blue **Night**, cycled by the sun button in the top bar (or
  chosen in the `?` legend and ⌘K), with the panels laid on a sky that follows the theme.
  Indigo marks actions and selection, gold means only "needs you", moss means only "the
  AI said this", and rust is for delete.
  Applied before first paint, so there's no flash, and the mail body follows the app.
- **One drawn icon set** — toolbars, folders and menus share one set of monoline icons,
  inline SVG in the page itself (no emoji, no icon font, no extra request), so they look
  alike on every platform and take their color from the theme. The wind mark is the
  only symbol for the AI.
- **On a phone:** single-pane list-to-reader swap, an off-canvas folder drawer, swipe
  left to archive / right to snooze to tomorrow 8am with a live badge, and a Back gesture that unwinds reader → list → modal instead of leaving
  the app.
- **Accessibility is wired in** — modal focus traps with focus restore and the rest of
  the page inert, accessible names on every mail frame, an aria-live region announcing
  new mail and AI results, WCAG AA contrast for secondary text in every theme, a
  reduced-motion mode, and a "disable single-key shortcuts" toggle for voice and
  switch users.

## Prerequisites

- **Python 3.10+** (developed/tested on 3.14). No third-party packages.
- **Claude**, for summaries — either a Claude API key, as `"anthropic_api_key"` in
  `mailmind.config.json` or in `ANTHROPIC_API_KEY` (MailMind calls the Messages API
  itself, no SDK), or the `claude` CLI on your `PATH`, installed and authenticated
  (it shells out to `claude -p …` and uses your Claude subscription). Set
  `MAILMIND_MODEL` to pick a cheaper/faster model.
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

# 2. Verify the credentials resolve (prints ok/fail per account and for the API key, no secrets)
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
python3 rules.py expire github.com 72      # trash this domain's INBOX mail after 72h (0 clears)
python3 manage.py expire all --dry         # preview what the expiry rules would trash now
```

> ⚠️ **`sweep`, `expire` and `empty-trash` mutate real mailboxes** (move to Trash / purge).
> Always `--dry` first. Every sweep/reconcile is logged to `sweep_audit.jsonl` /
> `reconcile_audit.jsonl`. Moves go to Trash and are recoverable; Gmail purges
> its Trash after 30 days.

## Environment variables

| Variable | Effect |
|---|---|
| `MAILMIND_NOSYNC=1` | Serve the dashboard read-only (no background sync) |
| `ANTHROPIC_API_KEY` | Call the Claude API for every AI feature, billed to that key's organization. `"anthropic_api_key"` in `mailmind.config.json` wins over it. Neither set: use the `claude` CLI. |
| `MAILMIND_AI` | `api` or `cli` — use that engine whether or not a key is set. |
| `MAILMIND_MODEL` | Override the Claude model (default `claude-opus-5-5` on the API, the CLI's own default otherwise), e.g. `claude-haiku-5-5` |
| `ANTHROPIC_BASE_URL` | Send API calls somewhere other than `https://api.anthropic.com` (a proxy). |
| `MAILMIND_POOL=1` | Reuse persistent per-account IMAP connections instead of connecting per op — **much** faster repeated reads. Off by default; set to `1` for all accounts, or a comma-separated list of emails. Proton Bridge is excluded from the `1` catch-all and must be listed explicitly. |
| `MAILMIND_WARM_FOLDERS=1` | Also sync each account's Drafts / Archive / Sent alongside INBOX, so search and threading cover more than the inbox. Off by default; same `1`-or-list form. |
| `MAILMIND_RECONCILE_SECS` | Target seconds for one full ghost-prune cycle across all accounts (default `1800`; `0` disables). |
| `MAILMIND_EXPIRE_SECS` | Seconds between expiry-rule passes (default `3600`; `0` disables). Never runs under `MAILMIND_NOSYNC`. |
| `MAILMIND_NO_DRAFT_TICK=1` | Disable the background reply pre-drafter (also implied by `MAILMIND_NOSYNC`). |
| `MAILMIND_NO_SCHEMA=1` | Ask the model for free-form JSON instead of using structured-output schemas — a fallback for older `claude` CLI builds and models without structured output. |
| `MAILMIND_PROTON_BRIDGE_PASS` | Proton Bridge password (highest precedence) |
| `MAILMIND_PROTON_OP_ITEM` | 1Password item id holding the Bridge password |

Server port is the first CLI arg to `server.py` (default `8721`).

`MAILMIND_POOL` and `MAILMIND_WARM_FOLDERS` are both **off by default** and
nothing in the repo sets them, so a plain `python3 server.py` connects fresh for
every operation and syncs INBOX only. Export them from your shell profile or a
launcher script if you want them on.

## Where your data lives (all local, all git-ignored)

- `mailmind.db` — SQLite store of messages, bodies, attachments + summaries.
- `mailmind_rules.json` — your learned keep/block lists and expiry rules.
- `mailmind_views.json` — your saved searches / smart-folder lanes.
- `sweep_audit.jsonl` / `reconcile_audit.jsonl` — audit trails.
- `mailmind.config.json` — your accounts + credentials.

All of the above are in `.gitignore`. **Nothing personal is committed.**

## Testing

```bash
python3 tests.py               # behavioral classifier suite
python3 tests_sanitize.py      # HTML sanitizer: 24 attack classes, expects 0 leaks
python3 test_css_sanitizer.py  # CSS sanitizer unit tests
python3 test_expire.py         # expiry rules: storage, matching, age selection
python3 test_thread_file.py    # whole-conversation archive/delete: membership, undo, failures
python3 test_claude_api.py     # Claude API engine against a local stub: requests, refusals, retries
python3 test_upcoming.py       # Today & next: stored invites, repeats, updates, cancellations
python3 test_contacts_known.py # "new" recipients lookup: matching, drafts, thin history, CSRF
python3 test_send_after.py     # send-later times: passed or far-off ones are refused
python3 test_send_guard.py     # send-time checks: attachment words, subject, reply-all (needs node)
python3 test_draft_resume.py   # reopened drafts: text and quote, files, Bcc, threading, refusals
python3 test_draft_resume_ui.py # the composer's side of reopening a draft (needs node)
```

Each exits non-zero on failure. With [`just`](https://just.systems) installed,
`just test` runs all twelve and `just verify` adds lint, byte-compile and a
frontend syntax check — `just` on its own lists every recipe.

Separately, `scripts/backfill.sh` is a resilient summarization backfill — it
re-runs `summarize.py` until every non-junk message has a summary, backing off
when Claude rate-limits.

## Privacy & contributing

This is a personal-data app. **Never commit** `mailmind.config.json`,
`mailmind_rules.json` (it holds your personal keep/block entries), `mailmind.db`,
the audit `*.jsonl` files, or screenshots of real mail — the `.gitignore` covers
them; don't `git add -f` around it. PRs welcome; keep it dependency-free.

## License

MIT — see [LICENSE](LICENSE).
