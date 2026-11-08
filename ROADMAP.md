# MailMind — Roadmap

Only outstanding work is listed, re-verified against the tree on 2026-11-07: shipped behaviour is
described in the README and in the code itself, not here. The app is the React one in `web/src`, at `/`,
served by `python -m mailmind serve`. Each item says what is still missing there or on the server.

Items cite functions and symbols rather than line numbers, which drift. Value and effort are
relative to this codebase — S = an afternoon, M = a few days, L = a week-plus.

## Reply, compose and send

- **Send-time checks, second pass** · medium value · effort S — `guardCheck` in
  `features/compose/sendGuard.ts`, which `runChecks` calls and `Guard` in `Composer.tsx` shows, covers
  subject, attachment wording and reply-all size, and chips carry a "new" mark from `KnownContacts`.
  Still missing:
  - a near-miss line for an address one edit away from someone you write to;
  - a line when a Reply-To sends the reply somewhere other than the sender. `replySeed` takes
    `reply_to` for the To and says nothing, so a stranger there gets only the "new" mark on its chip;
  - plus-addresses and unconfigured aliases (`you+tag@`) counted as yours rather than new, which
    `myAddresses` and `isNewToYou` match only exactly;
  - the forward count. `forwardSeed`'s `fwdAttach` counts named inline parts without a
    Content-ID, and Outlook's synthetic `invite.ics`, but `attachments.extract` re-attaches
    neither, so "see attached" on such a forward stays quiet when nothing goes out attached.
- **Markdown body → clean HTML + text multipart** · high value · effort L — New messages still go
  out text/plain only: `builder.build_message` adds an alternative only when `spec["html"]` is set,
  and `mailmind.compose.domain.composition` sets it only for replies and forwards whose original had HTML. Needs a
  markdown renderer run back through `content.sanitize` (already battle-tested, so
  the safety half is free) plus a Write/Preview toggle over `#cmpBody` in `ComposerBox`. Blocker for
  inline images.
- **Inline `cid:` images in outgoing mail** · medium value · effort M — `cid:` handling is inbound
  only (`Bodies._resolve_cids` and `_CID_IMG` rewrite to data: URIs when rendering received mail). Outbound,
  `build_message` only calls `add_attachment` — no `Content-ID`, no `multipart/related`, and the
  composer's `addFiles` marks nothing inline. Correctly blocked on the markdown work: there is no HTML
  body for a cid to reference.
- **Templates / snippets with `;trigger` expansion** · medium value · effort M — Completely absent:
  no storage file, no route, no compose-time expansion. The pattern to copy exists twice —
  `discovery.adapters.views_file` and `curation.adapters.rules_file` both do atomic JSON-file persistence with a GET/POST pair and a UI — so
  this is a snippets store plus an expander in `features/compose/machine.ts`, which stays free of the
  DOM, and `{{placeholder}}` tab-through in `#cmpBody`.
- **Smart-paste link cleaning** · low value · effort S — No `paste` handler exists in
  `web/src` and no tracking-parameter stripper exists in any module. A client-side handler on
  `#cmpBody` that drops `utm_*`/`fbclid` and unwraps redirect wrappers before insertion. Independent of the
  markdown work.
- **Configurable Undo-Send delay** · low value · effort S — The feature ships; only "configurable"
  is unmet. `outbox_item.SEND_DELAY = 10` has no config key and no env override, and `UNDO_SECONDS`
  in `machine.ts` is a 10 of its own. Keep 10s as the default and expose it in the Settings home below.

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
- **Today & next's remaining blind spots** · low value · effort S — A zone only the invite's own VTIMEZONE
  explains (Outlook's `W. Europe Standard Time`, a vendor's prefix) floats, so such an invite shows
  the hour it says, whatever zone you're in; `invite.zone` is the one place to teach them. A later SEQUENCE is not
  weighed, only which message is newest, and RANGE=THISANDFUTURE moves one occurrence. A rule finer than a
  day shows only its first date. Ingest keeps an invite's body only for the
  Inbox (`Ingest._process` with `with_body`), so an invite filed elsewhere by a server rule is
  listed once opened. Bodies stored before `mime.parse` recorded inline-only calendar parts
  keep attachment rows without one; a one-off re-parse of stored bodies containing
  `text/calendar` would list them.
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

