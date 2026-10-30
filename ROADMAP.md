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

- **Reply variants and one-tap micro-replies** · high value · effort S — The single-shot `✦ Draft`
  works and never auto-sends, but it reaches only one tone: `aiDraftReply()` posts a hardcoded
  `tone:"balanced"`, even though `draft_reply.py` already implements `_TONES` (brief, balanced,
  warm, formal) and accepts a free-text `instruction` the UI never sends. Missing: a
  Brief/Detailed/Warm picker (~10 lines against an endpoint that already supports it), an
  instruction box, and Yes / No / Thanks / Send-me-a-time chips in the reader.
- **Send-time guards before an irreversible send** · high value · effort S — `composeSend()`
  validates exactly two things — at least one recipient, and no malformed addresses — then POSTs
  `/api/send`. Wanted, nearly all client-side: a first-contact flag on recipients, a confirm strip
  when a reply-all fans out past ~5 recipients, a body scan for "attached"/"ci-joint" against an
  empty attachment list, and an empty-subject check. Note on effort: `contact_card(addr)` already
  returns `known` via `/api/contact?addr=`, but that is **one round trip per address**, so the
  first-contact flag needs either N calls or a new bulk-`known` field — budget for the latter.
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

- **Make AI presence and setup state visible** · high value · effort S — `digest_json()` returns
  items/stats/accounts/aliases/status/sync and nothing about the model; grepping every module for
  `ai_available`, `ai_model` or a `claude --version` probe returns zero hits. A user with no claude
  CLI installed today gets a silently degraded Briefing and one per-request error string. Needs a
  startup probe cached in server state, two keys on `/api/digest`, a calm empty-state card, and a
  one-line "nothing leaves 127.0.0.1" explainer.
- **Events and tasks extracted from prose** · high value · effort L — Nothing turns message text
  into a calendar entry or a dated task. Two distinct halves, and the seam matters:
  - *The .ics half is cheap.* `ics.py` already has `_fold`, `_ics_esc`, `_ics_dt` and emits
    `BEGIN:VEVENT` inside `build_reply` — what is missing is a `METHOD:PUBLISH` emitter for a **new**
    event, and a date parser over prose. The only "Add to calendar" affordance today just downloads
    an .ics that already arrived as an attachment.
  - *The durable-task half needs a new table*, and per the original design constraint it must key on
    `(account, message_id/thread_id)` and **never** on `pk`, which is not stable across re-ingest.
  The AI `action` string in the Commitments panel is read-only and parses no dates.
- **Read-only agenda peek and a "happening soon" nudge** · medium value · effort M — Invites are
  parsed per-message-open in `_invite_for()` and discarded; the schema has no events table, so
  there is no queryable set of upcoming meetings. Needs VEVENT persistence at ingest (or a scan
  over messages with a text/calendar part) plus a rail or briefing widget. Clean follow-on to the
  shipped invite parser.
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
    `archived_at` column exists. Add it via the `_MIGRATIONS` table, stamp it in the `/api/archive`
    handler, and **clear it on the undo path** — otherwise the column lies after every undo.
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
- **Document Hub v2** · high value · effort M — The Hub silently caps at 400: `documents_json`
  accepts a `limit` but has no `offset`, and the client never passes either. Four follow-ons in
  value order: an inline PDF/image preview so you needn't download to check (`docOpen` only opens the
  source email; no preview modal exists), a load-more, group-by-sender/type sections in place of the
  flat row list, and a persistent rail lane so the Hub is a place rather than a ⌘K command.
- **Prose search without the `!` prefix** · medium value · effort S — The hard part is built and
  proven: `ai_do_plan()` calls the model with `DO_PLAN_SCHEMA`, re-parses the returned filter through
  `search.build()` (the AI never touches SQL), and `runDo()` drops the translated operators into the
  real, editable search box. Missing is the search-shaped front door — bare prose with no prefix
  still goes to `/api/search` as literal FTS — plus a labelled, dismissible "translated" chip so you
  can get the literal search back.
