# MailMind — Roadmap

Only outstanding work is listed, re-verified against the tree on 2026-11-07: shipped behaviour is
described in the README and in the code itself, not here. The app is the React one in `web/src`, at `/`,
served by `python -m mailmind serve`. Each item says what is still missing there or on the server.

Items cite functions and symbols rather than line numbers, which drift. Value and effort are
relative to this codebase — S = an afternoon, M = a few days, L = a week-plus.

## AI that acts, not just describes

- **Events and tasks extracted from prose** · high value · effort L — Nothing turns message text
  into a calendar entry or a dated task. Two distinct halves, and the seam matters:
  - *The .ics half is cheap.* `calendar.domain.rsvp` already has `_fold`, `_ics_esc`, `_ics_dt` and emits
    `BEGIN:VEVENT` inside `build_reply` — what is missing is a `METHOD:PUBLISH` emitter for a **new**
    event, and a date parser over prose. The only "Add to calendar" affordance today, on `Invite` in
    `Reader.tsx`, just downloads an .ics that already arrived as an attachment.
  - *The durable-task half needs a new table*, and per the original design constraint it must key on
    `(account, message_id/thread_id)` and **never** on `pk`, which is not stable across re-ingest.
  The AI `action` string in `Commitments` (`features/assistant/Panels.tsx`) is read-only and parses no dates.
- **AI pre-fills the Screener's verdict** · medium value · effort M — Rescope of the old
  "auto-triage the ambiguous residual": `ScreenerPane` already is a learning Imbox that writes
  `mailmind_rules.json` one sender at a time, so do not rebuild it. What is missing is a
  confidence-filtered claude pass suggesting Allow / Screen-out — today `is_junk` is set purely
  deterministically at ingest and no code path appends an AI verdict to the rules file. While in
  there, rename `python -m mailmind sweep-ai`: `Sweeps.sweep_ai` reads the classifier's `is_junk`, and
  only the command's name still promises an AI verdict.
- **Scheduled morning briefing + summarize-this-sender** · medium value · effort M — Cross-message
  synthesis ships in three flavours, but the every-minute job (`Clocked.round`) only calls
  `Snoozing.wake_due()` and `FollowUps.tick()`, so `briefs` is written only once the app asks (`useBrief`,
  as the Inbox opens), and a desktop notification goes only for new mail (`tell` in `NewMail.tsx`) and a meeting about to
  start (`SoonPill`). Second half: a claude pass over one sender's recent threads, hung off `ContactCard` —
  `Contacts.card` is pure SQL relationship stats with no AI call.
- **Weekly Review v2** · effort M overall — three follow-ons of different shapes; treat separately:
  - **Answer "you handled N this week"** · medium value · effort S — unanswerable today because no
    `archived_at` column exists. Add it as a new migration in `mailmind/platform/db/migrations`,
    stamp it as `MailCommands` settles an archive, and **clear it in `Reversals`** — otherwise the
    column lies after every undo. `mail_commands` already keeps each applied archive for a month,
    and would do instead if an Undo marked its row, which today it leaves as it was.
  - **Meetings and deadlines in the retrospective** · medium value · effort M — feed `Invites.of()`'s
    parsed VEVENTs into `Reviews` (`mailmind/assistant/application/review.py`), which has zero calendar/ics references today.
  - **Scheduled delivery of the review** · needs a decision first, not a sub-bullet — it would be the
    first time the server sends mail with no human in the loop.
- **Tidy that runs itself, with review** · medium value · effort M — `fileTidy` in `BriefPane` is reachable only
  from the brief's button and always goes through `askConfirm`. The intended v2 — run daily or on brief load, gated by
  an opt-in preference, with a "we filed N — review / undo" surface — needs a persisted setting, a scheduled trigger and that
  review strip.

## Search, rules and organization

- **PDF preview in the Document Hub** · low value · effort M — The Hub previews images in a frame whose sandbox allows nothing
  (`perimeter.attachment_preview_headers`). Chromium won't show a PDF in a sandboxed frame, and a same-origin one would break the
  security model, so a PDF needs a renderer of its own (pdf.js, say), as a new dependency, running with no origin.
