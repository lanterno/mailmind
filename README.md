# MailMind

**A local-first email client with an AI layer.** MailMind logs
into your mailboxes over IMAP, throws away the junk, AI-summarizes everything
that matters, and gives you a fast dashboard to read, triage, search and *send* —
so you read *summaries of what's important*, not your inbox.

It started as a summarizer with a reading pane and is now a full mail client:
compose and SMTP send with a durable outbox and Undo Send, conversation
threading, attachments, full-text search with an operator grammar, IMAP IDLE
push, snooze/mute/waiting, and a set of AI actions that never fire on their own.
See [Features](#features) for what ships today and
[ROADMAP.md](ROADMAP.md) for what doesn't yet.

- **Little to install.** Python, [Node](https://nodejs.org) to build the app, and six packages
  (FastAPI, uvicorn, the Anthropic SDK, IMAPClient, icalendar and recurring-ical-events) that
  [uv](https://docs.astral.sh/uv/) fetches on first run.
- **Your data stays local.** Mail is stored in a local SQLite file; the only
  thing that leaves your machine is the text of the mail Claude works on, sent to
  Anthropic through the Claude API.
- **Any mailbox.** Gmail/Workspace, generic IMAP (cPanel/Dovecot/…), and Proton
  (via Bridge) — all configured in one JSON file.
- **It learns.** A keep/block rules layer remembers your decisions so the same
  sender is never re-triaged.

> **Heads-up:** summaries and the other AI features need a Claude API key, in `mailmind.config.json` (`"anthropic_api_key"`) or `ANTHROPIC_API_KEY`; calls are billed per token to that key's organization. Without one, AI is off: MailMind still ingests and curates mail, sends nothing to Anthropic, and the brief says AI is off and how to turn it on.

---

## How it works

```
mailmind.config.json          (your accounts + how to read their passwords)
        │
   mailmind/accounts/          resolve credentials, open IMAP connections
        │
   mailmind/mailbox/           pull INBOX headers + bodies, skip obvious junk
        │
   mailmind/curation/          classify each message (spam / marketing /
        │                      notifications / receipts / personal / protected)
   mailmind/mailbox/ store  →  SQLite (mailmind.db)
        │
   mailmind/assistant/         summarize + score importance 1–5 with Claude
        │
   mailmind/http/  +  web/ (the React app)   the app on http://127.0.0.1:8721, served by
                                       `python -m mailmind serve` (FastAPI on uvicorn)
```

Around that spine sit the client modules: `mailmind/mailbox/` (IMAP writes, IMAP IDLE),
`mailmind/compose/` (send), `mailmind/discovery/` (FTS + operator
grammar), `mailmind/attention/` (snooze, mute, reply later, follow-ups),
`mailmind/content/` (reading a message, and the reader's whitelist sanitizer),
`mailmind/calendar/` (invites), and `mailmind/assistant/` (everything asked of Claude).

The app is one view: a real folder-and-account client whose Inbox opens on
today's AI brief. Curation tools (sweep junk, keep/block rules, audit) live in a
slide-in drawer, and the sync menu opens it and runs a reconcile.

## Features

MailMind started as a summarizer with a reading pane. It is now a full local-first
mail client — everything below ships today and runs against your local SQLite store
and the Claude API.

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
  `i` asks for the full one. A message keeps its summary when it moves, and an Undo
  brings it back, so neither pays for a second call. Rows are grouped by day.
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
- **A big folder opens at once.** The first time you open a folder, MailMind reads it
  whole in the background. The list waits a few seconds for it, then says "Reading this
  folder for the first time…" and fills in when the scan ends; a folder it couldn't read
  says so, with a Retry.
- **Folders stay current, not only the Inbox.** Opening a folder read before catches it
  up — mail another client filed there, a draft just saved, flags changed elsewhere — at
  most once a minute, and the list fills in if that takes more than a moment.
- **Messages open instantly.** The reader paints subject, sender and folder from the
  list immediately behind a shimmer skeleton, and escalates to an honest "first open
  of an older message can take a minute" hint if the server is slow.
- **Reading stays instant during triage.** Opening a message quietly warms the next
  two bodies into the local cache.
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
  An invite is read through `icalendar` and `recurring-ical-events`: every recurrence rule down
  to a day, RDATE, and each event of an invite.
- **An email attached to an email stays one file.** Its chip shows its real size, the download
  is the whole message as an `.eml`, and a forward carries it intact. Its text and its files
  aren't taken for the outer message's.
- **Summarize this sender** — an AI summary of one sender's recent threads, from their contact card, on your click.
- **Who wrote this, at a glance.** Opening a message shows who the sender is and how much you've written, and warns
  when Reply-To leaves their organisation. The warning sits outside the mail, where a message can't forge or hide it.
- **Click any sender for a relationship card** — how many emails they've sent, how
  often you replied, first and last contact, which of your accounts they write to,
  and their recent threads, with quick Compose and search actions. Pure local SQL,
  no enrichment API.

- **Flowed mail reads as written.** `format=flowed` mail shows its paragraphs, in the reader,
  in search and in a reopened draft, not lines broken at 72 columns.
- **Today & next understands Outlook.** Its zone names read as the zones they are, a later
  SEQUENCE of an invite wins, and a change to all future occurrences reaches them all.
- **Dark mail colors stay night** under every theme, and a conversation's badge matches what
  opening it shows.

### Triage & organization

- **One-key triage with real undo.** `e` archives *everywhere* (it never trashes),
  destructive verbs live on `#`/Delete/Backspace, and `z` — or a floating Undo
  button — reverses archive, delete, junk, snooze and mute, server-side. A reversal the
  server refuses is never reported as done, and when a group comes back only in part, what
  couldn't be restored stays for the next `z`. Undo survives a restart, and reaches back 50 actions or 24 hours.
- **Triage straight from the list.** Hovering a row offers Archive, Delete and Snooze
  without opening it.
- **Links that keep their word.** What is open is in the URL by the message's key, the
  folder its copy is in and the server's number for that copy, never a row's number. A link
  you kept, or a tab left open across a resync, opens the copy it named or nothing, never a
  message beside it that reuses its Message-ID, and every action names it the same way.
- **Conversations file whole.** On a conversation — its row, the thread reader, `e`/`d`
  or a bulk selection — Archive and Delete take every message of it in that folder (your
  replies in Sent stay put), and one Undo brings them all back.
- **Bulk triage on demand.** Mark rows with `x` or the checkbox on each avatar, then archive,
  delete, read, unread, star, junk, snooze or mute the whole set. Archive, delete, junk, snooze
  and mute go as **one** undo group; read, unread and star aren't on the undo stack.
- **Snooze, mute, reply-later and Waiting.** A numbered snooze preset popover (plus a
  native date-time picker for custom times) with its own Snoozed folder; a reply-later
  stack; thread mute that silences future replies at ingest and drops the thread out of
  the inbox while keeping it searchable; and a **Waiting** folder that tracks what you
  owe and are owed ("Sent 3d ago · no reply", "Awaiting your reply") and auto-resolves
  when a reply lands. A draft of your reply doesn't count: a pin stays until it goes out.
- **The Screener.** First-contact senders are gated one decision at a time — Allow
  keeps, Screen-out blocks — and each decision becomes a rule, so the same sender is
  never re-litigated. On your click, the AI suggests a verdict for the senders it's sure of; you still decide.
- **Purpose-built lanes.** **Feed** reads as a calm newsletter river of summary cards
  with read-and-archive; **Paper trail** reads as a money ledger with extracted amounts,
  refunds and monthly totals.
- **Saved searches as smart folders.** Any search can be pinned to the rail with one
  key. Ships with Unread, Flagged, Has attachments and Last 7 days, plus five
  auto-label lanes — Feed, Paper trail, Notifications, Action needed, Finance —
  which are just saved queries over the existing classifier buckets.
- **Snoozed, Muted, Waiting and Reply later across every account,** with counts, on All inboxes.
- **One "All inboxes" view** merges every connected account's INBOX by date with
  per-row account chips, as one list that pages back through all of it, and an account still on
  its first scan holds up no one. The switcher at the top of the rail shows each account as a colored dot
  (stacked for All inboxes) and says in words when one can't connect.
- **Document Hub.** A place in the rail (`/documents`) holding every real attachment across every
  account, in sections by sender or by type, with images previewed in a sandboxed frame — type facets, live filename/sender/subject search that reaches every file, a
  "show more" past the first page, direct download and click-through to the source email,
  with the same file synced to two accounts merged.
- **Gmail labels are read at sync,** so `label:` finds mail under every label it carries and each label has its lane in the
  rail. Mail already stored picks them up when a scan next fetches it.
- **Move to any folder.** `m` moves the open message, a conversation or marked rows to a folder you find
  by typing, with Undo.
- **Clean up several senders at once.** Mark them in the drawer and do it from one preview, each with its reason from
  the AI, under one Undo.
- **Unsubscribe & sender cleanup.** A ranked "senders flooding you" lane where one
  confirm blocks the sender, sweeps their INBOX copies to Trash (one `z` reverses
  both) and fires one standards-compliant unsubscribe: RFC 8058's one-click POST where the
  sender offers it, else a mailto from each account that got the mail, else the sender's page,
  opened in the browser. The way out comes from the headers ingest keeps, so a sender whose mail
  was never opened can still be left, and a second try after the sweep still knows it. A mailto goes through the
  outbox, so a server that's away only makes it wait, and it's filed in Sent. A preview stands for ten minutes and
  for the way out it showed: after that, or if the list changed it, the block and sweep go ahead and nothing is sent.
- **Expiry rules.** Give a sender or domain a shelf life — `python3 -m mailmind rules expire
  github.com 72` — and its INBOX mail moves to Trash once it is older than that. The
  server checks hourly; `python3 -m mailmind expire --dry` previews. Expiry is independent of
  keep/block, so a kept sender can still age out.
- **Lists that heal.** A row leaves the list the moment you file it, a folder pages with
  **Load 200 more** and a search with **Load more results**, capped counts read honestly as
  `N+`, and switching accounts renders the folder rail from cache instead of blanking on a
  live IMAP `LIST`.

### Search

- **Search suggests as you type** — senders, domains and operators — and can be held to one
  conversation.
- **Ask in plain words.** A search that reads as a sentence is translated on Enter, under a chip that
  gives you the literal search back.
- **Filters.** Any search can become a filter that stars or marks read new Inbox mail as it arrives,
  and Curate lists them to edit or remove. A filter never moves mail.
- **Full-text search across every account by default**, with the scope persisted
  between sessions. A polite background worker backfills bodies for mail ingested
  before bodies were stored — yielding instantly to anything you're doing, and moving on past
  any the server won't give — so search covers the whole mailbox, not just recent mail.
- **A safe operator grammar:** `from:` `to:` `cc:` `subject:` `body:` `label:`,
  `is:unread|read|flagged|answered|junk|newsletter|receipt|notification|snoozed`,
  `bucket:` `category:`, `has:attachment`, `in:` `account:`,
  `before:`/`after:`, `"phrases"` and `-exclusion`. `to:`/`cc:` hit real indexed
  recipients rather than the body text. A date means midnight where you are. Snoozed
  mail stays out of sight until it wakes, unless you ask for `is:snoozed`. The grammar
  is advertised in the search placeholder and documented in the `?` legend.
- **Search that persists.** Opening a result keeps the results as your working list —
  archive, snooze, mute, star, bulk and swipe all heal the results in place and
  auto-advance to the next hit.

### AI intelligence

Everything here runs through Claude and returns **schema-validated** structured
output, so malformed-response errors don't reach the UI. Nothing is ever sent
automatically.

- **The Claude API.** With an API key set, the server calls the Messages API through the
  official Anthropic SDK, and the command line through a client of its own on Python alone
  (`claude-opus-5-5` unless `MAILMIND_MODEL` names another; per-message triage
  runs at low effort, everything else at medium). Without a key, AI is off and
  nothing is sent. A safety-classifier decline on a mail body is
  re-run on another model server-side, and rate limits and overloads are retried.
  The key goes in `mailmind.config.json` as `"anthropic_api_key"` (inline, or
  `{"op": …}` / `{"env": …}` like an account password), and that wins over
  `ANTHROPIC_API_KEY`, so a key your shell exports for other tools isn't used by
  accident. The server's startup line names the model and where the key came from.
  The sync menu names the model, and in red the reason ("the API key was rejected",
  "rate-limited for now") when the last call failed. With no key, the brief says
  AI is off calmly and the composer drops Draft and Rewrite. The brief's card, and the sync
  menu with AI on, say that message text goes to Anthropic only when it is summarized, briefed or
  drafted.
  Every call is counted by day and by feature (triage, thread summaries, the brief, drafts,
  …) at Anthropic's list prices. The sync menu shows what today cost so far against the
  daily limit, and `python3 -m mailmind ai usage --days 30` (`just ai-usage`) lists each day.
  A backlog (a first import, or after an outage) goes to the Message Batches API at half price,
  one batch at a time, while new mail is still triaged as it arrives, so a large import can leave
  older mail without a summary for up to a day. Anthropic holds a batch until MailMind has brought
  its answers in, then MailMind deletes it. The sync menu says how many messages are out.
  MailMind spends at most $15 a day unless `"ai_daily_limit_usd"` in `mailmind.config.json`
  names another amount. At the limit, triage and pre-drafting stop until the next day,
  anything you ask the AI for asks first whether to go over, and a brief not yet written waits
  for you to ask for it. Background work waits while you
  use the AI and for 45 seconds after. Pre-drafting runs only if you've used the app in the
  last 45 minutes. If the API keeps rejecting a request, background work stops until the key
  or the model changes, while what you ask for still goes and shows the error.

- **Summaries and importance scores** per message, plus a per-thread "catch me up"
  TL;DR with key points.
- **A synthesized daily brief** in prose, with topic chips that list the messages
  behind each topic — computed once a day and cached across restarts.
- **"Since you last looked."** The `▾ N new` pill, which names mail that landed outside the
  list on screen, is augmented with a two-to-three sentence summary of what actually arrived;
  the plain count is always the floor.
- **Weekly Review** — an AI retrospective of the last 7 days, with how much you filed away and the week's meetings and
  deadlines: sent/received stats, who
  is still waiting on you, who you're waiting on, and three concrete things to start
  next week with. Each message it names opens in its thread.
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
  a single undo. Confirming files exactly what the preview showed, for ten minutes,
  and mail that came after it stays put. Unless you name a folder, a command leaves Sent,
  Drafts, Trash and Junk alone. Anything unsafe or ambiguous degrades into an ordinary
  search that touches nothing.
- **Tidy.** One strip offers to file every low-importance info/other message to
  Archive, with a Review mode to inspect them first and one undo to reverse the batch.
  User-triggered unless you turn on **Tidy by itself** in Settings: once a day it files what Tidy offers, and says so
  with one click to take it all back.
- **Morning brief (opt-in).** Today's brief is ready before you open the Inbox, within the daily AI limit.
- **Find dates.** On your click, Claude finds the meetings and dated tasks a message proposes, and you confirm or edit
  each. An event saves as an `.ics` file for your calendar and invites nobody; a task joins your list beside Commitments.
- **Commitments.** Every AI-extracted next-step across the inbox as one checkable
  agenda, each row with inline done / later / open and a click into the message.

### Compose & send

- **A durable outbox.** Queued mail survives a restart, goes out the moment its Undo
  window ends, retries with backoff (but not a refusal for good, such as an unknown recipient or a
  wrong password, which it gives up on at once), and surfaces failures at once on the outbox
  button, with a Retry that starts the tries afresh. A send that never reached the outbox keeps
  your words behind Edit on its float. Each account's mail
  goes out in a lane of its own, so one account's unreachable server holds up no other's. Where
  the server doesn't file sent mail itself, a Sent copy that fails is tried again with the same
  backoff, never sending the message twice or filing it twice, and the outbox says so if it
  never lands. Marking the original answered and removing the draft's copy are tried again the same way. A send
  waiting on a retry says so, with Try now and Cancel. If the connection drops, or MailMind stops, just as the whole
  message has gone out, no one can say whether the server took it: the outbox says it may have gone out and sends it
  again only if you ask. On Gmail, which files sent mail itself, Send again looks in Sent first and leaves it at that
  if the message is there.
- **Undo Send** — a grace window (10 seconds unless you choose another, from 5 to 60, in Settings) with a live countdown that pulls the
  message back before it hits the wire and restores everything you'd written. If you're
  writing another message by then, it is saved to Drafts before the undone one takes its
  place, and if that save fails it stays open while the undone one waits behind Edit.
- **Scheduled send** — pick a preset or an exact time; its row in the outbox says when,
  with **Send now** and **Cancel**.
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
- **Per-account signatures**, set in Settings, with a global default (`"signature_default"` in
  `mailmind.config.json`) — auto-inserted into new composes
  and live-swapped when you change the From account, without clobbering your edits.
- **Drafts that don't litter.** A compose is only "dirty" once you actually edit it,
  autosave runs every 8 seconds, and a signature-only body is never saved to your Drafts
  folder. Saves of one message go in turn and leave one copy, whatever overlaps them, and the
  Drafts list follows at once: a replaced, discarded or sent draft leaves it, on the server first.
- **Drafts that don't need the server.** A save lands on this machine first, so writing goes on
  whether the server is there or not, and read-only too. The server's copy follows when you close the message or
  save, and at most once a minute while you type, and a short reply sent within the minute never
  touches your Drafts folder. A draft that can't reach its server waits in the outbox panel, with Edit and Discard,
  and goes by itself once it can. A discard waits the same way, and a crash mid-save never leaves a second copy.
- **Drafts that don't lose your words.** What you type during a save is saved next. If the
  save that closes a message fails, the message stays a click away, and reopening it there
  (or by Undo Send) saves it when you close it again. If you changed a draft in another window, a
  save that hasn't seen that says so instead of overwriting it, and if another app changed it, you
  keep both.
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

  A reply you've saved once no longer needs the mail it answers: archive that first, or let another
  device move it, and the reply still goes out with its quote and threading, and still marks it
  answered.

  What counts as a draft is what your server says: the `\Draft` flag, or its Drafts folder under
  whatever name it uses. Mail you filed in "Contract drafts" stays ordinary mail, with Reply and
  no way for a Discard to remove it.

  A copy that was replaced after the list loaded is caught before it can overwrite the newer one,
  and a draft whose send is still waiting in the outbox stays shut until you cancel that send.

- **Write in Markdown.** A message with Markdown goes out as plain text plus sanitized HTML, with
  Write/Preview in the composer, and a draft reopens as you typed it.
- **Pictures in the text.** A button or a paste puts a picture at the cursor, and it travels inline by `cid:`.
- **Snippets.** Type `;name` and Tab to bring in saved words, and Tab through their `{{placeholders}}`.
- **A last look before it goes.** MailMind catches an address one typo from someone you write
  to, says when Reply-To sends a reply elsewhere, counts your plus-addresses and send-as
  addresses as yours, and shows a forward's true file count.
- **Pasted links arrive clean.** Links pasted into the composer lose tracking parameters and
  redirect wrappers, and Undo still takes the paste back.
- **A draft holding an attached email reopens** with that email as one of its files.

### Sync, reliability & performance

- **Live updates without a refresh.** An SSE stream tells the app of new mail, snooze wake-ups,
  mutes and ready AI drafts. The open list refreshes in place, mail that landed outside it shows
  as a sticky `N new` pill, and timers take over while the stream is down. An undo in another
  tab and a move queued offline that lands, or won't, refresh the list too. A tab that drops off (a
  sleeping laptop, a restart) hears what it missed when it reconnects, or fetches afresh
  when the server can no longer replay it.
- **Changes that wait are in plain sight.** A move, read mark or star made while the server can't
  be reached shows in the panel behind the outbox button, which says why it waits. One that gave
  up offers Retry, and any of them offers Discard, which leaves the message as the server has it.
- **Push that follows your accounts.** IMAP IDLE watches each account's inbox. Add an
  account, change its password or server, or remove it, and IDLE follows at once, with no
  restart; a move still queued for a removed account is given up rather than left waiting.
- **Background notifications.** Opt in from the sync menu and new mail fires a desktop notification
  while MailMind's tab is in the background, and the tab title counts what arrived either way.
- **A meeting nudge.** Ten minutes before a timed event from your invites, a sunlit pill
  beside the sync status reads "Studio sync in 8 min"; it opens the invite or dismisses,
  and turns into one desktop notification when the tab is in the background.
- **Triage survives an outage.** Archive, trash, junk, read marks and stars queue
  durably with an optimistic local update (and an undo for the moves), then re-apply exactly
  once when IMAP returns. The
  message leaves the list at once but MailMind keeps it until the move lands, so a move the
  server refuses, or one the queue gives up on, puts it back where it is, and Undo of a move
  that hasn't gone out restores it without asking the server.
- **A read-only mode that really is.** `just dev` runs MailMind read-only: it reads your
  mail when you open it, but writes nothing to a mail server. Its connections can only read,
  sends, moves, read marks and stars wait in their queues for a normal run, and what can't wait
  is refused. The sync pill says Read-only, and a move says it waits.
- **Filing never loses mail.** A move the server refuses leaves the message where it was
  and says so. On a server without MOVE, MailMind copies the one message, checks the
  copy landed, then removes only that message, never expunging the rest of the folder. A star
  or a read mark the server refuses is taken back on screen too, instead of showing until the
  next sync.
- **Flags follow other clients.** Each sync reads the read, star and answered marks again, so mail
  you read on your phone shows read here too: whatever changed, however old, where the server can say,
  and a folder's newest 500 messages where it can't. A change you make meanwhile, or one still queued
  offline, keeps its own.
- **Phantom messages clean themselves up.** A background loop reconciles one account
  per tick against the server, every folder MailMind has synced, yields instantly to anything
  you're doing, and pushes an event so open lists drop messages deleted in another client. It prunes only what it read
  before asking the server, so mail a sync stores while it runs is never taken for a ghost.
- **Optional persistent per-account IMAP connections.** Set `MAILMIND_POOL=1` and repeated reads
  get dramatically faster, with connection validation, age/use recycling and extra-conservative
  handling for Proton Bridge's low connection cap. An account's `"pool": true` asks for it between runs. Off by default while it's being tried out.
- **A hung server costs a click about twelve seconds, not two minutes,** and Undo is offered as soon as it answers;
  the worker keeps trying. Two processes changing your rules can't lose each other's change.
- **A first read counts as it goes.** A folder read for the first time says how many messages have arrived so far.
- **Per-account sync health.** Settings shows last successful sync, sync count, or a
  coarse error kind per connection, with a `↻ Retry` button — plus a dismissible
  banner naming any accounts that couldn't connect.
- **Optional multi-folder warming.** Set `MAILMIND_WARM_FOLDERS=1`, or an account's `"warm_folders": true`, to also pull each
  account's Drafts / Archive / Sent alongside INBOX so search and threading cover more
  than the inbox — throttled, bounded, and never delaying new mail. Warming reads only a
  folder's newest mail, so opening the folder still reads the rest, and so does opening one
  whose first sync failed.
- **A UI that stays responsive during IMAP work** — folder badges are cached and
  invalidated on real mutations, the Curate drawer reads the local DB instead of
  scanning every inbox, and adding your first account starts a sync immediately
  instead of leaving you staring at an empty list.

### Data ownership & trust

- **One-click local backup** of your whole mailbox as a consistent `.db` snapshot from
  Settings, or a zip of lossless per-message `.eml` files or standard `mbox` archives, there
  or from the command line (`python3 -m mailmind export --format eml --out DIR`). Read-only — it never
  touches IMAP.
- **Upgrades can't cost you mail.** Before a new version changes the database's layout, MailMind
  backs it up beside it (the newest three kept, readable only by you), and each step of the change
  lands whole or not at all. `python3 -m mailmind db check-upgrade` tries the next upgrade on a copy
  first and reports only row counts and integrity checks, never a message.
- **The page asks no other host for anything** until you load a message's images. Its fonts are served from the
  app, so opening your mail tells no font service you did.
- **Only your own MailMind page can talk to the local server.** It answers just its
  own host name, refuses requests other websites start and any change that lacks the page's
  token and origin, can't be framed by another page, and checks every folder name and search
  value before it reaches the mail server, so a message whose ID hides a line break can't slip
  in a command of its own. Its answers tell the browser to keep them from other sites and out of
  caches, and the page carries a content security policy that lets only its own scripts run.
- **Your Bridge password goes only to Bridge.** Bridge's certificate is pinned the first
  time MailMind connects, so something else listening on its port while it's down gets no
  password, over IMAP or SMTP. A changed certificate shows in Settings, to trust or not.
- **A password gone bad isn't sent over and over.** When a server refuses a login, MailMind
  doesn't try it again for five minutes, then twice that each time, up to an hour, so it isn't
  what locks the account out. Moves wait in their queue meanwhile. Retry and Test in Settings try at
  once, and so does saving a new password.
- **An account that's down still shows its folders.** When a server can't be reached, the
  rail lists the folders MailMind already holds that account's mail in, with their counts.
- **Bodies render in a locked sandbox** with an explicit deny-all CSP, remote images
  blocked by default, and a runtime guard that strips scripting permission from any
  reader frame.
- **Nothing is ever sent or deleted behind your back.** AI drafts wait for you, bulk
  actions require a preview and a confirm, and every destructive action is reversible.
- **A 60-second guided first run,** offered once and always available from `?`.
- **Snoozed mail that wakes out of sight** stays said in the new-mail pill.
- **You always know what's happening** — a first-run welcome with a Connect-account
  button, an explicit "Claude not reachable" message rather than an endless spinner,
  and an inline Retry on every error panel.

### Keyboard, theming & accessibility

- **Full keyboard control** — `j`/`k` to move and open, `n`/`p` for the next or previous unread,
  `↵` for focus reading, `[` to show or hide folder names, `e`/`d`/`b`/`s`/`u`/`x` to triage, `h` snooze, `w` mute,
  `l` reply-later, `v` save a view, `z` undo,
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
  left to archive / right to snooze to tomorrow 8am with a live badge, and a Back gesture that
  closes what's on top, then the message, instead of leaving the app.
- **Accessibility is wired in** — modal focus traps with focus restore and the rest of
  the page inert, accessible names on every mail frame, an aria-live region announcing
  new mail and AI results, WCAG AA contrast for secondary text in every theme, a
  reduced-motion mode, and a "disable single-key shortcuts" toggle for voice and
  switch users.

- **Preferences live in Settings,** kept in your config file so they outlive a browser-data clear: Comfortable,
  Cozy or Compact rows, high contrast, what notifies you and quiet hours, and the AI's daily limit.
- **Rows work on hover.** Star and Mark read join Archive, Delete and Snooze, and resting on a
  row or opening a conversation readies its mail ahead.
- **Time you can see.** Undo offers and send countdowns show a draining bar of the time left,
  and both floats are announced to screen readers. Accessibility checks cover Day, Dusk and Night.
- **The `?` legend is generated from the commands**, so it can't drift from what the keys do.

## Prerequisites

- **Python 3.14+.** Live push uses `imaplib`'s IDLE support, new in 3.14.
- **[uv](https://docs.astral.sh/uv/)** (`brew install uv`), which installs the server's six
  packages, FastAPI, uvicorn, the Anthropic SDK, IMAPClient, icalendar and recurring-ical-events,
  from `pyproject.toml` on first run.
- **[Node](https://nodejs.org)**, which builds the app and runs the frontend checks. `just run`
  builds it first.
- **A Claude API key**, for summaries and the other AI features, as
  `"anthropic_api_key"` in `mailmind.config.json` or in `ANTHROPIC_API_KEY`. Without one,
  AI is off. Set `MAILMIND_MODEL` to pick a cheaper/faster model.
- **Optional:** [`just`](https://just.systems), which wraps the commands below. Without it, build
  the app with `cd web && npm ci && npx vite build`, then run `uv run python -m mailmind serve`.
- **Optional:** the [`op`](https://developer.1password.com/docs/cli/) (1Password)
  CLI — only if you want to keep passwords in 1Password instead of inline.
- **Optional:** [Proton Mail Bridge](https://proton.me/mail/bridge) — only if you
  add a Proton account (it must be installed and running).

## Quickstart

```bash
git clone https://github.com/<you>/mailmind.git && cd mailmind

# To look around first: the whole app on fictional mail, fake servers and a temp data directory.
# Nothing of yours is read or changed, and Ctrl-C ends it. (Needs uv, just and Node.)
just demo                         # http://127.0.0.1:8721   (custom port: just demo 9000)

# 1. Configure ONE account to start (delete the others in the example)
cp mailmind.config.example.json mailmind.config.json
$EDITOR mailmind.config.json

# 2. Verify the credentials resolve (prints ok/fail per account and for the API key, no secrets)
python3 -m mailmind check

# 3. Pull recent mail, then summarize it
python3 -m mailmind sync all 90   # last 90 days (use a big number like 3650 for all-time)
python3 -m mailmind assess 2000 3  # summarize up to 2000, 3 workers

# 4. Run the dashboard
just run                          # builds the app, then http://127.0.0.1:8721   (custom port: just run 9000)
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

Two top-level keys beside `"accounts"` are for the AI. `"anthropic_api_key"` is the Claude API
key, in any credential style. `"ai_daily_limit_usd"` is the most MailMind spends on it in a day,
in US dollars: `15` unless you set another positive number.

An account may say `"pool": true` to keep a connection open between reads, and `"warm_folders": true` to sync its
Drafts, Archive and Sent beside the Inbox. A set `MAILMIND_POOL` or `MAILMIND_WARM_FOLDERS` decides over either.

An account may also say `"imap_adapter": "imapclient"`. `python -m mailmind serve` then reaches
it through the IMAPClient library instead of Python's own imaplib: the new IMAP code, tried on
real mail before it becomes the default. The command line stays on imaplib, removing the key goes
back, and the startup line `[imap] …` says how many accounts asked.

**Per-type notes**
- **Gmail / Workspace** — you need an **App Password** (Google account → 2-Step
  Verification → App passwords), *not* your login password. Defaults to
  `imap.gmail.com:993`, trash `[Gmail]/Trash`.
- **Generic IMAP** — set `host`/`port`. Default is `:993` direct TLS; use
  `"port": 143, "starttls": true` for STARTTLS. Either way the server's certificate
  is verified, so a self-signed one won't connect. The **trash folder name matters** —
  some servers use `INBOX.Trash` instead of `Trash`; a wrong name silently breaks
  sweeps. Run `python3 -m mailmind inventory you@company.com` to see the real names.
- **Proton** — install and run Proton Mail Bridge, then supply the Bridge
  password via `MAILMIND_PROTON_BRIDGE_PASS`, a 1Password item
  (`MAILMIND_PROTON_OP_ITEM`), or `~/.config/mailmind/proton_bridge_pass`
  (chmod 600). Only a server on this machine (`127.0.0.1`, `localhost`, `::1`) skips
  certificate verification, because Bridge's certificate is self-signed. Instead MailMind
  pins the certificate it meets first, in the account's `tls_pins`, and from then on sends no
  password to a server that shows another. If you reinstall or reset Bridge, Settings says the
  certificate changed; **Trust it** pins the new one.

## Two ways to use it

**Web dashboard / daemon** — `uv run python -m mailmind serve [port]` (`just run`). Auto-syncs
every 5 minutes and at once when IDLE hears new mail, and triages what arrived on a schedule of
its own, so a slow model never holds up a sync. With `MAILMIND_PROFILE=read-only` it serves
read-only: it writes nothing to a mail server, so sends, moves and flag changes wait in their
queues until a full run, and it syncs nothing in the background. `just dev` does that.

The app, at `/`, exists once built (`just build-web`, which needs Node and which `just run` does
first). With nothing built, the server says so.

**CLI curation** (`python3 -m mailmind`, or `just`) — works across every account:

```bash
python3 -m mailmind status              # INBOX + Trash counts
python3 -m mailmind inventory all       # every folder + message/unseen counts
python3 -m mailmind plan all            # classify the live inbox, show what would be trashed
python3 -m mailmind sweep you@x.com --dry   # preview a junk sweep (ALWAYS dry-run first)
python3 -m mailmind sweep you@x.com         # move junk → Trash (verified)
python3 -m mailmind rules keep example.com  # learned rule: always keep this sender/domain
python3 -m mailmind rules block spammer.com # always trash (writes a rule)
python3 -m mailmind rules                  # show the learned lists
python3 -m mailmind rules expire github.com 72  # trash this domain's INBOX mail after 72h (0 clears)
python3 -m mailmind expire all --dry       # preview what the expiry rules would trash now
python3 -m mailmind assess --batch         # what has no summary yet, as a batch at half price, within today's limit; again for the answers
```

> ⚠️ **`sweep`, `sweep-stored`, `expire` and `empty-trash` mutate real mailboxes** (move to Trash / purge).
> Always `--dry` first. Every sweep/reconcile is logged to `sweep_audit.jsonl` /
> `reconcile_audit.jsonl`. Moves go to Trash and are recoverable; Gmail purges
> its Trash after 30 days.

## Environment variables

| Variable | Effect |
|---|---|
| `MAILMIND_HOME` | The data directory: everything under [Where your data lives](#where-your-data-lives-all-local-all-git-ignored). Unset, the repo folder, where it has always lived. It must already exist; MailMind never creates it. The server and every `python3 -m mailmind` command read it. |
| `MAILMIND_PROFILE` | `full` (the default) or `read-only`. Read-only writes nothing to a mail server or anywhere else: sends, moves, flag changes and a draft's copy on the server wait for a full run, sweeps and one-click unsubscribes are refused, and nothing syncs in the background. Any other name stops the server from starting. |
| `MAILMIND_WORKERS=account` | Give each account its own lane to its mail server, so one that hangs (Bridge stuck, a host that stops answering) no longer holds up the others. Unset, all accounts share one, as before. Being tried out before it becomes the default. |
| `MAILMIND_NOSYNC=1` | The older switch for `MAILMIND_PROFILE=read-only`, which it wins over. |
| `ANTHROPIC_API_KEY` | Call the Claude API for every AI feature, billed to that key's organization. `"anthropic_api_key"` in `mailmind.config.json` wins over it. Neither set: AI is off. |
| `MAILMIND_LOG_LEVEL=DEBUG` | Show debug lines and each error's text and traceback on the terminal, for chasing a fault. They can hold addresses and subjects. `mailmind.log` never does. |
| `MAILMIND_MODEL` | Override the Claude model (default `claude-opus-5-5`), e.g. `claude-haiku-5-5` |
| `ANTHROPIC_BASE_URL` | Send API calls somewhere other than `https://api.anthropic.com` (a proxy). |
| `MAILMIND_POOL=1` | Reuse persistent per-account IMAP connections instead of connecting per op — **much** faster repeated reads. Off by default; set to `1` for all accounts, or a comma-separated list of emails. Proton Bridge is excluded from the `1` catch-all and must be listed explicitly. |
| `MAILMIND_WARM_FOLDERS=1` | Also sync each account's Drafts / Archive / Sent alongside INBOX, so search and threading cover more than the inbox. Off by default; same `1`-or-list form. |
| `MAILMIND_RECONCILE_SECS` | Target seconds for one full ghost-prune cycle across all accounts (default `1800`; `0` disables). |
| `MAILMIND_EXPIRE_SECS` | Seconds between expiry-rule passes (default `3600`; `0` disables). Never runs read-only. |
| `MAILMIND_NO_DRAFT_TICK=1` | Disable the background reply pre-drafter (also implied by read-only). |
| `MAILMIND_NO_BATCHES=1` | Triage a backlog message by message at full price instead of sending it as a batch at half price. A batch already out is still brought in. |
| `MAILMIND_NO_SCHEMA=1` | Ask the model for free-form JSON instead of using structured-output schemas — a fallback for models without structured output. |
| `MAILMIND_PROTON_BRIDGE_PASS` | Proton Bridge password (highest precedence) |
| `MAILMIND_PROTON_OP_ITEM` | 1Password item id holding the Bridge password |

A switch (`MAILMIND_NOSYNC`, the `MAILMIND_NO_*` ones, and `MAILMIND_POOL` and `MAILMIND_WARM_FOLDERS` when
they list no addresses) is on for `1`, `true`, `yes` or `on` and off for `0`, `false`, `no`, `off` or nothing,
in any case. A value a variable doesn't take is named in the log, the variable and never the value, and reads
as the side that writes, sends or spends less: `MAILMIND_NOSYNC` turns read-only on, `MAILMIND_NO_DRAFT_TICK`
turns drafting ahead off, an address list's entry that is no address names no account, and the rest read as
if unset, but for `MAILMIND_PROFILE` (above). `mailmind/platform/environment.py` declares each variable.

The server's port is the first argument to `serve` (default `8721`).

`MAILMIND_POOL` and `MAILMIND_WARM_FOLDERS` are both **off by default**, so a plain
run connects fresh for every operation and syncs INBOX only. `just run-fast` sets
both for that run. To keep them on, give an account `"pool": true` or `"warm_folders": true`
in `mailmind.config.json`; a variable that is set decides over the file.

## Where your data lives (all local, all git-ignored)

In the repo folder, or in the directory `MAILMIND_HOME` names:

- `mailmind.db` — SQLite store of messages, bodies, attachments + summaries.
- `mailmind_snippets.json` — your saved snippets, written whole, readable by you only.
- `mailmind_rules.json` — your learned keep/block lists, expiry rules and filters. It's written whole or
  not at all, readable by you only. A hand edit that breaks it leaves the rules last read in
  force, and the next save keeps the broken copy in `backups/`.
- `mailmind_views.json` — your saved searches / smart-folder lanes.
- `sweep_audit.jsonl` / `reconcile_audit.jsonl` — audit trails.
- `mailmind.log` (and `.1` to `.3`, older) — what the app said of its own running, one JSON object a
  line, readable by you only. It holds ids, counts and the kinds of error, never an address or a subject.
- `backups/` — the database as it stood before each upgrade (the newest three, readable by you
  only), and any rules file that wouldn't parse.
- `mailmind.config.json` — your accounts + credentials.

All of the above are in `.gitignore`, and a `MAILMIND_HOME` outside the repo keeps them out of
it altogether. **Nothing personal is committed.**

## Testing

```bash
uv run pytest tests/unit/curation/test_classifier.py        # behavioral classifier suite
uv run pytest tests/security/test_sanitize.py               # HTML sanitizer: 24 attack classes, expects 0 leaks
uv run pytest tests/unit/content/test_css_sanitizer.py      # CSS sanitizer unit tests
uv run pytest tests/unit/curation/test_expire.py            # expiry rules: storage, matching, age selection
uv run pytest tests/unit/calendar/test_upcoming.py          # Today & next: stored invites, repeats, updates, cancellations
uv run pytest tests/unit/calendar/test_invite_text.py       # what an invite's text comes to: who a reply goes to, its dates
uv run pytest tests/contract/test_ics_reader_contract.py    # reading an invite: nothing raises, nothing costs much
uv run pytest tests/{unit,integration}/discovery/test_contacts_known.py   # "new" recipients lookup: matching, drafts, thin history, CSRF
uv run pytest tests/unit/compose/test_send_after.py         # send-later times: passed or far-off ones are refused
uv run pytest tests/{unit,integration}/compose/test_draft_resume.py   # reopened drafts: text and quote, files, Bcc, threading, refusals
uv run pytest tests/security/test_hardening.py              # security and data-loss fixes: TLS, other sites, folder names, filing
uv run pytest tests/security/test_mail_frame.py             # a mail body's own document: its policy, who may frame it, what it holds
```

These need [uv](https://docs.astral.sh/uv/). With [`just`](https://just.systems) installed too, `just test` runs all of them
together with the rest of the pytest suite in `tests/`. That suite drives the app against fake IMAP,
SMTP and Claude servers and a made-up mailbox, and compares the answers with recorded ones in
`tests/golden/`. `just verify` adds lint (ruff, the import rules between the package's layers, and pyright), byte-compile, the
frontend checks (the page's API types and limits against the server's, which `just gen-api` writes from the routes' OpenAPI
document and the server's limits, then the TypeScript's types, lint and tests, which need Node) and a look for known
vulnerabilities in what the lockfiles pin.
`just test-full` also runs the browser tests (`just test-browser` runs only those), which click
through the app in Chromium, and `just test-dovecot` holds MailMind's IMAP code to the same promises against a
real Dovecot server in Docker. `just setup` installs all of it, and `just` on its own lists every recipe. uv installs those dev
tools from `pyproject.toml` on first use, as it does the app's own packages.

## Privacy & contributing

This is a personal-data app. **Never commit** `mailmind.config.json`,
`mailmind_rules.json` (it holds your personal keep/block entries), `mailmind.db`,
the audit `*.jsonl` files, or screenshots of real mail — the `.gitignore` covers
them; don't `git add -f` around it. PRs welcome. A package the server needs is declared in
`pyproject.toml`, and one the app needs in `web/package.json`.

## License

MIT — see [LICENSE](LICENSE).