- **One search grammar in both modes** · medium value · effort M — Two engines are live: mailbox mode
  hits `/api/search`, while Briefing routes to `applyFilters()`, a substring test over already-loaded
  digest rows, so `from:` / `is:unread` / `category:` silently do nothing there. Placeholders were
  made mode-aware as a stopgap; the real fix is routing Briefing through `/api/search`, or at minimum
  running `search.build()` client-side over `state.all`.
- **Cross-account virtual folders** · medium value · effort M — `snoozed_in`, `muted_in`,
  `waiting_in` and `replylater_in` are each `WHERE m.account=?`, and `folders_for(account)` counts
  per account, so there is no all-accounts Snoozed to match the shipped all-inboxes ✉ dot. Saved
  lanes already work cross-account because they ride `/api/search`'s `scope` param — the four helpers
  and their `store.list_*` counterparts need the same branch.
- **Unsubscribe coverage for senders whose bodies were never fetched** · medium value · effort M —
  Cleanup can only offer an unsubscribe where a body happens to be cached: `_resolve_unsub` does
  `raw = store.get_body(...); if raw is None: continue`, and `ingest.py` has zero references to
  `list_unsub_raw` / `list_unsub_method`, so those columns are populated lazily from the reader and
  never at ingest. Fix both ends: capture the List-Unsubscribe headers during ingest for all future
  mail, and add an on-demand header-only IMAP fetch for the backlog.
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
- **Keep the query when switching folders or modes** · low value · effort S — `selectFolder()` calls
  `clearSearch(false)`, which wipes both `#search`'s value and `state.q`; `setMode()` likewise only
  rewrites the placeholder. Preserving at least the text would make exploring results far less
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
- **Shard `_ops_lock` per account** · high value · effort M — Still one process-wide
  `threading.Lock()`, taken at ~15 sites including `message_body`, first-folder sync, the mutation
  queue, undo, `live_stats`, reconcile and the outbox worker — so a slow Proton fetch still blocks
  archive/send/open on every other account. The pool's per-account slot lock is taken *inside* it, so
  the pool cuts per-op latency without buying any cross-account concurrency. This is the structural
  half of the cold-open fix.
- **Persist and document `MAILMIND_POOL` / `MAILMIND_WARM_FOLDERS`** · medium value · effort S — Both
  features are hard default-off and nothing in the repo sets either — no launcher, no plist. A plain
  `python3 server.py` therefore silently reverts to fresh-connect-per-op and INBOX-only sync. Pick a
  persistence story (launcher script, or derive the account list from `mailmind.config.json`).
- **Background folder first-sync** · medium value · effort M — First open of a big folder still runs
  `ingest.sync_folder(..., full=True)` synchronously inside the HTTP request while holding the global
  lock, swallowing failures with a print. The stored-first + SSE-refresh progressive fill was never
  built; no thread is spawned for it anywhere.
- **Cache the last message list per account** · medium value · effort M — `loadFolders()` keeps
  `state.foldersByAcct` and renders the rail instantly from cache, so the rail no longer blanks on
  account switch. There is no equivalent for messages — `loadCMessages()` always refetches
  `/api/messages`, so the row area still goes empty every time.
- **Self-host the fonts, add gzip and ETag** · high value · effort M — dashboard.html still
  preconnects to fonts.googleapis.com and blocks on a remote stylesheet for Nunito Sans and
  Shippori Mincho — the only remote dependency in an app whose README sells local-first and
  privacy-first, so this is a stated-values violation, not just latency. Server side there is no
  `Content-Encoding` or `ETag` anywhere (the only zlib use is `crc32` for a cache fingerprint, and
  the only caching header is one `no-cache`); gzip is ~15 lines in the response path and worth real
  time on a 340KB HTML file plus the large JSON digests.
- **Pool the reconcile read** · low value · effort S — Half done: `reconcile_account` now connects
  with `timeout=15`, capping the exposure, but it still opens its own connection while holding
  `_ops_lock` instead of using `pool.borrow` the way the three read call sites do.
- **CONDSTORE / QRESYNC flag-delta** · low value · effort M — Carried over from the original phase
  plan; no `CONDSTORE`, `QRESYNC` or `MODSEQ` reference exists anywhere. Re-fetching FLAGS for the
  visible window is genuinely enough today; revisit only if flag-sync cost becomes visible.