- **Batch cleanup with a stated rationale** · medium value · effort M — Cleaning ten noisy senders
  means ten preview-and-confirm cycles: rows in `Flood` (`features/curation/Curate.tsx`) are strictly one-at-a-time
  through `previewCleanup`, with no marked set. Wanted: multi-select executing one batched block+sweep
  under a single undo group, and a per-sender AI rationale ("you have never opened one of these 47 in
  6 months") — `Cleanup.senders` is pure SQL `noise_score` today, with no claude call and no
  rationale field.
- **Gmail multi-label views** · low value · effort M — Carried over from the original phase plan and
  never built. `X-GM-LABELS` is used for **writes** only (archive drops `\Inbox`; the relabel path
  adds one), but labels are never FETCHed and there is no `labels` / `message_labels` junction, so a
  Gmail message cannot be seen under all the labels it actually carries.
## Speed and the connection layer

- **Make a gate for each account the default** · high value · effort S — `MAILMIND_WORKERS=account`
  gives each account its own gate (`mailmind.platform.workers.gates`), so a slow Proton fetch no
  longer blocks archive, send or open on another account; unset, one lock is still every
  account's gate. The whole suite passes under either. The owner soaks it on real accounts, then it
  becomes the default and the flag goes. Then
  give each account's worker the connection it owns (the pool dissolved into it), and split the
  jobs that still pass `gates.EVERY_ACCOUNT`, which wait for every gate: a draft save whose old
  copy is in another account (it needs just the two accounts' gates, which `gates.run` can't take
  yet), an undo with no account, and a message whose account `server_ops.account_of` can't find.
- **Soak IMAPClient on real accounts, then make it the default** · high value · effort S — Waits on
  the owner. `python -m mailmind serve` reaches an account through IMAPClient when its config asks (`"imap_adapter":
  "imapclient"`; the startup line counts them). Both adapters pass the contract against the fake
  and against Dovecot (`just test-dovecot`), and the whole suite passes under either. On real
  Gmail, IMAP and Proton Bridge accounts the two answered every read alike (capabilities, folders,
  status, searches, headers, flags, bodies, IDLE), but nothing has written through IMAPClient
  there yet, nor run on it for days. The owner asks for it on one account, then on all, and uses
  it daily for a week, watching Settings' sync health and the server's output;
  removing the key goes back. Then `front.build` calls `mailbox.use("imapclient")` and the ask
  goes.
- **Turn the connection pool on by default** · high value · effort M — Waits on the owner's soak.
  `Pool` is complete, safety-reviewed and wired into four read call sites (`Ingest.fetch_body`,
  `Folders._live_folder_list` backing the folder rail, `Sweeps.plan` ahead of a sweep, and reconcile's read of UIDs), but
  `_targets()` reads `MAILMIND_POOL`, then each account's `"pool"` key, both unset by default, so every read falls through to a
  fresh session and a logout. Its docstring has a cold Proton body fetch at ~37s, against ~2s warm.
  Remaining: run it on real accounts for days; validate against Proton Bridge (`ProviderProfile`
  recycles a Bridge connection sooner, and `Pool._enabled` leaves Bridge out of the `MAILMIND_POOL=1`
  catch-all, so it has to be listed by address); extend borrowing to mutations (only reads borrow
  today); and default it on.
## The API, now that one client and one door read it

- **Move the command line off `urllib_http.py`** · low value · effort S — `python3 -m mailmind` runs on Python alone, so
  `assess` and the rest reach Claude through `UrllibModel` (`TRANSPORTS` in `bootstrap/assistant.py`; only `front.build` calls
  `assistant.use("sdk")`), and `tests/contract/test_ai_contract.py` holds the two to one behaviour over `adapters.wire`.
  Make the SDK the one transport, and every command a `uv run python -m mailmind ...`, and `UrllibModel`, the doubled contract
  runs and that part of `wire` go. The same step lets the command line take IMAPClient once its soak ends, and imaplib's
  adapter go after it.

## The reading room and the visual system

- **Sender identity inline, with a From/Reply-To spoof hint** · medium value · effort M — `ContactCard` delivers most
  of the data (monogram, "N emails · replied N× · first X ago"), but it is a
  click-through popover from `SenderButton`; the key facts should appear inline in `.rd-from`. And there is no spoof
  warning at all: `replySeed` reads `reply_to` to pick the recipient but nothing ever compares it
  to `from_addr`.
## Setup, first run and housekeeping

- **60-second guided first run** · medium value · effort M — `.firstrun`, in `App`'s `empty`, is a static empty state
  shown only when no accounts are connected; there are no coach-marks and no flag
  gating a walkthrough. The surfaces worth explaining have multiplied since this was first proposed
  — today's brief, the folder rail and focus reading, ⌘K, `?ask` / `!do` prefixes, the
  Screener — so it is worth more now, not less. Gate on one flag.
- **Consistency sweep: buttons, empty-state strings, toast vs. persistent** · low value · effort M —
  Three concrete, checkable changes rather than a vibe: (1) one `.btn-retry` replacing `.ai-rerun`,
  `.frail-retry`, `.ob-retry` and `.do-confirm`; (2) one string table replacing the
  inlined phrasings (`App`'s `empty`: "No matches." / "Nothing left here."; `Reader`'s "No message selected."); (3) a stated
  rule for toast-vs-persistent — **persistent iff the event happened while
  the user was not looking** — then apply it, since a snooze waking where no list shows it (`snooze-wake` in `app/realtime.ts`)
  only toasts.

## The desktop app

- **Proton Mail goes with the desktop app** — decided on 2026-11-08. Proton offers IMAP only through its Bridge, which has
  to run on the same computer as the app, so only a desktop app can count on it. The `proton` account type, the
  `proton_bridge` credential and the loopback handling stay as they are until that app exists; add no new Bridge special cases.

## Open decisions

These are carried forward from the original phase plan because they need a call, not an
implementation.

- **At-rest encryption for `mailmind.db`** — the DB now holds full bodies and attachments, which
  materially raises its value. Current posture is gitignored, with no encryption anywhere in the
  tree; the database, config, rules, views, log and backups are owner-only. SQLCipher was postponed on 2026-11-03, and
  `python3 -m mailmind`, which runs on Python alone, would have to install it too. The question is whether that posture stays acceptable
  for a single-user localhost machine or becomes opt-in encryption.
- **How aggressively to prefetch folders** — on-demand sync bounds disk and first-sync cost, but
  unified-inbox-across-accounts and global search both want broader coverage.
- **Where the data directory lives by default** — `MAILMIND_HOME` names one, and unset it is the repo
  folder, where it has always been. Left for later on 2026-11-03.
- **Default remote-image policy** — block-by-default with per-message "Load images" (current, and the
  privacy-preserving choice) vs. allowing images for known/personal senders.

## Next up

1. **Soak the three defaults, one at a time** (high) — `MAILMIND_WORKERS=account`, then IMAPClient, then `MAILMIND_POOL`. Each
   is written and tested, and waits on days of real use before it becomes the default.
