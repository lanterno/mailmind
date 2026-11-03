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
- **Draft saves that lose or duplicate text** · high value · effort S — Three gaps around
  `composeSaveDraft`, all in the composer's own saves:
  - A close-save that fails loses the message. `composeClose` tears the composer down before its
    save resolves, and a failure leaves only a 2.6-second toast that isn't a live region.
    Everything since the last good save is gone, and the whole message is lost if none ever
    landed: closed within the first autosave tick, IMAP down the whole time (`do_draft` has no
    offline queue), or a red chip that makes every save fail "invalid domain". The fix is to
    snapshot the closed-over composer into `state.failedSend` and `showSendFloat("failed", …)`,
    so the existing Edit (`reopenFailed` → `makeRoom`) brings it back, and to `announce` it.
    This is the safety half of the three-way Esc prompt above.
  - Typing during a save is dropped. The save clears `cmp.dirty` when it resolves, even if keys,
    or a file's `FileReader` onload, landed while it was in flight. `autosaveTick`,
    `composeClose` and `makeRoom` all skip a composer that isn't dirty, so an Esc right after
    closes it unsaved while the footer still says "Saved". `removeChip`, removing a file and
    changing From never set `dirty` at all. The fix is an edit counter bumped wherever `dirty` is
    set, with the save clearing `dirty` only if the counter hasn't moved.
  - Overlapping saves orphan or duplicate a copy. Nothing serializes saves: an autosave tick
    during a slow manual or closing save, a double Cmd-S, two tabs. `do_draft` reads `prev`
    before taking `_ops_lock`, so the second save expunges the UID the first already replaced
    and the first save's copy is left in Drafts untracked, surviving the eventual Send (repro:
    UIDs 2 and 3 kept, the row on 3). Two first saves both carry `draft_id: null` and make two
    rows and two copies. A Send during a first save leaves that save's copy behind. The fix is a
    per-composer `cmp._saving` promise that later saves, `autosaveTick` and `composeSend` wait
    on. On the server, read `prev` (and `same`) inside the lock, and dedupe first saves on the
    composer's `idempotency_key`, which `do_draft` ignores today.
