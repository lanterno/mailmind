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
    before its save job takes the IMAP gate, so the second save expunges the UID the first
    already replaced and the first save's copy is left in Drafts untracked, surviving the
    eventual Send (repro:
    UIDs 2 and 3 kept, the row on 3). Two first saves both carry `draft_id: null` and make two
    rows and two copies. A Send during a first save leaves that save's copy behind. The fix is a
    per-composer `cmp._saving` promise that later saves, `autosaveTick` and `composeSend` wait
    on. On the server, read `prev` (and `same`) inside the lock, and dedupe first saves on the
    composer's `idempotency_key`, which `do_draft` ignores today (pinned by
    `test_a_first_save_sent_twice_makes_one_draft`).
- **Drafts are recognised by folder name** · high value · effort M — `_draft_info` and
  `draft_resume` treat any folder with "draft" anywhere in its name as Drafts ("Contract drafts",
  "Clients/Draft agreements", every subfolder of one), and never check `\Draft`. Viewing a message
  there registers a `drafts` row for it and swaps Reply for Edit and Delete. Edit opens a received
  message that has a text/plain body as a draft. Discard (`do_draft`'s delete branch) or a Send
  (the outbox cleanup) then STOREs `\Deleted` and UID EXPUNGEs it, with no Undo. That is
  permanent on Dovecot-style servers; on Gmail it only drops the label. The registered row also
  adds the folder to the "new to you" lookup's draft folders, so sent mail filed there stops
  vouching for its recipients. A localized Drafts folder ("Entwürfe", "Brouillons") gets no Edit
  at all. The fix: go by the role `mailmind.mailbox.domain.roles` gives the folder (the
  `\Drafts` special-use attribute first, then the whole name), as saves already do through
  `mailops.find_role`, or by the message's own `\Draft` flag. Give Discard and the outbox cleanup the guard `do_draft` uses for a
  copy elsewhere, `raw_sha` or `_flagged_draft`. Keep both checks, because a MailMind save that
  never synced has no `messages` row to be flagged.
- **A replaced or discarded draft stays listed** · high value · effort M — a save's new copy shows
  on the Drafts list's next open (its catch-up), but `do_draft`, Discard and the outbox's draft
  cleanup change IMAP and `drafts`, never `messages`, so the copy a re-save, a Send or a Discard
  removed stays listed until reconcile's next pass over Drafts, or Edit's 409. Under the IMAP
  gate, delete each replaced row by `(account, folder, uid)`, including the copy
  `append_to_drafts` finds by Message-ID and removes without returning it, and store the new copy
  from APPENDUID. Then reload a Drafts list that is on screen, since `composeDiscard` refreshes
  only the rail.
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
  `apply_queued_mutation` uses). Pinned by
  `test_a_reply_marks_its_original_answered_after_the_original_is_archived`.
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
- **A draft with an older copy can't be edited** · medium value · effort S — When another client
  leaves an old copy of a draft behind, the two copies share a Message-ID. The Drafts row then
  stands for a two-message conversation (`convoRow`), which opens in the thread reader. That reader
  offers Reply and Forward but no Edit, so the draft can't be reopened from the list. In Drafts,
  open the newest copy in the single-message reader. Pinned by
  `test_a_draft_with_an_older_copy_still_opens_for_editing` (tests/browser).

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
  mail, and add an on-demand header-only IMAP fetch for the backlog. The same gap breaks a retry:
  once a cleanup's sweep has moved all the sender's mail, the rows it read from are gone, so
  running the cleanup again after a failed send finds `no_method`.
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
  indexed. `server.backfill_round` re-downloads it every round under the IMAP gate, and twelve
  such messages among the newest unfetched bodies starve everything older. From is safe, since ingest
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
- **Consistency sweep: buttons, empty-state strings, toast vs. persistent** · low value · effort M —
  Three concrete, checkable changes rather than a vibe: (1) one `.btn-retry` replacing `.ai-rerun`,
  `.frail-retry`, `.ob-retry` and `.do-confirm`; (2) one `EMPTY` string table replacing the four
  inlined phrasings ("Nothing here." / "No matches." / "Nothing matches." / "No messages in this
  folder."); (3) a stated rule for toast-vs-persistent — **persistent iff the event happened while
  the user was not looking** — then apply it, since snooze-wake toasts today while new mail gets a
  sticky pill.

## Correctness and hardening

- **Keep request text out of log lines** · low value · effort S — `messages_in` prints
  `[folder-sync] {account}/{folder}` with the folder as the query string decoded it, so a name
  holding `%0A` writes extra lines into the server's output. Only the local terminal reads it, but
  a forged line could pass for a real one. Print request-supplied values with `!r`.
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
- **Negation in search** · medium value · effort S — two gaps in `search.build`, each pinned by a
  strict xfail in `tests/test_search_build.py`:
  - a negated FTS term with nothing positive (`-from:lin`) is dropped, so the query matches all
    mail, the named sender's included. Use `m.pk NOT IN (SELECT rowid FROM msg_fts WHERE msg_fts
    MATCH ?)`;
  - `-category:x` builds `s.category <> ?`, which is NULL for mail without a summary, so it hides
    every unsummarized message. Use `s.category IS NOT ?`.
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
- **Flags changed elsewhere: cheaper, and live** · low value · effort M —
  `ingest._refresh_flags` reads the flags of a folder's `FLAG_REFRESH` newest UIDs on every
  incremental sync. With CONDSTORE (`FETCH … CHANGEDSINCE` and a `highest_modseq` kept per
  folder) it could ask only for what changed, and reach older mail too. What it changes isn't
  pushed: the open list shows it on its next load, since `sync-done` redraws only the digest.
- **Prune a sweep's local rows by Message-ID** · low value · effort S — `manage.sweep_account`
  prunes local rows by live UID, where expiry and the AI sweep prune by Message-ID. Across a
  UIDVALIDITY change sync hasn't seen yet, it deletes the wrong rows. The next sync's reset heals
  them.
- **AI spend and cache keys** · medium value · effort M — each pinned by a strict xfail in
  `tests/test_hwf_ai.py`:
  - a cached brief keeps the pks it was built with, so a renumbered folder shrinks its chips
    (`test_bug_a_cached_brief_still_names_its_messages_after_a_renumber`);
  - opening your own reply in Sent kicks a pre-draft that `_gen_draft_ahead`'s GC then drops as
    moot, so every open pays again. Check the anchor's folder in `_draft_eligible`
    (`test_bug_a_thread_that_ends_in_your_own_reply_is_not_pre_drafted`);
  - with From switched to another account, Draft looks the thread up under that account and
    answers 404 (`test_bug_a_draft_survives_switching_the_from_account`);
  - the review's `cleaned` count ignores the account it is scoped to, because `unsub_log` has no
    account column (`test_bug_the_review_counts_only_this_accounts_cleanups`);
  - a message without a thread id is summarized as thread `pk:N`, which matches nothing (404). Sync
    gives every row a thread id, so this path is dead in practice
    (`test_bug_a_message_without_a_thread_id_can_be_summarized`);
  - the brief's `_clean` keeps the model's group `count` unclamped, so a chip can claim more
    messages than its pks hold.
- **Threads, Reply Later and the classifier: smaller bugs** · low value · effort S — each pinned by a
  strict xfail in `tests/test_hwf_domain.py`:
  - `store.list_replylater` has no state filter, so a dismissed thread stays in the stack
    (`test_hwf_a_dismissed_reply_later_thread_leaves_the_stack`);
  - the ESP marker `"sg."` matches by substring, so `msg.` and `nsg.` domains count as bulk
    (`test_hwf_a_domain_merely_containing_an_esp_marker_is_not_bulk`);
  - the Screener's decided-domain check is exact, so a rule for a parent domain doesn't cover its
    sub-domains (`test_hwf_the_screener_skips_senders_a_parent_domain_rule_decided`).
    `server.cleanup_senders` repeats the same check;
  - `manage.plan_account_db`, behind `/api/stats`, reads `list_unsub_raw`, which is filled only at
    preview time, so mail that is junk only by its List headers counts as kept
    (`test_hwf_the_stored_inbox_plan_counts_the_junk_ingest_stored`);
  - a full re-scan of a folder blanks the snippets that fetched bodies filled, and INBOX snippets
    taken at sync keep invisible preheader padding, because `_html_to_snippet_text` never strips
    what it unescapes (`test_hwf_a_full_rescan_keeps_the_snippet_of_an_opened_message`,
    `test_hwf_an_inbox_snippet_drops_invisible_preheader_padding`);
  - `server.backfill_round` retries the same 12 newest bodies forever when they can't be
    fetched (deleted elsewhere, say), and never reaches older mail
    (`test_hwf_backfill_moves_past_bodies_it_cannot_fetch`).
- **A JSON body that isn't an object** · low value · effort S — a POST whose body is a JSON array
  answers 500 instead of 400 "bad request". Pinned by
  `test_a_json_body_that_isnt_an_object_is_a_bad_request`.

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
   is written, reviewed and dormant behind an unset env var. Follow it immediately with **give
   each account its own IMAP gate**, the structural half.
4. **Real listbox semantics for the message list** (high / M) — the biggest remaining accessibility
   gap, and the message list is the one surface a screen reader cannot use today.
