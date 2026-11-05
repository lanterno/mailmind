# MailMind — Roadmap

This file replaces `ROADMAP.md`, `FEATURES_ROADMAP.md`, `POLISH.md` and `UX_VISION.md`, all
four of which had drifted far enough from the code to mislead — `FEATURES_ROADMAP`'s closing
claim that every Theme 0–7 item had shipped was simply false, while several items it listed as
planned had in fact landed. Everything below was re-verified against the tree on 2026-07-29,
and **only outstanding work is listed**: shipped behaviour is described in the README and in
the code itself, not here.

Items cite functions and symbols rather than line numbers, which drift. Value and effort are
relative to this codebase — S = an afternoon, M = a few days, L = a week-plus.

## Reply, compose and send

- **Send-time checks, second pass** · medium value · effort S — the strip (`guardCheck` /
  `showSendGuard`) covers subject, attachment wording and reply-all size, and chips carry a
  "new" mark from `contacts_known`. Still missing:
  - a near-miss line for an address one edit away from someone you write to;
  - a line when a Reply-To sends the reply somewhere other than the sender;
  - plus-addresses and unconfigured aliases (`you+tag@`) counted as yours rather than new;
  - the forward count. `forwardMsg`'s `fwdAttach` counts named inline parts without a
    Content-ID, and Outlook's synthetic `invite.ics`, but `_extract_attachments` re-attaches
    neither, so "see attached" on such a forward stays quiet when nothing goes out attached.
- **Markdown body → clean HTML + text multipart** · high value · effort L — New messages still go
  out text/plain only: `compose.build_message` supports `add_alternative(spec["html"])`, but the
  caller sets `html = None` and fills it only for replies/forwards whose original had HTML. Needs
  a ~120-line stdlib markdown renderer run back through `sanitize_html` (already battle-tested, so
  the safety half is free) plus a Write/Preview toggle over `#cmpBody`. Blocker for inline images.
- **Inline `cid:` images in outgoing mail** · medium value · effort M — `cid:` handling is inbound
  only (`_CID_IMG` / `_resolve_cids` rewrite to data: URIs when rendering received mail). Outbound,
  `build_message` only calls `add_attachment` — no `Content-ID`, no `multipart/related`, and
  `addAttachments()` marks nothing inline. Correctly blocked on the markdown work: there is no HTML
  body for a cid to reference.
- **Templates / snippets with `;trigger` expansion** · medium value · effort M — Completely absent:
  no storage file, no route, no compose-time expansion. The pattern to copy exists twice —
  `views.py` and `rules.py` both do atomic JSON-file persistence with a GET/POST pair and a UI — so
  this is a snippets store plus a keyup expander and `{{placeholder}}` tab-through in `#cmpBody`.
- **Smart-paste link cleaning** · low value · effort S — No `paste` listener exists in
  dashboard.html and no tracking-parameter stripper exists in any module. A pure-client handler
  that drops `utm_*`/`fbclid` and unwraps redirect wrappers before insertion. Independent of the
  markdown work.
- **Configurable Undo-Send delay** · low value · effort S — The feature ships; only "configurable"
  is unmet. `SEND_DELAY = 10` is a bare module constant in `outbox.py` with no config key and no env
  override, though `enqueue` and the `scheduled` heuristic both already read it. Keep 10s as the
  default and expose a slider alongside the existing signature setting.
- **Three-way choice on Esc with a dirty draft** · low value · effort S — The litter bug is fixed
  (prefilled recipients no longer dirty the compose, signature-only bodies are never saved), but
  `composeClose` still silently auto-saves and toasts. The specified Keep editing / Save draft /
  Discard prompt was never built.

## AI that acts, not just describes

- **Events and tasks extracted from prose** · high value · effort L — Nothing turns message text
  into a calendar entry or a dated task. Two distinct halves, and the seam matters:
  - *The .ics half is cheap.* `ics.py` already has `_fold`, `_ics_esc`, `_ics_dt` and emits
    `BEGIN:VEVENT` inside `build_reply` — what is missing is a `METHOD:PUBLISH` emitter for a **new**
    event, and a date parser over prose. The only "Add to calendar" affordance today just downloads
    an .ics that already arrived as an attachment.
  - *The durable-task half needs a new table*, and per the original design constraint it must key on
    `(account, message_id/thread_id)` and **never** on `pk`, which is not stable across re-ingest.
  The AI `action` string in the Commitments panel is read-only and parses no dates.