## Keyboard, screen readers and reversibility

- **Real listbox semantics for the message list** · high value · effort M — `<div id="rows">` carries
  no role, and both `rowMarkup()` and the Briefing `renderList()` emit plain `<div class="row"
  data-pk>` with no `role="option"`, no `aria-selected` and no tabindex; `markSel()` only toggles a
  CSS class and `move()`/`cmove()` never call `.focus()`. A screen-reader user can hear the palette
  and the recipient autocomplete — both correctly `role="listbox"` — but not the actual message
  list. Biggest remaining a11y gap.
- **Finish the row hover cluster, and hover-to-prefetch** · medium value · effort S — `rowMarkup()`'s
  `.racts` cluster offers Archive, Delete and Snooze (Snooze only on multi-message conversation
  rows); Star and Mark-read are still key- or toolbar-only. Separately, a debounced `mouseover` on
  `#rows` calling the existing `prefetchBody` would make the first open of a hovered row instant
  (today prefetch fires only on open, thread expansion and the next Focus card).
- **Live regions on the toast, send float and undo float** · medium value · effort S — `#srLive`
  exists and works, but it is called from only nine sites; `toast()`, `#sendFloat` and `#undoFloat`
  carry no `aria-live` or `role="status"`, so toasts, send status and the Undo countdown are silent.
  Briefing's `select()` never announces either. Pair this with the outstanding amber-on-dark contrast
  check on `--faint` / `--mute`.
- **One `toastAction()` primitive with a draining bar** · medium value · effort M — `toast()` (2600ms
  on `#toast`) and `showUndoFloat()` (8000ms on `#undoFloat`) are two independent primitives with two
  lifetimes, and the only animation on the floater is a one-shot slide-in — nothing makes the undo
  window legible. Collapse toast / showUndoFloat / showSendFloat into `toastAction(label, undoFn, ms)`
  with a CSS width-transition bar; `startUndoCountdown()` already proves the pattern, but only for
  sends.
- **Briefing-mode triage parity** · medium value · effort S — Briefing rows vanish instantly
  (`afterRemove()` filters state and rebuilds via `renderList()`, never touching `healPk()`), while
  Mailbox rows collapse smoothly. And `blockIt()` — the doubly-consequential verb, since it trashes
  the message *and* writes a persistent rule — is the one action with no Undo affordance, unlike
  `trash()` and `archiveSel()`.
- **One key registry driving keys, palette and legend** · medium value · effort M —
  `buildPaletteEntries()` already carries a `key` field on most entries, but the legend is
  hand-written `<kbd>` markup and the actual handling lives in a separate switch in the keydown
  listener. Three sources of truth that can and do drift; emit the legend from the registry grouped
  by `modes` and dispatch the switch through the same table.
- **Persistent context bar in both modes** · medium value · effort S — `#clientCtx` exists and is
  populated in nine places, but `body:not(.mode-client) .client-ctx{display:none}` hides it entirely
  in Briefing, where the original "no 'where am I' indicator" complaint still holds verbatim. Also:
  make the account and folder segments clickable (they are plain text today) and append the unread
  count already sitting in state.
- **Remember the last view across reloads** · medium value · effort S — Boot restores seven
  layout/mode preferences but not the location: there is no `mm_account` / `mm_folder` /
  `mm_allinbox` key, so `setMode('client')` always lands on `accountsList[0]` INBOX. Still the
  ~15-line change: write the tuple on `selectFolder` / `enterAllInbox` / monogram click, read it in
  the boot IIFE before `openClient()`.
- **Sticky "N new" pill for snooze-wake** · low value · effort S — New mail gets a persistent
  clickable pill via `showNewPill()`; a snoozed message waking back into the open folder either
  silently reloads or flashes a 2.6s toast. `onSnoozeWake` never calls the pill functions that
  already exist.

## The reading room and the visual system

- **Sender identity inline, with a From/Reply-To spoof hint** · medium value · effort M — The contact
  card delivers most of the data (monogram, "N emails · replied N× · first X ago"), but it is a
  click-through popover; the key facts should appear inline in `.rd-from`. And there is no spoof
  warning at all: `replyTo()` reads `d.reply_to` to pick the recipient but nothing ever compares it
  to `from_addr` — the only "spoof" matches in the file are CSS comments about iframe chrome.