- **Search-as-you-type suggestions and clickable scoping** · high value · effort M — Roughly a
  quarter done: the contact card gives a working "more from this sender". Missing is any suggestion
  endpoint (no route answers `/api/suggest`), a debounced popover under `#search` in `TopBar` completing
  senders/domains from the same source `/api/contacts` indexes plus operator names from `QuerySpec`'s
  fixed grammar, and a "this conversation only" scope beside `ScopeToggle`, which offers all accounts or
  this one. `Entries` in `Palette.tsx` and `ChipField` in `Composer.tsx` are the working precedents for the dropdown.
- **Incoming rules engine, and create-filter-from-search** · high value · effort L — `curation.domain.rulebook` is a
  one-dimensional verdict list, not an engine: `KEYS = (keep_senders, block_senders, keep_domains,
  block_domains)` and `add(rules, token, verdict)`. There is no condition language, no action verb, no
  rules table, and no ingest-time evaluation beyond `classifier.is_junk`. Where v1 hooks in:
  **evaluate in `Ingest._process` immediately after `isj = 1 if self._curation.is_junk(b) else 0`, and express
  conditions by reusing `QuerySpec`'s existing operator grammar** rather than inventing a
  second one — that keeps "create filter from search" a serialization step instead of a translation
  layer. Ship the local-action subset (auto-star / auto-read / auto-label) well before any IMAP
  MOVE, and re-scope first: "+ Save view", the Screener and `/api/cleanup/execute` already cover the
  "this sender floods me" case filters usually serve.
- **Document Hub v2** · high value · effort M — Three follow-ons in value order: an inline PDF/image
  preview so you needn't download to check (a row's `doc-body` in `Hub` only opens the source email; no preview modal
  exists), group-by-sender/type sections in place of the flat row list, and a persistent rail lane so
  the Hub is a place rather than a ⌘K command.
- **Prose search without the `!` prefix** · medium value · effort S — The hard part is built and
  proven: `BulkCommands.plan` calls the model with `DO_PLAN_SCHEMA`, re-parses the returned filter through
  `QuerySpec.parse` (the AI never touches SQL), and `runDo` drops the translated operators into the
  real, editable search box. Missing is the search-shaped front door — bare prose with no prefix
  still goes to `/api/search` as literal FTS — plus a labelled, dismissible "translated" chip so you
  can get the literal search back. `TopBar`'s `onKeyDown` is where the prefixes are read.