- **Today & next's remaining blind spots** · low value · effort S — `ics.next_occurrence` reads
  DAILY/WEEKLY/MONTHLY/YEARLY with INTERVAL, COUNT, UNTIL, BYDAY and BYMONTHDAY; any other part
  (BYSETPOS, BYMONTH, BYWEEKNO, BYHOUR, HOURLY) makes `_occurrences` return None and
  `server.upcoming` shows only the series' first date. Ingest keeps an invite's body only for the
  Inbox (`ingest._process` with `with_body`), so an invite filed elsewhere by a server rule is
  listed once opened. Bodies stored before `mailparse.parse` recorded inline-only calendar parts
  keep attachment rows without one; a one-off re-parse of stored bodies containing
  `text/calendar` would list them.
- **AI pre-fills the Screener's verdict** · medium value · effort M — Rescope of the old
  "auto-triage the ambiguous residual": the Screener already is a learning Imbox that writes
  `mailmind_rules.json` one sender at a time, so do not rebuild it. What is missing is a
  confidence-filtered claude pass suggesting Allow / Screen-out — today `is_junk` is set purely
  deterministically at ingest and no code path appends an AI verdict to the rules file. While in
  there, fix `sweep_account_ai`, whose docstring advertises an AI verdict it does not use.
- **Scheduled morning briefing + summarize-this-sender** · medium value · effort M — Cross-message
  synthesis ships in three flavours, but the 60s tick only calls `wake_snoozed()` and
  `followups_tick()`, so `briefing_cache` is never warmed before you open the tab, and the
  Notification API fires only for new mail. Second half: a claude pass over one sender's recent
  threads, hung off the contact card — `contact_card()` is pure SQL relationship stats with no AI
  call.
- **Weekly Review v2** · effort M overall — three follow-ons of different shapes; treat separately:
  - **Answer "you handled N this week"** · medium value · effort S — unanswerable today because no
    `archived_at` column exists. Add it as a new migration in `mailmind/platform/db/migrations`,
    stamp it in the `/api/archive` handler, and **clear it on the undo path** — otherwise the
    column lies after every undo.
  - **Meetings and deadlines in the retrospective** · medium value · effort M — feed `_invite_for()`'s
    parsed VEVENTs into `summarize_review.py`, which has zero calendar/ics references today.
  - **Scheduled delivery of the review** · needs a decision first, not a sub-bullet — it would be the
    first time the server sends mail with no human in the loop.
- **Tidy that runs itself, with review** · medium value · effort M — `doTidy()` is reachable only
  from the two `[data-tidy="go"]` delegates and always calls `confirm()`. The intended v2 — run
  daily or on brief load, gated by an opt-in preference, with a "we filed N — review / undo"
  surface — needs a persisted setting, a scheduled trigger and that review strip. Zero matches for
  `autoTidy` / `mm_tidy` anywhere.

## Search, rules and organization

- **Search-as-you-type suggestions and clickable scoping** · high value · effort M — Roughly a
  quarter done: the contact card gives a working "more from this sender". Missing is any suggestion
  endpoint (`do_GET`'s chain has no `/api/suggest`), a debounced popover under `#search` completing
  senders/domains from the same source `/api/contacts` indexes plus operator names from `search.py`'s
  fixed grammar, and a "this conversation only" scope chip. The `#plist` palette combobox is the
  working precedent for the dropdown mechanics — it is a nav finder, not this.
- **Incoming rules engine, and create-filter-from-search** · high value · effort L — `rules.py` is a
  one-dimensional verdict list, not an engine: `KEYS = (keep_senders, block_senders, keep_domains,
  block_domains)` and `add(token, verdict)`. There is no condition language, no action verb, no
  rules table, and no ingest-time evaluation beyond `mailclass.is_junk`. Where v1 hooks in:
  **evaluate in `ingest.py` immediately after `isj = 1 if mailclass.is_junk(b) else 0`, and express
  conditions by reusing `search.build()`'s existing operator grammar** rather than inventing a
  second one — that keeps "create filter from search" a serialization step instead of a translation
  layer. Ship the local-action subset (auto-star / auto-read / auto-label) well before any IMAP
  MOVE, and re-scope first: "+ Save view", the Screener and `/api/cleanup/execute` already cover the
  "this sender floods me" case filters usually serve.
- **Document Hub v2** · high value · effort M — Three follow-ons in value order: an inline PDF/image
  preview so you needn't download to check (`docOpen` only opens the source email; no preview modal
  exists), group-by-sender/type sections in place of the flat row list, and a persistent rail lane so
  the Hub is a place rather than a ⌘K command.