- **Drafts are recognised by folder name** · high value · effort M — `_draft_info` and
  `draft_resume` treat any folder with "draft" anywhere in its name as Drafts ("Contract drafts",
  "Clients/Draft agreements", every subfolder of one), and never check `\Draft`. Viewing a message
  there registers a `drafts` row for it and swaps Reply for Edit and Delete. Edit opens a received
  message that has a text/plain body as a draft. Discard (`do_draft`'s delete branch) or a Send
  (the outbox cleanup) then STOREs `\Deleted` and UID EXPUNGEs it, with no Undo. That is
  permanent on Dovecot-style servers; on Gmail it only drops the label. The registered row also
  adds the folder to the "new to you" lookup's draft folders, so sent mail filed there stops
  vouching for its recipients. A localized Drafts folder ("Entwürfe", "Brouillons") gets no Edit
  at all. The fix: resolve the account's Drafts folder by the `\Drafts` special-use attribute
  first and an exact name second (`mailops._find_folder` prefers name hints today), or go by the
  message's own `\Draft` flag. Give Discard and the outbox cleanup the guard `do_draft` uses for a
  copy elsewhere, `raw_sha` or `_flagged_draft`. Keep both checks, because a MailMind save that
  never synced has no `messages` row to be flagged.
- **The Drafts list never updates** · high value · effort M — `messages_in` syncs a folder only
  when it has no `sync_state` row, i.e. the first time it is ever opened against this database.
  `do_draft`, Discard and the outbox's draft cleanup change IMAP and `drafts` but never
  `messages`. So after a save, a re-save, a Send or a Discard the old copy stays listed and the
  new one never appears; a brand-new message's draft doesn't show at all. `MAILMIND_WARM_FOLDERS`
  makes it worse: `sync_folder(full=False)` only adds, so one draft piles up as several rows. No
  sync prunes Drafts, because `reconcile_account` is INBOX-only. A ghost row goes only when Edit
  gets its 409, or the user trashes or moves it. The fix, under `_ops_lock`:
  - delete each replaced row by `(account, folder, uid)`, including the copy `append_to_drafts`
    finds by Message-ID and removes without returning it;
  - pick up the new copy from APPENDUID, or an incremental sync when there is none;
  - prune Drafts on open with a folder-parameterised reconcile that keeps its UIDVALIDITY and
    empty-SEARCH guards.

  Then reload a Drafts list that is on screen, since `composeDiscard` refreshes only the rail.
- **Fresh replies name their original by pk** · medium value · effort M — A reply or forward that
  wasn't reopened from Drafts sends `in_reply_to_pk` on every save and send, and `_compose_spec`
  resolves it with `store.get_message`. pks are AUTOINCREMENT and never reused, so it can't hit
  the wrong message. But once that row is deleted (another client moved it and reconcile pruned
  it, a UIDVALIDITY reset, expiry, a queued archive draining), every save and send fails "original
  message not found". The failed send's Edit reopens with the same pk, and closing loses whatever
  wasn't saved. The outbox has the same gap: `_side_effects` looks the original up by pk after the
  10-second grace window. Archiving the thread right after replying (`mailops.archive_message`
  deletes the row) therefore silently skips `\Answered` and the follow-up record. Reopened drafts
  already avoid this through the `resume` contract. The fix: after the first save, have the
  server hand back the threading headers and quote so the composer switches to `resume`. Store the
  original's Message-ID and `thread_id` on the outbox row (append-only migration), and set
  `\Answered` by Message-ID in whatever folder it moved to (the `mailops._uid_by_msgid` pattern
  `apply_queued_mutation` uses).
- **Outgoing replies carry the reader's quote fold** · medium value · effort S —
  `compose.reply_bodies` and `forward_bodies` run the original's HTML through
  `sanitize_html.sanitize`. That function wraps the first top-level blockquote that follows text
  in the reader-only `<details class="mm-quote"><summary>··· quoted text</summary>`, so it goes
  out in the HTML part of replies to Gmail, Apple Mail and Thunderbird threads. Recipients see
  either an unstyled collapsed toggle or a stray "··· quoted text" line, and with Gmail's
  `gmail_quote` wrapper the element is mis-nested. `_plain_from_html` leaks the same words into
  the plain part when the original was HTML-only. Saved drafts keep it in `drafts.tail_html`,
  which the `resume` branch re-sends without sanitizing. The fix is a `fold=False` switch on
  `sanitize` for the three compose callers, a strip of the wrapper from stored `tail_html`, and a
  compose test that neither MIME part contains `<details`.

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
  preconnects to fonts.googleapis.com and blocks on a remote stylesheet for Nunito Sans,
  Shippori Mincho and Klee One — the only remote dependency in an app whose README sells local-first and
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
  indexes stable. Then lift the refusal.
- **Folded subjects and senders** · medium value · effort S — `ingest._process` reads headers with
  compat32 and `ingest.dec` turns a fold's CR/LF into spaces. "…items⏎ for next week" is stored
  with three spaces. HTML hides the run, but Reply and Forward put it into the subject box and the
  reply goes out with it. A fold next to an encoded-word loses its whitespace in `decode_header`
  instead ("Re:مرحبا", "Café menufor next week"), and that shows everywhere: the list, the
  reader, search tokens and AI prompts. A fold inside a quoted From display name makes
  `parseaddr` return `('', 'Bo')`, losing the sender's address. `manage.py` carries its own copy
  of `dec`. The fix is to unfold `\r?\n[ \t]+` to a single space before decoding, in both copies of
  `dec` and before `parseaddr`, not merely drop the CRLF: a kept tab is later deleted by
  `compose._clean`, gluing the words. Existing rows heal on a full resync.
- **An encoded CR or LF in To, Cc or Reply-To** · medium value · effort S — Under
  `policy.default` an encoded-word that decodes to CR or LF in an address header makes
  `headerregistry` raise, and `mailparse._hdr` doesn't guard `msg.get`. So `parse()` raises for
  the whole message. `ingest.fetch_body` never stores the body, `message_body` returns 500, and
  the reader's Retry fails the same way. Reply and Forward can't load it and its body is never
  indexed. `body_backfill_tick` re-downloads it every cycle under `_ops_lock`, and twelve such
  messages among the newest unfetched bodies starve everything older. From is safe, since ingest
  reads it with compat32. The fix: fall back in `_hdr` to the raw value from `raw_items()`,
  unfolded, decoded and with CR/LF turned to spaces, the idea `server._hdr_text` already uses.
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

## Correctness and hardening

- **Check the server's answer when starring or marking read** · medium value · effort S —
  `mailops.set_flags` sends `UID STORE +FLAGS/-FLAGS` without reading `typ`, then mirrors the
  flag locally through `store.set_local_flags`. imaplib returns a NO rather than raising it, so a
  STORE the server refuses (a UID that has gone, a read-only folder) still shows as starred or read
  until the next flag refresh. Check `typ` the way `_move_uid` does, skip the local mirror on a
  refusal, and let `/api/flag` and `/api/seen` answer `ok: false`.
- **Quote expiry tokens before they reach IMAP** · low value · effort S — `manage.expire_account`
  searches `FROM f'"{token}"'` for each key of `rules.EXPIRE`. Tokens are set only from the CLI
  (`rules.py expire`), but the server's hourly `expire_loop` runs the same search, so a token
  holding a quote breaks it and one holding a line break sends a second command. Refuse such tokens
  in `rules.set_expiry`, as `accounts.mailbox_arg` does for folder names.
- **Keep request text out of log lines** · low value · effort S — `messages_in` prints
  `[folder-sync] {account}/{folder}` with the folder as the query string decoded it, so a name
  holding `%0A` writes extra lines into the server's output. Only the local terminal reads it, but
  a forged line could pass for a real one. Print request-supplied values with `!r`.
- **Don't send mail to the API without a key** · high value · effort S — with
  `MAILMIND_AI=api` and no key, `summarize.call_claude` and `call_claude_json` still call
  `claude_api.message`, and `claude_api._post` sends an empty `x-api-key` with the prompt (mail
  text) to api.anthropic.com. `/api/review` and `/api/briefing/since` do this on every open, and
  the 401 then shows as "the API key was rejected" while `summarize.status` reports the engine as
  `none`. Once the CLI goes (D8) this is the default path. Raise before the request when `key()` is
  empty. Pinned by `test_without_a_key_no_mail_leaves_for_the_api`.
- **Undo a Proton trash without marking the mail read** · medium value · effort S — Bridge marks a
  message read on its way to Trash, and `mailops._mk_move_undo` keeps no `\Seen` state, so Undo
  brings unread mail back read. Record `seen` in the descriptor and clear `\Seen` after the move
  back when it was unset. Pinned by `test_undoing_a_proton_trash_brings_unread_mail_back_unread`.
- **Let a received nudge expire** · medium value · effort S — `followups_tick` runs
  `store.expire_followups` (older than `FOLLOWUP_EXPIRE_DAYS`, 14) and then
  `store.followup_detect_received`, whose floor is 30 days and which only skips threads that
  already have a row. The expired row is gone, so the same tick inserts it again with a fresh
  `due_ts`, and the nudge keeps nagging until day 30. Keep expired rows (state `expired`) or bound
  detection by the expiry window. Pinned by `test_a_received_nudge_stops_after_the_expiry_window`.
- **Store a time, not a pk, in Reply Later's `last_msg_ts`** · low value · effort S —
  `store.set_replylater` binds `anchor_pk` to both `anchor_pk` and `last_msg_ts`. Nothing reads
  `last_msg_ts` for `replylater` rows yet, so the harm is latent: code that ages them by it would
  see a 1970 timestamp. Pinned by `test_reply_later_records_when_the_thread_last_moved`.
- **Make cleanup's unsubscribe fire** · high value · effort S — three bugs, each pinned by a strict
  xfail in `tests/test_http_cleanup.py`. Together they mean the unsubscribe almost never goes out:
  - `cleanup_execute` sweeps the sender's INBOX mail to Trash before it calls `_resolve_unsub`.
    The moves delete the local rows that `_resolve_unsub` reads, so a sender whose mail is all in
    the INBOX gets `no_method`. Resolve the method before the sweep.
  - `_parse_list_unsub` takes one-click only when `List-Unsubscribe-Post` reads `List=One-Click`.
    RFC 8058's value is `List-Unsubscribe=One-Click`, so a compliant sender falls back to mailto
    or the landing page.
  - `_unsub_mailto_send` passes `{"from": …, "to": [...]}` as the envelope. `smtplib.sendmail`
    iterates the dict's keys, so it sends `RCPT TO:<from>` and `RCPT TO:<to>`. It also sends
    `m.as_bytes()` with bare LF line ends, which RFC 5321 forbids. Pass `[to]` and use
    `policy=email.policy.SMTP`.
- **Give up at once on a refusal that won't change** · medium value · effort S —
  `outbox.send_one` backs off and retries every failure up to `MAX_ATTEMPTS`. A 550 for a
  recipient or a 554 for the message is sent four more times over about half an hour. A 535 logs in
  with a refused password five times, which some providers count toward a lockout. Make a 5xx reply
  (`smtplib.SMTPResponseException.smtp_code >= 500`, and each code in an `SMTPRecipientsRefused`)
  terminal on the first try. Keep backing off on 4xx and on connection errors. Today's behaviour
  is pinned in `tests/golden/http_send/a_refused_login_and_a_rejected_message.json`.
- **Let a resubmitted RSVP dedupe** · low value · effort S — `do_invite_rsvp` keys the outbox row
  `rsvp:<account>:<uid>:<partstat>:` plus a random suffix, so the same POST sent twice (a retry, a
  second click before the row redraws) queues two replies to the organizer. The suffix lets a
  changed mind send again. Take the key from the request instead, as `do_send` does, and have
  `inviteRsvp()` in the frontend draw one per click. Pinned by `test_a_double_submitted_rsvp_queues_once`.
- **Parse the messages the corpus shows going wrong** · medium value · effort M — four strict
  xfails in `tests/test_world.py`, each on a fictional message:
  - a raw UTF-8 display name in From empties `from_name` and `from_addr`;
  - snippets keep the wire's carriage returns;
  - a multipart with no boundary loses its text, in the snippet and when opened;
  - a forwarded `message/rfc822` attachment downloads empty with size 0
    (`mailparse.extract_part` uses `get_payload(decode=True)`, which is None for that type).

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

1. **Draft saves that lose or duplicate text** (high / S) — closing during a failed save, or
   typing while one is in flight, silently loses what was written. These are the last ways the
   composer drops a user's words.
2. **Drafts are recognised by folder name** (high / M) — Discard on a message opened from any folder
   named like "Contract drafts" expunges it with no Undo.
3. **Turn the connection pool on by default and document the flags** (high / M) — the cold-open fix
   is written, reviewed and dormant behind an unset env var. Follow it immediately with **shard
   `_ops_lock` per account**, the structural half.
4. **Real listbox semantics for the message list** (high / M) — the biggest remaining accessibility
   gap, and the message list is the one surface a screen reader cannot use today.