- **Cross-account virtual folders** · medium value · effort M — `Queries.snoozed_in`, `muted_in`,
  `waiting_in` and `replylater_in` each read one account, and `Folders.of_account(account)` counts
  per account, so there is no all-accounts Snoozed to match all inboxes: `virtual` in `FolderRail`
  comes from `useFolders(account)`, and on All inboxes there is no account to ask. Saved lanes already work
  across accounts because they ride `/api/search`'s `scope` param, and `Queries.all_inboxes` and
  `waiting_on_others` are one query over every account. The four helpers and what they read
  (`store.snoozed_rows`, `attention.book`'s `list_muted`, `list_waiting` and `list_replylater`) need
  the same branch.
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
- **Honest thread counts** · low value · effort S — The list badge (`thr-count` in `RowItem`) is `_conversation_rows`'s
  `COUNT(*) OVER (PARTITION BY m.thread_id)` within one folder (`folder_conversation_rows`), while
  `Queries.thread_json`, whose `count` `ConversationView` shows, selects across all folders — so a thread with a reply in Sent shows "2" and
  opens 3. Either count account-wide or relabel the tooltip from "N messages" to "N in this folder".

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
  `Pool` is complete, safety-reviewed and wired into three read call sites (`Ingest.fetch_body`,
  `Folders._live_folder_list` backing the folder rail, and `Sweeps.plan` ahead of a sweep), but
  `_targets()` reads `MAILMIND_POOL`, which is unset by default, so every read falls through to a
  fresh session and a logout. Its docstring has a cold Proton body fetch at ~37s, against ~2s warm.
  Remaining: run it on real accounts for days; validate against Proton Bridge (`ProviderProfile`
  recycles a Bridge connection sooner, and `Pool._enabled` leaves Bridge out of the `MAILMIND_POOL=1`
  catch-all, so it has to be listed by address); extend borrowing to mutations (only reads borrow
  today); and default it on.
- **Persist `MAILMIND_POOL` / `MAILMIND_WARM_FOLDERS`** · medium value · effort S — Both features
  are hard default-off. The README documents them and `just run-fast` sets both for one run, but
  nothing else does, so a plain `just run` silently reverts to
  fresh-connect-per-op and INBOX-only sync. Pick a persistence story (a key in
  `mailmind.config.json`, or derive the account list from it).
- **Pool the reconcile read** · low value · effort S — Half done: `Reconciler.reconcile_account`
  connects with `timeout=15`, capping the exposure, but it still opens its own session while holding
  the IMAP gate instead of using `Pool.borrow` the way the three read call sites do. `borrow` would
  have to take that timeout.

## The API, now that one client and one door read it

- **Move the command line off `urllib_http.py`** · low value · effort S — `python3 -m mailmind` runs on Python alone, so
  `assess` and the rest reach Claude through `UrllibModel` (`TRANSPORTS` in `bootstrap/assistant.py`; only `front.build` calls
  `assistant.use("sdk")`), and `tests/contract/test_ai_contract.py` holds the two to one behaviour over `adapters.wire`.
  Make the SDK the one transport, and every command a `uv run python -m mailmind ...`, and `UrllibModel`, the doubled contract
  runs and that part of `wire` go. The same step lets the command line take IMAPClient once its soak ends, and imaplib's
  adapter go after it.

## Keyboard, screen readers and reversibility

- **Finish the row hover cluster, and hover-to-prefetch** · medium value · effort S — `.racts` in `RowItem` offers
  Archive, Delete and Snooze; Star and Mark-read are still key- or toolbar-only. Separately, a debounced
  hover on a row calling the body route with `prefetch=1` would make the first open of a hovered row instant.
  Today `prefetchNext` in `useMailbox.ts` warms only the next two, and `Member` in `Reader.tsx` fetches a conversation's
  message only once it is opened, so opening a thread warms none of its members.
- **Live regions on the send float and undo float** · medium value · effort S — `toast()` and `announce()` reach
  `#srLive`; `SendFloatView` and `#undoFloat` carry no `aria-live` or `role="status"`, so send status and the Undo
  countdown are silent. Pair this with a contrast check of Dusk and Night: axe runs only in Day.
- **One toast-with-action primitive with a draining bar** · medium value · effort M — `toast()` (2600ms
  in `lib/toast.tsx`) and `offerUndo` (8000ms in `features/mailbox/actions.ts`) are two independent primitives with two
  lifetimes, and nothing makes the undo window legible. Collapse them and `SendFloatView`'s countdown into one
  with a CSS width-transition bar; `SendFloatView` already proves the pattern, but only for sends.
- **Emit the keys legend from the commands** · low value · effort S — `buildCommands` in `app/commands.ts` already
  drives the keys and the palette, but `Keys` in `features/status/Help.tsx` is hand-written `<kbd>` markup and has already
  drifted: it has no `l` (reply later) and no `g l`. Group the legend from the commands, as the palette does.

## The reading room and the visual system

- **Sender identity inline, with a From/Reply-To spoof hint** · medium value · effort M — `ContactCard` delivers most
  of the data (monogram, "N emails · replied N× · first X ago"), but it is a
  click-through popover from `SenderButton`; the key facts should appear inline in `.rd-from`. And there is no spoof
  warning at all: `replySeed` reads `reply_to` to pick the recipient but nothing ever compares it
  to `from_addr`.
- **Dark mail colors under the Day theme** · low value · effort S — `frameTheme` in `MailFrame.tsx` themes dark mail
  from the live tokens, which are the light ones under Day and Dusk, and `lookOf` in `mailDoc.ts` applies them. Choosing
  Dark under Aa there leaves plain text on light paper and inverts rich mail onto a light page. Dark needs its own night
  palette that doesn't follow the app theme.
- **Density modes (Comfortable / Cozy / Compact)** · low value · effort M — Zero matches for
  `density` or `data-density`; Mailbox row metrics are hardcoded in the `.row.cmsg` rules. Worth more
  now that rows carry a two-line summary and a next-step chip: a `--row-pad` / `--row-lh` pair under `body[data-density]`, with Compact clamping the gist to one
  line and hiding the chip, pairs naturally with the missing Preferences pane.
- **High-contrast toggle** · low value · effort S — Unbuilt: no `body.hc`, no preference, and `lib/theme.ts` knows only
  the three themes. Lower priority now that the base palette passes AA, but the light theme proved the pattern — one
  body-class override of the `:root` token block is the whole feature.
- **Reopen a draft that holds an attached email** · low value · effort S — An attached email is one
  part now (`mime.walk`, `mime.part_bytes`) and goes out as a parsed message (`build_message`), but
  `resume.resume_files` still refuses such a draft ("which MailMind can't reattach yet"). To lift it:
  walk with `mime.walk` rather than `msg.walk()`, so its part indexes match `parse()`'s, and read the
  file's bytes with `part_bytes`, since `decoded` gives none for such a part.
- **format=flowed is read as hard breaks** · low value · effort S — Nothing reads the `format` or
  `DelSp` parameters. A text-only flowed message shows in the `<pre>` of `Bodies.as_document` hard-wrapped at
  about 72 columns, with a stray space on each space-stuffed line, and DelSp=yes splits words.
  Search and summaries are barely touched. Another app's flowed draft reopens whole with its soft
  breaks fixed in, then goes out without `format=flowed`. The fix is an RFC 3676 decoder in
  `mime`'s `_decode` and `content.text.text_body`. Per line: count the quote depth, then drop one
  stuffing space, then join a line ending in a space to the next one at the same depth (deleting
  the space when `delsp=yes`). Never join the `-- ` signature line.

## Setup, first run and housekeeping

- **A real Settings home with a Preferences tab** · high value · effort M — `Connections` in `features/settings/Settings.tsx`
  is a single-purpose "Email connections" dialog; there is no tab strip, and `Directory` exposes only
  accounts/alias/signature/webmail. It has become the natural landing zone for several other gaps —
  density, high-contrast, AI model, the AI daily limit (file only today), the 10s undo-send window,
  the 45s and 20s intervals of `whileDown` in `app/realtime.ts`, notification preferences and quiet hours — and for the `mm_*`
  localStorage keys that currently hold every preference and die with a browser-data clear. Needs
  the tab strip, a preferences block in `mailmind.config.json`, and a read/write route.
- **Standard-format export from the UI** · medium value · effort S — `mailmind/platform/db/export.py` implements
  `export_eml()` and `export_mbox()` (the latter with proper mboxrd `From `-escaping), but the only
  download route is `/api/export` (`_snapshot` in `http/routers/export.py`), a `.db` snapshot, and
  Settings exposes exactly one control, `Backup`. No route references
  `export_eml`/`export_mbox`. Since `.db` is a MailMind-only format, the stated escape hatch does
  not actually let a user leave. Add a format picker beside `Backup` and a route
  that streams a zip.
- **60-second guided first run** · medium value · effort M — `.firstrun`, in `App`'s `empty`, is a static empty state
  shown only when no accounts are connected; there are no coach-marks and no flag
  gating a walkthrough. The surfaces worth explaining have multiplied since this was first proposed
  — today's brief, the folder rail and focus reading, ⌘K, `?ask` / `!do` prefixes, the
  Screener — so it is worth more now, not less. Gate on one flag.
- **First-sync feedback, with a live count** · medium value · effort S — A first scan says so
  ("Reading this folder for the first time…", from `last?.syncing` in `App`), but never how far it has got.
  `/api/digest`'s `stats` already holds how many messages are stored, and `Status.tsx` polls it while a sync
  runs, so "read N messages so far…" is a small change.
- **Consistency sweep: buttons, empty-state strings, toast vs. persistent** · low value · effort M —
  Three concrete, checkable changes rather than a vibe: (1) one `.btn-retry` replacing `.ai-rerun`,
  `.frail-retry`, `.ob-retry` and `.do-confirm`; (2) one string table replacing the
  inlined phrasings (`App`'s `empty`: "No matches." / "Nothing left here."; `Reader`'s "No message selected."); (3) a stated
  rule for toast-vs-persistent — **persistent iff the event happened while
  the user was not looking** — then apply it, since a snooze waking where no list shows it (`snooze-wake` in `app/realtime.ts`)
  only toasts.

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
