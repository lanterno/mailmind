# MailMind

**A local-first email client with an AI layer.** MailMind logs
into your mailboxes over IMAP, throws away the junk, AI-summarizes everything
that matters, and gives you a fast dashboard to read, triage, search and *send* —
so you read *summaries of what's important*, not your inbox.

**[Get the Mac app](https://github.com/lanterno/mailmind/releases/latest)**, for macOS 15 or later on Apple silicon or
Intel: no developer tools needed. [Install it](#install-the-mac-app) in a few steps; [if something goes
wrong](#if-something-goes-wrong), Help › Copy Diagnostics has what to send; and [Uninstall](#uninstall-the-mac-app)
takes it all away again.

It started as a summarizer with a reading pane and is now a full mail client:
compose and SMTP send with a durable outbox and Undo Send, conversation
threading, attachments, full-text search with an operator grammar, IMAP IDLE
push, snooze/mute/waiting, and AI that summarizes new mail by itself, writes the day's brief as you
open the Inbox, and otherwise waits for you or for a schedule you turn on.
See [Features](#features) for what ships today and
[ROADMAP.md](ROADMAP.md) for what doesn't yet.

- **Little to install.** Python, [Node](https://nodejs.org) to build the app, and eight packages
  (FastAPI, uvicorn, the Anthropic SDK and its httpx2, IMAPClient, icalendar, recurring-ical-events and sqlcipher3) that
  [uv](https://docs.astral.sh/uv/) fetches on first run.
- **Your data stays local.** Mail is stored in an encrypted SQLite file on your machine; the only thing that leaves it
  is the mail the AI works on, sent to the provider you chose (Claude by default), and nothing at all with AI mode off,
  as a fresh install starts unless a key for AI is already there. With AI mode on, the sender, subject and opening
  text of each message in your Inbox go to the provider you chose (Anthropic, OpenAI, Google, or the service you
  named) to be summarized, automatically: the last 30 days’ as soon as you turn it on, then each new one. Those
  senders, subjects and summaries go again to write the day’s brief and what came in since you looked. Whole
  conversations go only when you ask: for a summary or a draft, with what you type into Ask, a search written as a
  sentence, or Rewrite. Attachments never leave this Mac. See [what AI sends](#ai-intelligence) for a model on this
  Mac and drafting ahead.
- **Any mailbox.** Gmail and Google Workspace, iCloud, Yahoo, AOL, Fastmail, any other IMAP server
  (cPanel/Dovecot/…), and Proton (through its Bridge), added in Settings from the address. Outlook and Hotmail aren't
  supported yet ([#47](https://github.com/lanterno/mailmind/issues/47)).
- **It learns.** A keep/block rules layer remembers your decisions so the same
  sender is never re-triaged.

> **Heads-up:** summaries and the other AI features need a Claude API key, kept in your login Keychain with `uv run python -m mailmind ai key`, or in `ANTHROPIC_API_KEY`, unless you choose another provider in Settings; calls are billed per token to that key's organization. With no key in `ANTHROPIC_API_KEY` and none for any provider named in `mailmind.config.json`, AI mode starts off: MailMind still ingests and curates mail, sends nothing to any AI provider and offers nothing only AI can do, until AI mode is turned on in Settings. On without a key, the brief says AI is off and how to set it up.

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
   mailmind/assistant/         summarize + score importance 1–5 with the AI chosen
        │
   mailmind/http/  +  web/ (the React app)   the app on http://127.0.0.1:8721, served by
                                       `uv run python -m mailmind serve` (FastAPI on uvicorn)
```

Around that spine sit the client modules: `mailmind/mailbox/` (IMAP writes, IMAP IDLE),
`mailmind/compose/` (send), `mailmind/discovery/` (FTS + operator
grammar), `mailmind/attention/` (snooze, mute, reply later, follow-ups),
`mailmind/content/` (reading a message, and the reader's whitelist sanitizer),
`mailmind/calendar/` (invites), and `mailmind/assistant/` (everything asked of the AI).

The app is one view: a real folder-and-account client whose Inbox opens on
today's AI brief. Curation tools (sweep junk, keep/block rules, audit) live in a
slide-in drawer, and the sync menu opens it and runs a reconcile.

## Features

MailMind started as a summarizer with a reading pane. It is now a full local-first
mail client — everything below ships today and runs against your local SQLite store
and the AI provider you chose (Claude by default).

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
  brings it back, so neither pays for a second call. Rows are grouped by day. A row
  of your own mail, in Sent, Drafts, a conversation or a search, names whom it went
  to ("To Ada, Lin +2") where another's names who sent it.
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
- **A big folder opens at once.** The first time you open a folder, MailMind reads it in
  the background. The list waits a few seconds for it, then says "Reading this folder for
  the first time…" and fills in as it reads; a folder it couldn't read says so, with a
  Retry. In the Mac app (the Rust server) the first read stores the folder's newest 500
  messages, which show then, and reads the rest newest first in the background; the older
  Python server reads it whole, and fills in when the scan ends.
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
- **Newsletters render in their real design.** Inline styles and `<style>` blocks, the head's
  among them, survive a whitelist sanitizer, which shows what a browser shows of what it drops (a
  `<noscript>`'s text, an `<object>`'s fallback, the text after a nested form), while scripts, remote fetches, trackers and
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
- **Links that keep their word.** What is open is named by the message's key, the
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
- **Sweep an inbox's junk, with a look first.** Curate's **Sweep** lists what would go at once, from what MailMind
  has stored of the Inbox (or from the server, until the account's first scan has read it all): each junk bucket with
  its count, the senders with the most of it and its newest subjects. Confirm reads the Inbox on the server before
  anything moves, and moves only what is still junk, read and unstarred there, none of it older than what the list
  could name. Starred and unread mail always stays, and the plan says how much; the button counts only what would go. A
  sweep takes the newest 500 at a time (sweep again for the rest), and one `z` puts it back from the Trash, an
  account's at once (the plan says how much of it, if any, has no Message-ID to find it by and can't come back). A
  sweep the server stops says so in plain words: try again, or, if some went first, Undo brings that back; and when it
  can't ask the server again how much went, it says so and Undo puts back whatever did.
- **Unsubscribe & sender cleanup.** A ranked "senders flooding you" lane where one
  confirm blocks the sender, sweeps their INBOX copies to Trash (one `z` reverses
  both) and fires one standards-compliant unsubscribe: RFC 8058's one-click POST where the
  sender offers it, else a mailto from each account that got the mail, else the sender's page,
  opened in the browser. The way out comes from the headers ingest keeps, so a sender whose mail
  was never opened can still be left, and a second try after the sweep still knows it. A mailto goes through the
  outbox, so a server that's away only makes it wait, and it's filed in Sent. A preview stands for ten minutes and
  for the way out it showed: after that, or if the list changed it, the block and sweep go ahead and nothing is sent.
- **Expiry rules.** Give a sender or domain a shelf life — `uv run python -m mailmind rules expire
  github.com 72` — and its INBOX mail moves to Trash once it is older than that. The
  server checks hourly; `uv run python -m mailmind expire --dry` previews. Expiry is independent of
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
  any the server won't give — so search covers what your mail says, not just its headers. A sync keeps
  each Inbox message of under 64 KB whole as it arrives, so search finds what it says and it opens with
  no wait. On the Rust server and the Mac app the backfill asks for a folder's dozen at a time over the
  account's kept connection and reaches back twelve months: older mail is found by its sender, subject and
  the like until you open it, which fetches it. It keeps only the text of a message over 256 KB, whose
  attachments come when you open it, and asks for nothing you opened, moved or deleted while it waited its turn.
- **A safe operator grammar:** `from:` `to:` `cc:` `subject:` `body:` `label:`,
  `is:unread|read|starred|answered|junk|newsletter|receipt|notification|snoozed` (`is:flagged` is `is:starred`),
  `bucket:` `category:`, `has:attachment`, `in:` `account:`,
  `before:`/`after:`, `"phrases"` and `-exclusion`. `to:`/`cc:` hit real indexed
  recipients rather than the body text. A date means midnight where you are. Snoozed
  mail stays out of sight until it wakes, unless you ask for `is:snoozed`. The grammar
  is advertised in the search placeholder and documented in the `?` legend.
- **Search that persists.** Opening a result keeps the results as your working list —
  archive, snooze, mute, star, bulk and swipe all heal the results in place and
  auto-advance to the next hit.

### AI intelligence

Everything here runs through the AI provider you chose (Claude by default) and returns
**schema-validated** structured output, so malformed-response errors don't reach the UI.
AI mode starts off on a fresh install, unless `ANTHROPIC_API_KEY` is set or `mailmind.config.json` names a key for
any provider, and while it is off nothing goes to any provider. What it sends once it is on,
as Settings, Help and the Mac app's first run say before you turn it on:

> With AI mode on, the sender, subject and opening text of each message in your Inbox go to the provider you chose
> (Anthropic, OpenAI, Google, or the service you named) to be summarized, automatically: the last 30 days’ as soon as
> you turn it on, then each new one. Those senders, subjects and summaries go again to write the day’s brief and what
> came in since you looked. Whole conversations go only when you ask: for a summary or a draft, with what you type into
> Ask, a search written as a sentence, or Rewrite. Attachments never leave this Mac.

With **Draft replies ahead** on: "With Draft replies ahead on, whole conversations of important mail go too, to draft
replies before you open them." With a model on this Mac (or a service at an address on it), the words are "nothing
leaves this Mac", unless the model is one of Ollama's cloud models (its name ends in `-cloud` or `:cloud`, and Ollama
runs it on its own servers). Then they are, for one named `gpt-oss:120b-cloud`: "gpt-oss:120b-cloud runs in Ollama’s
cloud, so what is summarized goes to Ollama’s servers."

So three things go by themselves, and each reads only the Inbox: the summaries of its mail from the last 30 days, the
line on what came in since you looked as the new-mail pill shows, and the day's brief as the Inbox opens when none is
stored, the last two written from the senders, subjects and summaries already made. Mail you sent, drafted, deleted or
filed elsewhere goes only when you ask, or with a switch you turned on in Settings › AI: **Draft replies ahead for
important mail** sends whole conversations, your own replies in them too, and **Have last week’s review ready each
Monday** sends the subjects and recipients of the threads you are waiting on, with the names of the week's meetings
from invites in any folder. Everything else waits for you to ask, or for one of those switches.

- **The Claude API.** With an API key set, the server and every command call the Messages API
  through the official Anthropic SDK (`claude-opus-5-5` unless you choose another in Settings or
  `MAILMIND_MODEL` names one, which wins;
  per-message triage runs at low effort, everything else at medium). Background summaries ask the
  provider's small model (`claude-haiku-5-5`, OpenAI's `gpt-6-luna`, Gemini's `gemini-3.5-flash-lite`;
  a model on this Mac or a compatible service, the one chosen), read only the Inbox, and reach back only
  30 days: older mail, and mail filed anywhere else, is summarized when you ask for a summary. Settings' Spending card names both models and how many messages wait.
  Without a key, AI is off and nothing is sent. A safety-classifier decline on a mail body is
  re-run on another model server-side, and rate limits and overloads are retried.
  `uv run python -m mailmind ai key` keeps the key in the login Keychain and points `"anthropic_api_key"` in
  `mailmind.config.json` at it (`POST /api/ai/key` does the same from the Mac app, and `POST /api/ai/key/clear`
  takes it out; or name it there with `{"op": …}` / `{"env": …}`, like an account password), and
  that wins over `ANTHROPIC_API_KEY`, so a key your shell exports for other tools isn't used by
  accident. The server's startup line names the model and where the key came from.
  **Or another provider.** Settings has AI mode, which turns AI off altogether, every background job
  included: it starts off on a fresh install, and on where a key for AI is already there. On, it offers
  a choice of provider: Claude (the default), OpenAI, Google Gemini, a model on this Mac (Ollama,
  LM Studio, llama.cpp, vLLM, Jan or GPT4All, which Settings finds running and lists with their models,
  and with none running, links to Ollama with one model to start with and its context length to set;
  nothing leaves the machine, unless the model is one of Ollama's cloud models, which the AI status says) or any other OpenAI-compatible service at a URL you give, each with a
  model of your choosing from the list the provider offers. Each provider's key is kept in the login
  Keychain like Claude's (`ai key --provider openai`; a model on this Mac needs none); `ai on` and
  `ai off` turn AI mode on and off from the command line. A reasoning model's thinking is left out of
  what it answers, and a model on this Mac is given five minutes to load. Every provider goes through
  the same daily limit and usage ledger, priced from its own list (a model on this Mac costs nothing),
  and only Claude takes batches: choosing another drops a batch that is out (MailMind asks nothing more
  of Anthropic for it, so it isn't deleted there, and what it costs is still billed but never counted
  against the daily limit), and its messages wait for the provider chosen.
  The sync menu names the model, and in red the reason ("the API key was rejected",
  "rate-limited for now", "the Anthropic account has no credit left") when the last call failed.
  Each paid provider's key card says it is billed per use by that provider, separately from any
  ChatGPT or Claude subscription, and where to add credit. Settings says what AI mode sends (above) before it
  is on, and so do Help and the Mac app's first run. With AI mode on and no key, the brief says
  AI is off calmly and the composer drops Draft and Rewrite. The brief's card says what AI mode sends
  as Settings does, and the sync menu with AI on says it in short.
  Every call is counted by day and by feature (triage, thread summaries, the brief, drafts,
  …) at the provider's list prices. The sync menu shows what today cost so far against the
  daily limit, and `uv run python -m mailmind ai usage --days 30` (`just ai-usage`) lists each day.
  Settings' Spending card shows the last 7 days and this month by feature, and says the provider's
  console holds the bill.
  A backlog (a first import, or after an outage) goes to the Message Batches API at half price,
  one batch at a time, while new mail is still triaged as it arrives, so a large import can leave
  older mail without a summary for up to a day. Anthropic holds a batch until MailMind has brought
  its answers in, then MailMind deletes it (all but one dropped for another provider, above). A
  batch whose answers are in but that isn't deleted yet waits at Anthropic, holding mail, until
  Claude is the provider with AI mode on again. The sync menu says how many messages are out.
  MailMind spends at most $15 a day unless `"ai_daily_limit_usd"` in `mailmind.config.json`
  names another amount. At the limit, triage, pre-drafting and "since you last looked" stop until the next day,
  anything you ask the AI for asks first whether to go over, and a brief not yet written waits
  for you to ask for it. Background work waits while you
  use the AI and for 45 seconds after. Pre-drafting runs only if you've used the app in the
  last 45 minutes. If the API keeps rejecting a request, background work stops until the provider,
  the model, the URL or the key changes, or AI mode is turned off and on, while what you ask for
  still goes and shows the error.

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
  pre-drafts survive restarts. It is a switch, "Draft replies ahead for important mail" in
  Settings › AI, off until you turn it on. The ▾ beside Draft picks a tone (Brief, Balanced, Warm or
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
- **Last week's review, ready on Monday (opt-in).** Turned on in Settings, the Weekly Review of the week just ended is
  written on Monday morning, within the daily AI limit, and kept in MailMind's own database: Your week opens on it all
  week without waiting or spending, says which seven days it looked back on, and ↻ writes a fresh one. Nothing is ever
  sent as mail.
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
  with **Send now** and **Cancel**. It goes while MailMind is open, and in the Mac app Send later and the outbox
  say so.
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
  whether the server is there or not, and read-only too, where it stays on this Mac until you save it again in a full
  run. The server's copy follows when you close the message or save, and at most once a minute while you type, and a
  short reply sent within the minute never touches your Drafts folder. A draft that can't reach its server waits in
  the outbox panel, with Edit and Discard, and goes by itself once it can. A discard waits the same way, and a crash
  mid-save never leaves a second copy.
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
  mail when you open it, but writes nothing to a mail server. Its connections can only read, and on either server
  everything that would reach a mail server is refused at once, saying "MailMind is running read-only", before
  anything is queued or leaves the list: a send (new, a reply, Send later), the outbox's Retry and Send now, filing
  (move, trash, archive or junk; one message, a conversation or a selection), read marks and stars, a mute, a snooze,
  an RSVP, an unsubscribe by mail, a cleanup, a bulk action the AI planned, a sweep, making, renaming or deleting a
  folder, and an Undo that would have to ask a server. Nothing you ask for waits for a normal run, and a sync, a
  filter, a muted thread or a snooze waking queues nothing either; what a normal run queued earlier still waits for
  the next one. What changes only this Mac still works: reading, rules, AI, cancelling a queued send, and saving a
  draft, which is kept on this Mac (the outbox panel says "Kept on this Mac") and goes to its server only when you save
  it again with read-only off: a normal run never pushes it by itself. No sync runs in the background and IDLE
  doesn't listen for new mail, though a sync you ask for still reads your mail, a folder you open for the first time
  is still read, and snoozed mail still wakes, here alone. On the Rust server and in the Mac app, a first scan also
  goes on reading its older mail in the background. AI makes no summaries in the background and writes nothing ahead;
  the day's brief as the Inbox opens and the line on what came in since you looked still go, written from summaries
  already made. The sync pill says Read-only.
  The Mac app has it as a switch in Settings › General ("Read only: MailMind changes nothing on your mail servers"),
  which it applies the next time it opens, offering to open it again; its welcome screen mentions it. Only your own
  run reads the switch: a demo or test run (one that names its own data folder) runs as it was started.
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
- **Each account keeps its IMAP connection.** On the Rust server and the Mac app, opening a message or a folder,
  filing, undo, making, renaming or deleting a folder, the outbox and the body backfill share one logged-in connection
  an account, never two jobs at once. It is checked before each use, replaced after ten minutes or 200 uses (90 seconds
  or 40 for Proton Bridge, with its low connection cap), dropped when the Mac wakes from sleep, and logged out when you
  change or remove the account, or rename or delete the folder it has open. Quitting logs them all out at once and
  waits four seconds at most, so a Mac whose network has gone still quits promptly. The Python app keeps them only
  with `MAILMIND_POOL=1` or an account's `"pool": true`.
- **A hung server costs a click about twelve seconds, not two minutes,** and Undo is offered as soon as it answers;
  the worker keeps trying. Two processes changing your rules can't lose each other's change.
- **A first read counts as it goes.** A folder read for the first time says how many messages have arrived so far.
- **A new account's Inbox fills newest first.** On the Rust server and the Mac app, its first sync stores the newest
  500 messages and shows them at once, then reads the rest in the background, 500 at a time and newest first, the
  list filling as they come; quit meanwhile and it goes on where it stopped, and a Mac waking from sleep goes on at
  once. The status says how far it has got ("Reading your inbox: 2,300 of 41,000"), a message opens while it reads,
  and one account's slow server holds up no other. It reads each message's first 64 KiB for its preview, keeping a
  message that fits in them whole, and downloads no attachment. On either server, mail that was there before is no new mail: no notification, no badge,
  no "N new".
- **Adding an account starts at its address.** MailMind knows Gmail, iCloud, Yahoo, AOL, Fastmail and Proton by an
  address's domain, or by the mail servers its domain names, and fills in their servers, with the page where the
  provider's app password is made beside the password. Gmail shows the three steps to an App Password and takes it as
  Google shows it, spaces and all. Any other address is Other IMAP, with its incoming and outgoing (SMTP) servers
  typed; editing one leaves an outgoing server it never saved following its incoming host. Microsoft's accounts are
  recognised, and told they can't be added until MailMind can sign in to Microsoft
  ([#47](https://github.com/lanterno/mailmind/issues/47)). A test of an account logs in at its outgoing server too, and
  a connection is tested as soon as it is saved; the Mac app's first run tests it before saving. A Proton account logs
  in with the Bridge's password, which only a saved account is tested with, so a new one is tested once it is added.
- **A failed login says what to do next.** In plain words, by what went wrong: a password refused (with the provider's
  app-password page), a server that didn't answer, a certificate not trusted, Proton Bridge not running, or an outgoing
  server that refused what the incoming one took. On the Mac, an account's Sync card says it once and puts the fix
  beside it: **Enter App Password…**, **Change Password…** or **Edit Server…** opens the account on that field, tries
  what you type before keeping it, and syncs again once it logs in. A test that fails the same way says only that.
  What kind of failure it was goes in **Help › Copy Diagnostics**; the page shows it beside the sentence in small type.
- **Trash is found, not assumed.** A delete, a sweep and an expiry rule put mail in the account's Trash where its server
  lists it, else in the folder the server marks as Trash (`\Trash`), else in one named like one, so a Gmail in another
  language and iCloud's "Deleted Messages" work as they are, and Undo brings a delete or a sweep back from there.
- **Removing an account can take its mail off this Mac.** Settings asks, ticked unless you untick it, whether the mail
  MailMind keeps of the account goes too: its messages with their bodies and attachments, what was said of them, its
  Undo entries, the sweep and reconcile records of it, and the AI's cards made for it. A sync reading the account, or a
  read of its older mail, stops at its next message and keeps nothing, not even the trouble its syncs had, so the
  account added again starts afresh; every open window drops the account at once. Its mail on the server stays as it
  is. In the Mac app the dialog warns first when the account has sends that haven't gone or drafts not yet on its
  server, which are only on this Mac, and says that copies in MailMind's backups folder stay there: newer backups
  replace them in time, and a copy Start Fresh kept stays until you delete it.
- **Per-account sync health.** Settings shows last successful sync, sync count, or a
  coarse error kind per connection, with a `↻ Retry` button — plus a dismissible
  banner naming any accounts that couldn't connect. Trouble that may pass (a connection lost after sleep or a change
  of Wi-Fi, a timeout, a server busy for now) shows only once the next sync fails too, or three minutes on; a refused
  password or a changed certificate shows at once.
- **One thing breaking stops nothing else.** On the Rust server and the Mac app, a sync, a send, a change or a read of
  older mail whose code breaks part way ends as one that failed, is logged by where it broke, and is tried again as
  such, and the rest of the background work goes on.
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

- **Your mail is encrypted on disk.** The database, its log, its backups and a database export are encrypted with
  SQLCipher (AES-256), with a key MailMind makes once and keeps in your login Keychain, so a copy of the file, in a backup
  of the disk, a sync folder or anyone else's hands, can't be read without it. Your rules, saved views, snippets and the
  audit trails are in it too, never in a file beside it. Every file an export writes is readable by you only.
- **Your browser keeps nothing of your mail.** The browser writes every address the app goes to into its history, in
  plaintext, and may sync it, so no URL names an account, a folder, a search or a topic: a view that holds one is a
  place, `/p/` and a random id, whose meaning stays in the encrypted database. Back, a reload and a link opened in a new
  tab still come back to it, and the browser's storage holds no address either.
- **Your passwords are in the Keychain, never in a file.** A password you type in Settings, or give
  `uv run python -m mailmind accounts password you@example.com`, and each AI provider's API key (`ai key`) go to your login
  Keychain; `mailmind.config.json` only says where each one is. A config that holds a password itself is refused.
- **One-click local backup** of your whole mailbox as a consistent `.db` snapshot, encrypted as the database is, from
  Settings, or a zip of lossless per-message `.eml` files or standard `mbox` archives, there
  or from the command line (`uv run python -m mailmind export --format eml --out DIR`). Read-only — it never
  touches IMAP. Settings (General, in the Mac app) also says how much room the database takes on this Mac.
- **Upgrades can't cost you mail.** Before a new version changes the database's layout, MailMind
  backs it up beside it (the newest three kept, encrypted and readable only by you), and each step of the change
  lands whole or not at all. `uv run python -m mailmind db check-upgrade` tries the next upgrade on a copy
  first and reports only row counts and integrity checks, never a message. The Rust server makes and upgrades the
  database the same way, and `mailmind-server db check-upgrade` is its check.
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
- **Three themes in the Meadow palette, and Automatic** — a cream **Day**, a warm
  **Dusk** and a deep-blue **Night**, or **Automatic** (the default on a fresh install): Night
  while your computer is in dark mode, else the time of day's, Day from 6, Dusk from 17 and
  Night from 21. A theme you chose before stays chosen. They're cycled by the sun button in the top bar (or
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

## The Mac app

MailMind has a native Mac app over the Rust core (`shells/apple`). It runs the whole of MailMind in its own process,
with no port open: the mailbox and search, reading and writing, Settings and the first run, and the assistant,
curation, attention and the calendar, as the page has them, with the menu bar's commands and a Mac's keys. It wears
the page's Meadow look: paper panels on a painted sky in Day, Dusk, Night or Automatic (in high contrast, with solid
panels, when you ask in Settings or macOS's **Increase Contrast** is on), the page's typefaces and icons, a rail
that folds to icons, the summary note and a reply under the message, the brief beside the Inbox, its greeting under a
line on how your mail stands (what needs you and what's unread). It reads and
organises as a Mac's mail app does: **File › Print** (⌘P) prints the open message, with the images you chose to show,
⌘F goes to the search field and **Find in Message** (⌥⌘F, or ⌘F in a message's own window) finds a word in what's
open, a double-click opens a message in a window of its own (with the reader's single keys and cards), a link says
where it goes in a line at the foot while the pointer is over it, and rows dragged onto one of the rail's folders move
there. Every place on the rail has a menu
(**Get New Mail** where mail shows). **Move To** makes a new folder and moves into it, an account's folders make one
in their menu (**New Folder…**), and a folder of your own has **Rename Folder…** and **Delete Folder…** there (never one the server
keeps, as Gmail's Starred); one that holds mail is deleted only once you say so again, its mail moved to Trash first,
or on Gmail, where a folder is a label, left in All Mail. When one message comes, its notification says who sent it
and the subject, and a
click opens it, as a meeting's reminder opens its invite, reopening the window if you closed it. It can be
your Mac's mail app (**Mail › Settings › General › Default email reader**), so a mailto: link opens its composer, and
**Check mail against the server**, in the status menu, drops at once what another app moved or deleted.

It sends and syncs only while it is open. **Settings › General › Open at login** opens it as you log in, and
quitting saves whatever you are writing as a draft, sends a message still in its Undo window first, and asks before
leaving one scheduled for later. Its window
closed and opened again from the Dock shows the mail already open, and when the Mac wakes or its network changes the
mail syncs at once.

### Install the Mac app

MailMind runs on macOS 15 or later, on a Mac with Apple silicon or an Intel processor (**Apple menu › About This Mac**
says which you have), and needs nothing else installed.

1. Open the [latest release](https://github.com/lanterno/mailmind/releases/latest), download `MailMind-<version>.dmg`
   under **Assets**, and open it.
2. Drag **MailMind** onto **Applications**.
3. Open MailMind from your Applications folder. The first time, macOS stops it: MailMind is signed ad hoc, not yet
   with an Apple Developer ID, so macOS can't check who made it. It says *"MailMind" Not Opened*: click **Done**.
4. Open **System Settings › Privacy & Security**, scroll down to **Security**, and click **Open Anyway** beside
   *"MailMind" was blocked to protect your Mac*. Click **Open Anyway** again in the dialog that follows, and enter your
   login password or use Touch ID. (Control-click › Open no longer does this, since macOS 15.)

MailMind opens, and from then on it opens like any other app. It updates itself: it asks before its first check, and
**MailMind › Check for Updates…** checks at once.

Before you add an account:

- **Gmail** takes an App Password, not your Google password: Settings shows the steps and links the page that makes
  one. **iCloud, Yahoo, AOL and Fastmail** each take an app password of their own too, linked the same way.
- **Proton** needs [Proton Mail Bridge](https://proton.me/mail/bridge) installed and running, which takes a paid
  Proton plan; MailMind logs in with the Bridge's password.
- **Outlook, Hotmail and Microsoft 365** aren't supported yet: they need Microsoft's own sign-in
  ([#47](https://github.com/lanterno/mailmind/issues/47)), and Settings says so before it asks for a password.
- **AI is off** until you turn it on in **Settings › AI**, which says first what it would send, and to whom. It needs a
  key from the provider you choose, billed by them per use, or a model on this Mac.
- **Read only**, in **Settings › General**, lets you look around with MailMind changing nothing on your mail servers.

### If something goes wrong

- **Help › Copy Diagnostics** copies what the owner needs to see why: the version, this Mac, how each account is set
  up, what failed lately and the log's last lines, with no address, subject, name or password in it, and a mail server
  named only when it is a provider's (Gmail, iCloud, Yahoo, AOL, Fastmail, Outlook) or this Mac's own, any other as
  "own server". Paste it into a message to whoever gave you MailMind.
- **Help › Report a Problem…** copies the same, and offers to open a new issue on GitHub to paste it into.
- **The log** is `mailmind.log` in MailMind's data folder, `~/Library/Application Support/io.github.lanterno.mailmind`
  (in Finder, **Go › Go to Folder…** and paste that), with older lines in `mailmind.log.1` to `.3`. It says what
  MailMind did and the kinds of error, never an address, a subject or a password, so it is safe to send.
- **If MailMind quits unexpectedly**, macOS keeps a crash report in `~/Library/Logs/DiagnosticReports`, named MailMind
  and the date and time: send that file too.
- A sync that fails says why in **Settings › Accounts**, with what to do next, and background work that keeps failing
  says so in the sync menu. If MailMind can't open its copy of your mail, its window says why and what to do, with
  **Copy Details**, **Show in Finder** and, where a fresh copy would help, **Start Fresh…**, which moves that copy into
  `backups/` and reads your mail from its servers again.

### Uninstall the Mac app

Removing an account (**Settings › Accounts**, then **Remove**) takes its password out of the login Keychain and, unless
you untick it, its mail off this Mac: its messages with their bodies and attachments, what the AI said of them, its
Undo entries and the records of its sweeps. Its mail on the server stays as it is. The copies of MailMind's database in
`backups/` still hold the account's mail as it was then. The copy made before each upgrade changes the database keeps it
until newer copies replace it (the newest three are kept). A copy **Start Fresh** moved aside keeps it until you delete
it, since nothing prunes it. Step 4 below removes them all.

To remove MailMind and everything it keeps:

1. If **Open at login** is on, turn it off in **Settings › General**.
2. Quit MailMind (**MailMind › Quit MailMind**, ⌘Q).
3. Move **MailMind** from Applications to the Trash.
4. In Finder, choose **Go › Go to Folder…**, paste `~/Library/Application Support/io.github.lanterno.mailmind`, and
   move that folder to the Trash. It holds MailMind's copy of your mail, its backups, its settings and the log.
5. Open **Keychain Access** (search for it with Spotlight, ⌘Space), type `mailmind` in its search field, and delete
   each item named mailmind: the key your copy of the mail was encrypted with (account `database-key`), each mail
   account's password (`account:` and the address), any AI provider's key, and the Proton Bridge's password.
6. Empty the Trash.

If Open at login was on and you skipped step 1, remove MailMind in **System Settings › General › Login Items &
Extensions**. MailMind's own preferences (the read-only switch, how the window was laid out) hold nothing of your mail;
they are anything named `io.github.lanterno.mailmind` in `~/Library/Preferences` and `~/Library/Caches`, and can go
too.

How a release is made is in [docs/releasing.md](docs/releasing.md).

## Prerequisites

- **Python 3.14+.** Live push uses `imaplib`'s IDLE support, new in 3.14.
- **macOS**, whose login Keychain keeps the key the database is encrypted with, your account passwords and the Claude
  API key. Elsewhere, set `MAILMIND_DB_KEY` to 64 hex digits of your own (`openssl rand -hex 32`) and keep them safe:
  they are the only way into the database. Passwords can then come from 1Password or the environment.
- **[uv](https://docs.astral.sh/uv/)** (`brew install uv`), which installs the eight packages the
  server and every command run on, FastAPI, uvicorn, the Anthropic SDK and its httpx2, IMAPClient, icalendar,
  recurring-ical-events and sqlcipher3, from `pyproject.toml` on first run. Every command is
  `uv run python -m mailmind ...`.
- **[Rust](https://rustup.rs)** (`rustup`), which builds the leaves the app shares with the Rust core
  (`core/crates/mm-py`) when uv first installs the packages, and again when their Rust changes.
  `core/rust-toolchain.toml` names the version, which rustup fetches by itself.
- **[Node](https://nodejs.org)**, which builds the app and runs the frontend checks. `just run`
  builds it first.
- **A Claude API key**, for summaries and the other AI features, kept in the Keychain with
  `uv run python -m mailmind ai key`, or in `ANTHROPIC_API_KEY`. Without one, and with no other provider's key named in
  `mailmind.config.json`, AI mode starts off. Set `MAILMIND_MODEL` to pick a cheaper/faster Claude model.
- **Optional:** [`just`](https://just.systems), which wraps the commands below. Without it, build
  the app with `cd web && npm ci && npx vite build`, then run `uv run python -m mailmind serve`.
- **Optional:** the [`op`](https://developer.1password.com/docs/cli/) (1Password)
  CLI — only if you want to keep passwords in 1Password instead of the Keychain.
- **Optional:** [Proton Mail Bridge](https://proton.me/mail/bridge) — only if you
  add a Proton account (it must be installed and running).

## Quickstart

```bash
git clone https://github.com/<you>/mailmind.git && cd mailmind

# To look around first: the whole app on fictional mail, fake servers and a temp data directory.
# Nothing of yours is read or changed, it finds no model server on this Mac, and Ctrl-C ends it.
# (Needs uv, just and Node.)
just demo                         # http://127.0.0.1:8721   (custom port: just demo 9000)

# 1. Configure ONE account to start (delete the others in the example), and keep its password in the Keychain
cp mailmind.config.example.json mailmind.config.json
$EDITOR mailmind.config.json
uv run python -m mailmind accounts password you@example.com   # asks for it; or add the account in Settings
uv run python -m mailmind ai key                                # the Claude API key, the same way

# 2. Verify the credentials resolve (prints ok/fail per account and for the API key, no secrets)
uv run python -m mailmind check

# 3. Pull recent mail, then summarize it
uv run python -m mailmind sync all 90   # last 90 days (use a big number like 3650 for all-time)
uv run python -m mailmind assess 2000 3  # summarize up to 2000 of the last 30 days, 3 workers

# 4. Run the dashboard
just run                          # builds the app, then http://127.0.0.1:8721   (custom port: just run 9000)
just run-rust                     # the same on the Rust server, which is taking over from the Python one
```

## Configuring accounts

You can manage connections two ways: the dashboard's **⚙ Settings** panel
(add / edit / remove accounts, set a friendly **alias** per mailbox, and test a
connection — a password you type goes to your login Keychain, and is never sent back to the browser), or by editing
`mailmind.config.json` directly as below.

Each account has a `type` and a `credential`, which says where its password is. The file never holds a password
itself: one that does is refused, and MailMind won't start until it is in the Keychain.

```jsonc
{
  "accounts": [
    // In your login Keychain: `uv run python -m mailmind accounts password you@gmail.com` puts it there and writes this.
    { "email": "you@gmail.com", "type": "gmail", "credential": { "keychain": "account:you@gmail.com", "version": 1 } },

    // Generic hosted IMAP (set host/port; port 143 + "starttls": true for STARTTLS), its password in 1Password.
    { "email": "secure@company.com", "type": "imap", "host": "mail.yourhost.com", "port": 993, "trash": "Trash",
      "credential": { "op": "op://Private/Your Mail Item/password" } },

    // Proton — via the local Bridge.
    { "email": "you@proton.me", "type": "proton", "credential": { "proton_bridge": true } }
  ]
}
```

Credential styles: `{"keychain": "account:you@x"}` (the login Keychain, which Settings and `accounts password` write)
· `{"op": "op://Vault/Item/Field"}` (1Password) · `{"env": "VAR_NAME"}` (environment variable) ·
`{"proton_bridge": true}`. `uv run python -m mailmind accounts materialize` moves what an `op` or `env` credential
names into the Keychain.

Top-level keys beside `"accounts"` are for the AI. `"anthropic_api_key"` says where the Claude API key is
(`ai key` keeps it in the Keychain as `anthropic-api-key`), and `"openai_api_key"`, `"gemini_api_key"`,
`"local_api_key"` (for a model server on this Mac that asks for one) and `"compatible_api_key"` where another
provider's is (`ai key --provider openai`, and so on), each in any of those styles but `{"proton_bridge": true}`,
which there means no key: Claude's then doesn't turn to `ANTHROPIC_API_KEY` either. `"ai_daily_limit_usd"` is the
most MailMind spends on it in a day, in US dollars: `15` unless you set another positive number.

An account may say `"pool": true` to keep a connection open between reads on the Python app (the Rust server and the
Mac app keep every account's), and `"warm_folders": true` to sync its Drafts, Archive and Sent beside the Inbox. A
set `MAILMIND_POOL` or `MAILMIND_WARM_FOLDERS` decides over either.

An account may also say `"imap_adapter": "imapclient"`. `uv run python -m mailmind serve` then reaches
it through the IMAPClient library instead of Python's own imaplib: the new IMAP code, tried on
real mail before it becomes the default. The command line stays on imaplib, removing the key goes
back, and the startup line `[imap] …` says how many accounts asked.

**Per-type notes**
- **Gmail / Workspace** — you need an **App Password**, *not* your Google password: turn on
  2-Step Verification for your Google Account, create an App Password at
  [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), and paste it
  as Google shows it, four groups of four letters: MailMind keeps it in your login Keychain
  without the spaces. Settings (the page's and the Mac app's) has a button to that page and
  the steps beside the password. It picks Gmail for a `gmail.com` or `googlemail.com` address,
  and offers it for one whose domain's mail Google takes (its MX records name Google's
  servers: as you type the address, MailMind asks this computer's nameservers for the
  domain's MX records, and for nothing else). Saving a connection tests it at once, and a refusal says what to do: an App
  Password where a Google password was typed, a sign-in Google wants on the web first, or
  IMAP switched off. No App Passwords offered at all means Advanced Protection, or a
  Workspace admin, has switched them off. Defaults to `imap.gmail.com:993`, trash
  `[Gmail]/Trash`.
- **Generic IMAP** — set `host`/`port`. Default is `:993` direct TLS; use
  `"port": 143, "starttls": true` for STARTTLS. Either way the server's certificate
  is verified, so a self-signed one won't connect. An outgoing (SMTP) server left out is the IMAP host, on
  `smtp_port` 587 with STARTTLS unless `smtp_starttls` is false. Trash is the folder named `trash` when the server lists
  it, else the one the server marks as Trash, else one named like one (`INBOX.Trash`, "Deleted Messages"). Run
  `uv run python -m mailmind inventory you@company.com` to see the real names.
- **iCloud, Yahoo, AOL and Fastmail** — Settings fills in their servers from the address, and Trash for iCloud, and
  offers Fastmail's for a Fastmail custom domain, by the mail servers the domain names. Each takes an app password, not
  the account's own: Settings links the provider's page for one beside the password. An iCloud+ custom domain isn't
  supported yet: iCloud lets other mail apps sign in only with the @icloud.com address, so Settings says to add that
  address instead, where the custom address's mail arrives too. Yahoo Japan (yahoo.co.jp) is another company's, with
  servers of its own: type them in as Other IMAP.
- **Microsoft** (Outlook, Hotmail, Live, Microsoft 365) — not yet: these accounts need Microsoft's own sign-in
  ([#47](https://github.com/lanterno/mailmind/issues/47)), and Settings says so before any password is asked.
- **Proton** — install and run Proton Mail Bridge, then supply the Bridge
  password via `MAILMIND_PROTON_BRIDGE_PASS`, a 1Password item
  (`MAILMIND_PROTON_OP_ITEM`), the login Keychain (`proton-bridge-password`, which the
  Mac app's Settings keep through `POST /api/accounts/bridge-password`), or
  `~/.config/mailmind/proton_bridge_pass` (chmod 600), first found first; the Rust
  server reads no file. Only a server on this machine (`127.0.0.1`, `localhost`, `::1`) skips
  certificate verification, because Bridge's certificate is self-signed. Instead MailMind
  pins the certificate it meets first, in the account's `tls_pins`, and from then on sends no
  password to a server that shows another. If you reinstall or reset Bridge, Settings says the
  certificate changed; **Trust it** pins the new one.

## Two ways to use it

**Web dashboard / daemon** — `uv run python -m mailmind serve [port]` (`just run`). Auto-syncs
every 5 minutes and at once when IDLE hears new mail, and triages what arrived on a schedule of
its own, so a slow model never holds up a sync. With `MAILMIND_PROFILE=read-only` it serves
read-only: it writes nothing to a mail server, so it refuses a send, a move or a flag change, and every other change a
mail server would see, rather than queue it for a full run, and it syncs nothing in the background. `just dev` does that.

The app, at `/`, exists once built (`just build-web`, which needs Node and which `just run` does
first). With nothing built, the server says so.

**CLI curation** (`uv run python -m mailmind`, or `just`) — works across every account:

```bash
uv run python -m mailmind status              # INBOX + Trash counts
uv run python -m mailmind inventory all       # every folder + message/unseen counts
uv run python -m mailmind plan all            # classify the live inbox, show what would be trashed
uv run python -m mailmind sweep you@x.com --dry   # preview a junk sweep (ALWAYS dry-run first)
uv run python -m mailmind sweep you@x.com         # move junk → Trash (verified)
uv run python -m mailmind rules keep example.com  # learned rule: always keep this sender/domain
uv run python -m mailmind rules block spammer.com # always trash (writes a rule)
uv run python -m mailmind rules                  # show the learned lists
uv run python -m mailmind rules expire github.com 72  # trash this domain's INBOX mail after 72h (0 clears)
uv run python -m mailmind expire all --dry       # preview what the expiry rules would trash now
uv run python -m mailmind accounts password you@x.com  # keep an account's password in the login Keychain
uv run python -m mailmind ai key                  # keep the Claude API key there (--provider openai|gemini|local|compatible for another's)
uv run python -m mailmind ai off                  # turn AI mode off: nothing goes to any AI provider (`ai on` turns it back on)
uv run python -m mailmind assess --batch         # the last 30 days' with no summary yet, as a batch at half price, within today's limit; again for the answers
```

> ⚠️ **`sweep`, `sweep-stored`, `expire` and `empty-trash` mutate real mailboxes** (move to Trash / purge).
> Always `--dry` first. Every sweep and reconcile is recorded in the audit trail, in the database.
> Moves go to Trash and are recoverable; Gmail purges
> its Trash after 30 days.

## Environment variables

| Variable | Effect |
|---|---|
| `MAILMIND_HOME` | The data directory: everything under [Where your data lives](#where-your-data-lives-all-local-all-git-ignored). Unset, the repo folder, where it has always lived. It must already exist; MailMind never creates it. The server and every `uv run python -m mailmind` command read it. |
| `MAILMIND_PROFILE` | `full` (the default) or `read-only`. Read-only writes nothing to a mail server: every change one would see (a send, a move, a flag change, a snooze or mute, an RSVP, a cleanup, a sweep, a folder made, renamed or deleted, a draft's discard from the server, and an Undo that would ask a server) is refused before anything is queued. A draft saved read-only is kept on this Mac and reaches its server only once it is saved again in a full run, which never pushes it by itself. No sync or IDLE runs in the background, though a folder you open for the first time is still read to its oldest message (on the Rust server and the Mac app, a first scan's older mail goes on being read in the background), and snoozed mail still wakes, on this Mac alone. Any other name stops the server from starting. |
| `MAILMIND_WORKERS` | On the Rust server and the Mac app, each account has its own lane to its mail server, so one that hangs (Bridge stuck, a host that stops answering) holds up no other; `one` makes every account share one lane. The Python app shares one unless this is `account`. |
| `MAILMIND_NOSYNC=1` | The older switch for `MAILMIND_PROFILE=read-only`, which it wins over. |
| `ANTHROPIC_API_KEY` | Call the Claude API for every AI feature while Claude is the provider, billed to that key's organization. A key `"anthropic_api_key"` in `mailmind.config.json` names (`ai key` keeps one in the Keychain) wins over it. AI mode starts on where it is set or `mailmind.config.json` names a key for any provider (`"anthropic_api_key"`, `"openai_api_key"` and the rest), and off otherwise. |
| `MAILMIND_LOG_LEVEL=DEBUG` | Show debug lines and each error's text and traceback on the terminal, for chasing a fault. They can hold addresses and subjects. `mailmind.log` never does. |
| `MAILMIND_MODEL` | Override the Claude model (default `claude-opus-5-5`), e.g. `claude-haiku-5-5`, over the one chosen in Settings. Ignored while another AI provider is chosen. |
| `ANTHROPIC_BASE_URL` | Send API calls somewhere other than `https://api.anthropic.com` (a proxy). It is held to the rules a URL chosen in Settings is: https, or plain http only to 127.0.0.1, ::1 or localhost; a port from 1 to 65535 where it names one, never an empty one (`https://proxy.example.test:/`); no user, password, query or fragment; and no IPvFuture or zone in brackets (`https://[fe80::1%25en0]/`). One that breaks them sends nothing: Claude is unreachable. |
| `MAILMIND_POOL=1` | On the Python app, reuse persistent per-account IMAP connections instead of connecting per op — **much** faster repeated reads. Off by default; set to `1` for all accounts, or a comma-separated list of emails. Proton Bridge is excluded from the `1` catch-all and must be listed explicitly. The Rust server and the Mac app keep every account's connection and don't read it. |
| `MAILMIND_WARM_FOLDERS=1` | Also sync each account's Drafts / Archive / Sent alongside INBOX, so search and threading cover more than the inbox. Off by default; same `1`-or-list form. |
| `MAILMIND_RECONCILE_SECS` | Target seconds for one full ghost-prune cycle across all accounts (default `1800`; `0` disables). |
| `MAILMIND_EXPIRE_SECS` | Seconds between expiry-rule passes (default `3600`; `0` disables). Never runs read-only. |
| `MAILMIND_NO_DRAFT_TICK=1` | Disable the background reply pre-drafter (also implied by read-only). |
| `MAILMIND_NO_BATCHES=1` | Triage a backlog message by message at full price instead of sending it as a batch at half price. A batch already out is still brought in. |
| `MAILMIND_NO_SCHEMA=1` | Ask the model for free-form JSON instead of using structured-output schemas — a fallback for models without structured output. |
| `MAILMIND_PROTON_BRIDGE_PASS` | Proton Bridge password (highest precedence) |
| `MAILMIND_PROTON_OP_ITEM` | 1Password item id holding the Bridge password |
| `MAILMIND_DB_KEY` | The key the database is encrypted with, as 64 hex digits, in place of the one in the Keychain. For the tests, the demo and a computer with no Keychain; set, the Keychain is never asked. Anyone who has it can read your database, so keep it out of shell profiles and history. |

A switch (`MAILMIND_NOSYNC`, the `MAILMIND_NO_*` ones, and `MAILMIND_POOL` and `MAILMIND_WARM_FOLDERS` when
they list no addresses) is on for `1`, `true`, `yes` or `on` and off for `0`, `false`, `no`, `off` or nothing,
in any case. A value a variable doesn't take is named in the log, the variable and never the value, and reads
as the side that writes, sends or spends less: `MAILMIND_NOSYNC` turns read-only on, `MAILMIND_NO_DRAFT_TICK`
turns drafting ahead off, an address list's entry that is no address names no account, and the rest read as
if unset, but for `MAILMIND_PROFILE` (above). `mailmind/platform/environment.py` declares each variable.

The server's port is the first argument to `serve` (default `8721`).

`MAILMIND_POOL` and `MAILMIND_WARM_FOLDERS` are both **off by default**, so a plain
run of the Python app connects fresh for every operation and syncs INBOX only. `just run-fast` sets
both for that run. To keep them on, give an account `"pool": true` or `"warm_folders": true`
in `mailmind.config.json`; a variable that is set decides over the file.

## Where your data lives (all local, all git-ignored)

In the repo folder, or in the directory `MAILMIND_HOME` names. The Mac app keeps the same files in a folder of its
own, `~/Library/Application Support/io.github.lanterno.mailmind`:

- `mailmind.db` — messages, bodies, attachments, summaries, your learned keep/block lists, expiry rules and filters,
  saved views, snippets and the audit trails, encrypted with the key in your login Keychain. Keep a copy of that key
  somewhere safe, such as 1Password: without it neither the database nor its backups can be read on another computer.
  `security find-generic-password -s mailmind -a database-key -w` prints it.
- `mailmind.log` (and `.1` to `.3`, older) — what the app said of its own running, one JSON object a
  line, readable by you only. It holds ids, counts and the kinds of error, never an address or a subject. The Mac app
  and the Rust server write it too, and when something in them breaks it says which part and which line of the code,
  never what it was working on.
- `backups/` — the database as it stood before each upgrade (the newest three, encrypted and readable by you only).
- `mailmind.config.json` — your accounts, your preferences, and where each password is (never a password itself),
  readable by you only.

The login Keychain's items under the service `mailmind` hold the rest: `database-key`, each account's password
(`account:<address>`), each AI provider's API key (`anthropic-api-key`, `openai-api-key`, `gemini-api-key`,
`local-api-key` and `compatible-api-key`) and the Proton Bridge's password (`proton-bridge-password`).

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
SMTP, Claude and OpenAI-compatible servers and a made-up mailbox, and compares the answers with recorded ones in
`tests/golden/`. `just verify` adds lint (ruff, the import rules between the package's layers, and pyright), byte-compile, the
frontend checks (the page's API types and limits against the server's, which `just gen-api` writes from the routes' OpenAPI
document and the server's limits, then the TypeScript's types, lint and tests, which need Node) and a look for known
vulnerabilities in what the lockfiles pin.
`just test-full` also runs the browser tests (`just test-browser` runs only those), which click
through the app in Chromium, and `just test-dovecot` holds MailMind's IMAP code to the same promises against a
real Dovecot server in Docker. `just setup` installs all of it, and `just` on its own lists every recipe. uv installs those dev
tools from `pyproject.toml` on first use, as it does the app's own packages.

## Privacy & contributing

This is a personal-data app. **Never commit** `mailmind.config.json`, `mailmind.db`, its `backups/`, the log, or
screenshots of real mail — the `.gitignore` covers them; don't `git add -f` around it. PRs welcome. A package the server needs is declared in
`pyproject.toml`, and one the app needs in `web/package.json`.

## License

MIT — see [LICENSE](LICENSE).