- **Prose search without the `!` prefix** · medium value · effort S — The hard part is built and
  proven: `ai_do_plan()` calls the model with `DO_PLAN_SCHEMA`, re-parses the returned filter through
  `search.build()` (the AI never touches SQL), and `runDo()` drops the translated operators into the
  real, editable search box. Missing is the search-shaped front door — bare prose with no prefix
  still goes to `/api/search` as literal FTS — plus a labelled, dismissible "translated" chip so you
  can get the literal search back.
- **Cross-account virtual folders** · medium value · effort M — `snoozed_in`, `muted_in`,
  `waiting_in` and `replylater_in` are each `WHERE m.account=?`, and `folders_for(account)` counts
  per account, so there is no all-accounts Snoozed to match the shipped all-inboxes ✉ dot. Saved
  lanes already work cross-account because they ride `/api/search`'s `scope` param — the four helpers
  and their `store.list_*` counterparts need the same branch.
- **Batch cleanup with a stated rationale** · medium value · effort M — Cleaning ten noisy senders
  means ten preview-and-confirm cycles: flood rows are strictly one-at-a-time (`#floodList` delegate
  → `openCleanupPreview`), with no marked set. Wanted: multi-select executing one batched block+sweep
  under a single undo group, and a per-sender AI rationale ("you have never opened one of these 47 in
  6 months") — `cleanup_senders` is pure SQL `noise_score` today, with no claude call and no
  rationale field.
- **Gmail multi-label views** · low value · effort M — Carried over from the original phase plan and
  never built. `X-GM-LABELS` is used for **writes** only (archive drops `\Inbox`; the relabel path
  adds one), but labels are never FETCHed and there is no `labels` / `message_labels` junction, so a
  Gmail message cannot be seen under all the labels it actually carries.
- **Honest thread counts** · low value · effort S — The list badge counts
  `COUNT(*) OVER (PARTITION BY m.thread_id)` inside `WHERE m.account=? AND m.folder=?`, while
  `thread_json()` selects across all folders — so a thread with a reply in Sent shows "2" and opens
  3. Either count account-wide or relabel the tooltip from "N messages" to "N in this folder".
- **Keep the query when switching folders** · low value · effort S — `selectFolder()` calls
  `clearSearch(false)`, which wipes both `#search`'s value and `state.q`. Preserving at least the text would make exploring results far less
  retype-heavy.

## Speed and the connection layer

- **Turn the connection pool on by default** · high value · effort M — `pool.py` is complete,
  safety-reviewed and wired into three read call sites (`ingest.fetch_body`, `_live_folder_list`
  backing the folder rail, and `manage.plan_account` backing `/api/stats`), but `_targets()` reads
  `MAILMIND_POOL`, which is unset by default, so every op falls through to a fresh
  `accounts.connect()` + logout — today's ~37s cold open. Remaining: validate against Proton Bridge
  (a bridge branch exists in `pool._take` with its own max-age/max-uses, but `_is_bridge()` excludes
  it from the `MAILMIND_POOL=1` catch-all), extend borrowing to mutations (only reads borrow today),
  and default it on.
- **Make a gate for each account the default** · high value · effort S — `MAILMIND_WORKERS=account`
  gives each account its own gate (`mailmind.platform.workers.gates`), so a slow Proton fetch no
  longer blocks archive, send or open on another account; unset, one lock is still every
  account's gate. Soak it on real accounts, then make it the default and delete the flag. Then
  give each account's worker the connection it owns (the pool dissolved into it), and split the
  jobs that still pass `gates.EVERY_ACCOUNT`, which wait for every gate: a draft save whose old
  copy is in another account (it needs just the two accounts' gates, which `gates.run` can't take
  yet), an undo with no account, and a message whose account `mailops.account_of` can't find.
- **Persist and document `MAILMIND_POOL` / `MAILMIND_WARM_FOLDERS`** · medium value · effort S — Both
  features are hard default-off and nothing in the repo sets either — no launcher, no plist. A plain
  `python3 server.py` therefore silently reverts to fresh-connect-per-op and INBOX-only sync. Pick a
  persistence story (launcher script, or derive the account list from `mailmind.config.json`).
- **Cache the last message list per account** · medium value · effort M — `loadFolders()` keeps
  `state.foldersByAcct` and renders the rail instantly from cache, so the rail no longer blanks on
  account switch. There is no equivalent for messages — `loadCMessages()` always refetches
  `/api/messages`, so the row area still goes empty every time.
- **Self-host the fonts, add gzip and ETag** · high value · effort M — dashboard.html still
  preconnects to fonts.googleapis.com and blocks on a remote stylesheet for Nunito Sans,
  Shippori Mincho and Klee One — the only remote dependency in an app whose README sells local-first and
  privacy-first, so this is a stated-values violation, not just latency. Self-hosting them also
  takes Google Fonts out of `perimeter.page_policy`. Server side there is no `Content-Encoding` or
  `ETag` anywhere (the only zlib use is `crc32` for a cache fingerprint; the API's answers carry
  `Cache-Control: no-store`, the event stream `no-cache`, and the page none); gzip is ~15 lines
  in the response path and worth real time on a 340KB HTML file plus the large JSON digests.
- **Pool the reconcile read** · low value · effort S — Half done: `reconcile_account` now connects
  with `timeout=15`, capping the exposure, but it still opens its own connection while holding
  the IMAP gate instead of using `pool.borrow` the way the three read call sites do.

- **Soak IMAPClient on real accounts, then make it the default** · high value · effort S — The
  new door reaches an account through IMAPClient when its config asks (`"imap_adapter":
  "imapclient"`; the startup line counts them). Both adapters pass the contract against the fake
  and against Dovecot (`just test-dovecot`), and the whole suite passes under either. On real
  Gmail, IMAP and Proton Bridge accounts the two answered every read alike (capabilities, folders,
  status, searches, headers, flags, bodies, IDLE), but nothing has written through IMAPClient
  there yet, nor run on it for days. The owner asks for it on one account, then on all, and uses
  the new door daily for a week, watching Settings' sync health and the server's output;
  removing the key goes back. Then `front.build` calls `mailbox.use("imapclient")` and the ask
  goes.

## Keyboard, screen readers and reversibility

- **Real listbox semantics for the message list** · high value · effort M — `<div id="rows">` carries
  no role, and `rowMarkup()` emits plain `<div class="row cmsg" data-pk>` with no `role="option"`,
  no `aria-selected` and no tabindex; selection is only a CSS class and `cmove()` never calls
  `.focus()`. A screen-reader user can hear the palette
  and the recipient autocomplete — both correctly `role="listbox"` — but not the actual message
  list. Biggest remaining a11y gap.
- **Finish the row hover cluster, and hover-to-prefetch** · medium value · effort S — `rowMarkup()`'s
  `.racts` cluster offers Archive, Delete and Snooze; Star and Mark-read are still key- or
  toolbar-only. Separately, a debounced `mouseover` on
  `#rows` calling the existing `prefetchBody` would make the first open of a hovered row instant
  (today prefetch fires only on open and on thread expansion).
- **Live regions on the toast, send float and undo float** · medium value · effort S — `#srLive`
  exists and works, but it is called from only nine sites; `toast()`, `#sendFloat` and `#undoFloat`
  carry no `aria-live` or `role="status"`, so toasts, send status and the Undo countdown are silent.
  Pair this with the outstanding amber-on-dark contrast
  check on `--faint` / `--mute`.
- **One `toastAction()` primitive with a draining bar** · medium value · effort M — `toast()` (2600ms
  on `#toast`) and `showUndoFloat()` (8000ms on `#undoFloat`) are two independent primitives with two
  lifetimes, and the only animation on the floater is a one-shot slide-in — nothing makes the undo
  window legible. Collapse toast / showUndoFloat / showSendFloat into `toastAction(label, undoFn, ms)`
  with a CSS width-transition bar; `startUndoCountdown()` already proves the pattern, but only for
  sends.
- **One key registry driving keys, palette and legend** · medium value · effort M —
  `buildPaletteEntries()` already carries a `key` field on most entries, but the legend is
  hand-written `<kbd>` markup and the actual handling lives in a separate switch in the keydown
  listener. Three sources of truth that can and do drift; emit the legend from the registry grouped
  by section and dispatch the switch through the same table.
- **A clickable context bar** · low value · effort S — `#clientCtx` says where you are, but its
  account and folder segments are plain text. Make them clickable and append the unread count
  already sitting in state.
- **Remember the last view across reloads** · medium value · effort S — Boot restores layout
  preferences but not the location: there is no `mm_account` / `mm_folder` /
  `mm_allinbox` key, so `openMailbox()` always lands on `accountsList[0]` INBOX. Still the
  ~15-line change: write the tuple on `selectFolder` / `enterAllInbox` / monogram click, read it in
  the boot IIFE before `openClient()`.
- **Sticky "N new" pill for snooze-wake** · low value · effort S — New mail gets a persistent
  clickable pill via `showNewPill()`; a snoozed message waking back into the open folder either
  silently reloads or flashes a 2.6s toast. `onSnoozeWake` never calls the pill functions that
  already exist.
- **A heading for the reader** · low value · effort S — With a message open, axe reports
  `page-has-heading-one`, the only finding on the inbox, reader and composer. Make the subject an
  `<h1>`, and lower `tests/golden/browser/axe_baseline.json` with `--update-golden`.

## The reading room and the visual system

- **Sender identity inline, with a From/Reply-To spoof hint** · medium value · effort M — The contact
  card delivers most of the data (monogram, "N emails · replied N× · first X ago"), but it is a
  click-through popover; the key facts should appear inline in `.rd-from`. And there is no spoof
  warning at all: `replyTo()` reads `d.reply_to` to pick the recipient but nothing ever compares it
  to `from_addr` — the only "spoof" matches in the file are CSS comments about iframe chrome.
- **A cached image reload, and no jump on open** · medium value · effort M — `loadImagesFor(pk)`
  unconditionally refetches the body with `images=1` and re-renders the whole reader; memoize the
  payload per pk and swap only the srcdoc, as `rerenderMail` already does for reading settings, so
  toggling twice neither refetches nor loses scroll position. The iframe has a `min-height` and
  height transition but its true height is still applied only in the `load` handler
  (`sizeMailFrame`), so opening still jumps.
- **Dark mail colors under the Day theme** · low value · effort S — `bodyDoc()` themes dark mail
  from the live tokens (`tk("--bg")`, `tk("--ink")`), which are the light ones under
  `body.theme-light`. Choosing Dark under Aa there leaves plain text on light paper and inverts rich
  mail onto a light page. Dark needs its own night palette that doesn't follow the app theme.
- **Density modes (Comfortable / Cozy / Compact)** · low value · effort M — Zero matches for
  `density` or `data-density`; Mailbox row metrics are hardcoded in the `.row.cmsg` rules. Worth more
  now that rows carry a two-line summary and a next-step chip (about six rows per screen at 900px):
  a `--row-pad` / `--row-lh` pair under `body[data-density]`, with Compact clamping the gist to one
  line and hiding the chip, pairs naturally with the missing Preferences pane.
- **High-contrast toggle** · low value · effort S — Unbuilt: no `body.hc`, no preference. Lower
  priority now that the base palette passes AA, but the light theme proved the pattern — one
  body-class override of the `:root` token block is the whole feature.
- **An email attached to an email** · medium value · effort M — To the `email` package a
  message/rfc822 part is a container, so `mailparse.extract_part`'s `get_payload(decode=True) or
  b""` gives 0 bytes. The chip shows no size and the download is an empty .eml. `export_eml`
  skips it. Forwarding with files (`server._extract_attachments`) sends it as an empty base64
  message/rfc822 part, which RFC 2046 forbids, and a .eml attached in the composer goes out the
  same way. `parse()` also walks into it. The inner email's files are listed and forwarded as the
  outer's own. When the outer message has no text/plain, the inner email's text becomes the
  stored text: search, the snippet, a reply's plain quote, and the reader body too if there is no
  HTML either. `_resume_files` refuses drafts holding one until this is fixed. The fix: serve the
  inner message's bytes (a compat32 generator with CRLF, to stay close to the original) for size,
  download and forward. Attach message/rfc822 in `build_message` as a parsed message, which goes
  out 8bit, for forwards and uploads alike. Stop listing inner files while keeping the walk's part
  indexes stable. Then lift the refusal. Pinned by
  `test_hwf_an_attached_emails_files_are_not_listed_as_the_outers` and
  `test_hwf_an_attached_emails_text_is_not_taken_for_the_outers`.
- **format=flowed is read as hard breaks** · low value · effort S — Nothing reads the `format` or
  `DelSp` parameters. A text-only flowed message shows in the reader's `<pre>` hard-wrapped at
  about 72 columns, with a stray space on each space-stuffed line, and DelSp=yes splits words.
  Search and summaries are barely touched. Another app's flowed draft reopens whole with its soft
  breaks fixed in, then goes out without `format=flowed`. The fix is an RFC 3676 decoder in
  `mailparse._decode` and `ingest.text_body`. Per line: count the quote depth, then drop one
  stuffing space, then join a line ending in a space to the next one at the same depth (deleting
  the space when `delsp=yes`). Never join the `-- ` signature line.

## Setup, first run and housekeeping

- **A real Settings home with a Preferences tab** · high value · effort M — `#settingsModal` is a
  single-purpose "Email connections" dialog; there is no tab strip, and `config.py` exposes only
  accounts/alias/signature/webmail. It has become the natural landing zone for several other gaps —
  density, high-contrast, AI model, the 10s undo-send window, the 45s/120s/20s poll intervals,
  notification preferences and quiet hours — and for the nine `mm_*` localStorage keys that
  currently hold every preference and die with a browser-data clear. Needs the tab strip, a
  preferences block in `mailmind.config.json`, and a read/write route.
- **Standard-format export from the UI** · medium value · effort S — `export.py` implements
  `export_eml()` and `export_mbox()` (the latter with proper mboxrd `From `-escaping), but the only
  HTTP route is `/api/export` → `_serve_db_export()`, a `.db` snapshot, and Settings exposes exactly
  one control, the ⤓ Backup (.db) link. No route references `export_eml`/`export_mbox`. Since `.db`
  is a MailMind-only format, the stated escape hatch does not actually let a user leave. Add a
  format picker beside the existing Backup button and a route that streams a zip.
- **60-second guided first run** · medium value · effort M — `.firstrun` is a static empty state
  shown only when no accounts are connected; there are no coach-marks and no localStorage flag
  gating a walkthrough. The surfaces worth explaining have multiplied since this was first proposed
  — today's brief, the folder rail and focus reading, ⌘K, `?ask` / `!do` prefixes, the
  Screener — so it is worth more now, not less. Gate on one flag and reuse the
  existing scrim and z-ladder.
- **First-sync feedback, with a live count** · medium value · effort S — The "Reading your inbox
  for the first time…" spinner went with the Briefing view; `renderClientList()` shows a bare
  "Nothing here." while a first sync runs, so a user who has just added an account gets no
  signal. And the string is fixed — `sync()` polls `/api/digest` every 3s but reads only
  `d.sync.running`, ignoring the message count already in `d.stats`, so "read N messages so far…" is
  two lines inside the existing loop.
- **Consistency sweep: buttons, empty-state strings, toast vs. persistent** · low value · effort M —
  Three concrete, checkable changes rather than a vibe: (1) one `.btn-retry` replacing `.ai-rerun`,
  `.frail-retry`, `.ob-retry` and `.do-confirm`; (2) one `EMPTY` string table replacing the four
  inlined phrasings ("Nothing here." / "No matches." / "Nothing matches." / "No messages in this
  folder."); (3) a stated rule for toast-vs-persistent — **persistent iff the event happened while
  the user was not looking** — then apply it, since snooze-wake toasts today while new mail gets a
  sticky pill.

## Open decisions

These are carried forward from the original phase plan because they need a call, not an
implementation.

- **At-rest encryption for `mailmind.db`** — the DB now holds full bodies and attachments, which
  materially raises its value. Current posture is documented-and-gitignored at 0600, with no
  encryption anywhere in the tree. Stdlib-only encryption is awkward; the question is whether that
  posture stays acceptable for a single-user localhost machine or becomes opt-in encryption.
- **How aggressively to prefetch folders** — on-demand sync bounds disk and first-sync cost, but
  unified-inbox-across-accounts and global search both want broader coverage.
- **Default remote-image policy** — block-by-default with per-message "Load images" (current, and the
  privacy-preserving choice) vs. allowing images for known/personal senders.

## Next up

1. **Turn the connection pool on by default and document the flags** (high / M) — the cold-open fix
   is written, reviewed and dormant behind an unset env var. Follow it immediately with **give
   each account its own IMAP gate**, the structural half.
2. **Real listbox semantics for the message list** (high / M) — the biggest remaining accessibility
   gap, and the message list is the one surface a screen reader cannot use today.