- **Serif plain-text bodies, and a cached image reload** · medium value · effort M — `bodyDoc()`'s
  plain-text branch emits `pre{white-space:pre-wrap;font:15px/1.6 ui-monospace}` — pre-wrap landed,
  monospace stayed, and `--font-reading` (now an alias of the Nunito Sans UI face) is never used
  inside the iframe; a size and serif choice for plain-text mail belongs here too. Separately, `loadImagesFor(pk)` unconditionally refetches the body with `images=1` and
  re-renders the whole reader; memoize the payload per pk and swap only the srcdoc so toggling twice
  neither refetches nor loses scroll position. The iframe has a `min-height` and height transition
  but its true height is still applied only in the `load` handler, so opening still jumps.
- **Replace emoji with an inline-SVG monoline icon set** · medium value · effort L — Now the largest
  craft gap, since the type system and the Meadow palette with its Day theme have landed.
  Emoji are still the entire icon vocabulary (✦×46, ⚡×15, ⏰×10, ☰×7, 📎×7 …), including the literal
  `FOLDER_ICON` map and the toolbar; there is no `<symbol>` sprite and no `<use>` anywhere. Needs ~24
  glyphs using `currentColor` plus a swap of `FOLDER_ICON`, `.tbtn`, `.cab`, `.mono-dot` and the
  inline emoji in template literals.
- **Density modes (Comfortable / Cozy / Compact)** · low value · effort M — Zero matches for
  `density` or `data-density`; Mailbox row metrics are hardcoded in the `.row.cmsg` rules. Worth more
  now that rows carry a two-line summary and a next-step chip (about six rows per screen at 900px):
  a `--row-pad` / `--row-lh` pair under `body[data-density]`, with Compact clamping the gist to one
  line and hiding the chip, pairs naturally with the missing Preferences pane.
- **High-contrast toggle** · low value · effort S — Unbuilt: no `body.hc`, no preference. Lower
  priority now that the base palette passes AA, but the light theme proved the pattern — one
  body-class override of the `:root` token block is the whole feature.

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
  — mode toggle, the folder rail and focus reading, ⌘K, the ⚡ to-do filter, importance colors, `?ask` / `!do`
  prefixes, the Screener — so it is worth more now, not less. Gate on one flag and reuse the
  existing scrim and z-ladder.
- **First-sync feedback in Mailbox, with a live count** · medium value · effort S — The "Reading your
  inbox for the first time…" spinner exists in Briefing only; `renderClientList()` shows a bare
  "Nothing here." while a first sync runs, so a user who lands in Mailbox after adding an account
  gets no signal. And the string is fixed — `sync()` polls `/api/digest` every 3s but reads only
  `d.sync.running`, ignoring the message count already in `d.stats`, so "read N messages so far…" is
  two lines inside the existing loop.
- **Honest empty vs. silently-failed folder** · low value · effort S — A first-open `sync_folder`
  failure is caught and only printed, and the `messages_in` return dict carries no error flag, so an
  empty folder and a folder whose sync threw look identical. Without that flag the empty state cannot
  offer "Couldn't sync this folder — Retry."
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

1. **Reply variants and one-tap micro-replies** (high / S) — the backend already accepts `tone` and
   `instruction`; this is a picker and a chip row against a working endpoint.
2. **Make AI presence and setup state visible** (high / S) — a probe, two digest keys and one card;
   without it a machine with no claude CLI degrades silently.
3. **Send-time guards** (high / S) — the highest safety-per-line item left. Budget for a bulk-`known`
   field rather than N calls to `/api/contact`.
4. **Turn the connection pool on by default and document the flags** (high / M) — the cold-open fix
   is written, reviewed and dormant behind an unset env var. Follow it immediately with **shard
   `_ops_lock` per account**, the structural half.
5. **Real listbox semantics for the message list** (high / M) — the biggest remaining accessibility
   gap, and the message list is the one surface a screen reader cannot use today.
