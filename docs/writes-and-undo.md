# Writes, undo, and offline

- A change a person asks of the mail takes one path, online or offline: `mailbox.commands` (`MailCommands`), over
  `mail_commands` (migration 39). **Accept**, in one transaction: the command's row, and for a move the message's row marked
  `pending_removal` with its id and the undo entry, which names it. Every list, search, the conversation view, the
  brief, the delta and what drafting ahead picks from leave a marked row out (`AND m.pending_removal IS NULL`); a new list query does too. What the AI
  reads of a whole thread doesn't, since its fingerprint would change twice over one move and pay for a summary each time. A
  flag change shows on the local row instead, and `params` keeps the flags it replaced (`was`). **Submit**: a try as a job
  on the account's gate, which a click waits for only `FILE_WAIT` seconds, and for each of the server's answers only `TRY_TIMEOUT`. Past that the worker has it, and the answer's
  `status` is `waiting`. **Settle**, under the same gate and in one transaction: `applied`, where the message went and what
  the server did on the way (`outcome`), and the local rows (`_local_mirror`).
- A connection failure raises a member of `commands.UNREACHABLE`: the worker has the command, and the answer's `status` is
  `queued`. A refusal in a click (`ServerAnswer.won_t`: `refused` or `no_folder`) leaves nothing behind: whoever clicked
  hears of it, the message is as it was, and the undo entry and the row go. On the worker nobody is waiting, so a
  refusal, a give-up or a removed account puts the row in `error`, for the tray, and gives the message back
  (`_give_back`). Undo of a move not yet applied gives it back too.
- `begun` says a try may have reached the server. A first try acts on the UID seen when the command was accepted, if the
  folder's UIDVALIDITY (`params.epoch`) still holds, and searches for nothing. A later try asks the server first
  (`ServerOps._target`, `_already_in`): the Message-ID says where the message is, a folder with none by that id means it
  has left, and a UID from another epoch is never acted on. A send's `\Answered` (`server_ops.mark_answered`) goes
  straight to the server, since the outbox retries it.
- `mailbox.commands` runs the worker when IMAP returns, using the `applied` flag as an
  exactly-once gate. It and Compose's `Outbox` each run a `DurableQueue`, `QUEUE`, over their table:
  one claim wins a row, a failure backs off from 20 s doubling to 15 minutes, and the last allowed attempt goes to `error`,
  where a person's retry (`requeue`) starts the count again: the outbox's fifth, a command's hundred-and-first, about a
  day of tries. `/api/pending` lists the command log's unapplied rows for the panel the outbox button opens; its Retry
  (`commands.retry`) marks the message or shows the flags again, and its Discard (`commands.discard`, on the account's gate)
  gives them back and takes the entry off the undo stack (`undo.forget`). The `forget` job removes rows finished a
  month ago (`store.command_forget_finished`). The worker wakes
  when kicked and when the next queued row comes due (`until_due`), at least every 30 s. The
  outbox publishes `Failed` for an attempt that failed, as it does `Sent`. A send the
  server refuses for good (`SmtpTransport.permanent`: a 5xx, or every recipient refused with one) goes
  to `error` on its first try (`give_up`). The gate column (`applied`, `delivered`) is set the instant the one
  irreversible step returns, so the boot-time reset of running rows never repeats it, and for a command
  before the IMAP gate opens, so an Undo or Discard waiting on it finds it set. A row's
  state changes go through `QUEUE`, not the store. The move is itself an IMAP op, so it runs
  as a job on the account's IMAP gate; the outbox's SMTP send holds none.
- The outbox's queue has a lane for each account (`DurableQueue(lane="account")`): its drain
  runs each account's rows in turn on a thread of their own. The worker's drain doesn't wait
  for them; any other caller's (a test's `outbox.drain()`) waits for every lane it ran.
- `SmtpTransport.send` tells apart the two sides of a message's last bytes. `before_dot` sets `outbox.wire_begun`
  (migration 32) just before they go, and with no answer after them it raises `Unanswered`: the row goes
  to `error` as `outbox_item.UNSURE`, listed `unsure`, and only a person's retry sends it again. Any answer clears the
  mark, and so does a failure before them. `DurableQueue(unsure=...)` makes `reset_stuck` do the same for a
  row a crash left with the mark set.
- `outbox.wait_kind` (migration 41) says what a queued send waits for: `grace` (Undo Send's window), `scheduled` (a time
  someone picked) or `backoff` (a try failed). The list says `scheduled` or `retrying` from it, never from how far off the
  row is due, and a retrying row offers Try now and Cancel.
- What a delivered send still owes is each its own flag: its Sent copy, `\\Answered`, its draft's copy. One that fails
  (a connection lost, not a refusal, which would answer the same again) puts the row back in the queue, its tries counted
  from the delivery, and the next pass does only what's owed, until it is done or goes to `error`. The retry looks for the
  copy by Message-ID first (`Copies.append_to_sent`). `outbox.list_recent` shows such a row as `sent`, or as `error` once a
  Sent copy gives up (what else gives up asks nothing of anyone, so that row stays `sent`), and `cancel_queued` never
  deletes a row past its gate.
- On an account whose server files sent mail itself, a person's Retry of an `unsure` send looks in Sent first
  (`Outbox._in_sent_already`, on the gate for `LOOK_IN_SENT_WAIT`). A copy there marks it delivered, only its bookkeeping
  is left, and the answer says `gone: true`. A look that can't tell sends it again, as they were told it might.
- A message MailMind sends for itself goes through the outbox too: a mailto unsubscribe (`SystemSend`), keyed
  `unsub:<sender>:<account>`, waits out a server that's away and is filed in Sent.
- The read-only profile writes nothing to a mail server or anywhere else outside (decision D4).
  A session opens every mailbox with EXAMINE and raises `profile.ReadOnlyError` on any write,
  before it is sent: `ReadOnlySession` over IMAPClient, and over imaplib `accounts.ReadOnlyIMAP`,
  which refuses any command not known to only read. `SmtpTransport.connect` and `safe_http.one_click_post` refuse too. `ReadOnlyError` is a
  member of `commands.UNREACHABLE`, so a move or a flag change queues as it does offline, and
  both queues' drains wait for a full run. What nothing queues (a sweep) answers 409 through
  `shared.failure_answer`. A new write path either queues or calls `profile.refuse_writes` first.
- A server NO or BAD means the message didn't move. A session raises it as `IMAP4.error`,
  `_imap_mutate` returns `(False, None)`, and trash, archive and junk answer a 409 (`Conflict`)
  and keep the local row. `set_flags` does the same for a star or a read mark. Never send a bulk
  `EXPUNGE`: `MailSession.expunge(uids)` removes the UIDs named and no others, and
  `move_by_copy` flags each original only after its COPY succeeded.
- The queue's idempotency key carries the request's `mutation_id`, or a random one when the
  client sends none, so only a resubmit of the same request dedupes.
- `ingest.ingest_message` (undo's one-message re-ingest) never moves the sync watermark or
  creates a folder's `sync_state` row; only a sync that saw every UID may.
- A folder counts as synced (`ingest.folder_synced`) only when its `scan_state` (migration 24)
  is `synced`: a whole scan sets it, one cut to warming's newest messages sets `recent`, and an
  incremental sync, a failed scan or a one-message re-ingest leaves it be. Anything else, the
  first open scans the folder whole, as a job of its own (`mailbox.folders._folder_sync`, one per folder at
  a time) that the list request waits for up to `FIRST_SYNC_WAIT` seconds. Past that it answers
  with what is stored and `syncing: true`; a scan that failed answers `sync_failed: true`.
  Opening a folder already read, other than INBOX, catches it up the same way, an incremental
  sync waited for up to `CATCH_UP_WAIT` seconds (`catching_up: true` past that), at most once
  every `CATCH_UP_EVERY` seconds unless `mailbox.folders.touched` says MailMind put mail in it.
  Each sync ends by publishing `folder-synced` with how many it stored.
- What a folder's sync asks the server for is `domain.sync_plan`, a pure value from what the select said and what is
  stored: `plan_folder` the mail to fetch, `plan_flags` how to read flags. `Ingest` does the asking and the storing.
- An incremental sync reads flags again (`ingest._refresh_flags`). On a CONDSTORE server it compares
  the select's HIGHESTMODSEQ with the mark in `sync_state.highest_modseq` (migration 31): nothing if they
  match, else what `uids_changed_since` names, however old. A server without it (Proton Bridge), or one
  refusing the search, has its `FLAG_REFRESH` newest stored UIDs read instead. It reads the stored rows
  before the flags and writes each change only where the row still holds the flags it read (a click may
  land meanwhile, since the sync takes no IMAP gate), skipping rows with a flag change still queued or a
  move pending. The mark stays behind those two and a refused search, so what was skipped is read again.
  A sync that changed any publishes `FlagsChanged`, and the page reloads an open list on
  `flags-changed`.
- The body backfill (`syncing.backfill_round`) goes by `domain.body_backfill`. Each account takes its turn at a round's
  dozen, so no account's backlog, outage or busy gate holds another's mail back. A busy gate ends only that account's
  turn. A lost connection sets its message aside, a minute doubling up to six hours, and rests only its account for a
  minute. A body the server didn't give waits six hours. That is kept in memory, so a restart tries everything again.
- Undo is a LIFO stack in the database (`undo_entries`, migration 25; `mailmind.platform.undo`; `undo_group` reaches one group wherever it stands, for Tidy's review strip),
  so it outlives a restart (decision D3). It holds 50 groups
  (`Undo.UNDO_MAX`, an action alone counts as one) or 24 hours of them (`undo.KEEP_FOR`), whichever comes first, and an entry leaves it, committed, before
  any server is asked to reverse it. It knows nothing of what an entry means: `mailmind.bootstrap.mailbox` hands it a reverser for each kind, the Mailbox's in `application.reversal` (`Reversals`). Bulk actions push one grouped entry with children. `mailbox.undo.undo_last` tries every child, newest first; the ones it
  couldn't reverse (a refusal, not found, a raise) go back on the stack as a smaller group, and
  the answer counts `reversed` and `failed`. When none went back it raises `Conflict`, and with nothing to undo `NotFound`
  (`empty`). A reversal reports a server NO as `(False, why)`
  (`server_ops.move_by_msgid`, `relabel_by_msgid`), never as done. A group id must be unique for the database's
  life: the frontend's `undoGroup()` is a UUID, since the stack outlives a page load and a restart.
- A bulk command's confirm carries out a plan (`mailmind.assistant.application.plans`,
  `/api/ai/do-plan` → `/api/ai/do-confirm`). The preview freezes what it matched, by message key and folder, and
  the page sends back only the plan's id, so a renumber or mail that came later changes nothing. A plan is
  good for one confirm within ten minutes, and a restart ends it (410). A copy no longer in the folder it was seen
  in is left where it went, and counted as `gone`. `Filing.carry_out` sends each message through its own route (`filing.do_write`, `do_trash`,
  `attention.snoozing.snooze`), under one undo group. A filter reaches Sent, Drafts, Trash and Junk only by naming a folder, a verb
  never reaches its own home (`action_plan.out_of_reach`), and each message counts once, by the copy search would show.
- Whole-conversation archive, trash and move (`/api/thread/archive|trash|move` → `filing.do_thread_file`) resolves
  the members server-side — the anchor's `thread_id` within the anchor's folder, or the anchor alone
  with no thread id — and accepts them all in one transaction (`commands.accept_move_within`, which a
  caller holding a write uses), under one undo group, so a crash can't leave half of them marked. Each
  is then tried on its own once that commits: a refusal gives back only its own message, and a try
  that raises counts as queued, with the rest still tried.
- Marked rows go as one request (`/api/bulk/file` and `/api/bulk/move` → `filing.file_many`, up to `BULK_MAX`; each client sends more as more requests
  under one undo group): one transaction accepts every row, a `whole` one as its conversation, and then each is tried on its
  own. A click on many waits about as long as on one, there and for a conversation: past `FILE_WAIT` the rest go to the worker
  (`commands.leave_to_worker`), and every row has left the lists by then. A snooze of marked rows goes the same way, up to
  `snooze.BULK_CAP` copies a request. One naming more is the `bad request`, never cut short, since the page has already taken
  every marked row off the list.
- Every account's Inbox is one list and one query (`/api/inboxes`, `queries.all_inboxes`), read from what is stored: an account
  on its first scan starts one and holds up no one (`syncing: true`). So is who you wait on (`/api/waiting-on-others`).
- Send-time checks: the composer's `send` (`features/compose/machine.ts`) runs `runChecks` synchronously, before `close(true)`,
  so nothing is awaited before the composer closes and a double submit still can't happen.
  `/api/contacts/known` (the "new" marks) is a read-only POST: pure SQL, no IMAP gate.
  `Sending.send` refuses a `send_after` that has passed instead of sending now.
- A draft is local first (decision D5, migration 40). `Drafts.save` writes a save to `draft_journal`, one row a draft
  (the newest replaces the last), and that alone makes it saved, whatever the server is doing. The row is what the draft
  still asks of its server: a save's bytes and record, or `discard`. An autosave (`autosave: true`) comes due a minute after
  the last push (`PUSH_EVERY`). Any other save tries at once, on the account's gate for `PUSH_WAIT`, and answers
  `pushed: true`, or else `pushed: false` and the `draft-push` job (`Drafts.push_round`) has it, backing off from 20 s to 15
  minutes for as long as it takes. A push that lands deletes the row only if no newer save has replaced it. Read-only the job
  doesn't run, so saves and discards wait for a full run.
- A try sets `tried` to its Message-ID before the APPEND. The next try looks for that copy in Drafts: it stands if it is
  this very save, and goes if a newer one has taken over. A discard takes it too, and the row leaves only after the copy
  has (a server that refuses has the last word: the row goes, and the copy stays listed).
- A save names the `version` it saw (`base_version`). One that never saw the latest gets a 409 with `conflict: true`, unless
  the last save was this composer's (`saved_by`), whose answer was lost. Autosave then stops, a closing save fails into
  `failedSend`, and only a save someone asks for leaves `base_version` out and overwrites.
- A send about to go holds its draft: the job leaves it, a save answers 423, and delivery removes the copy and the row, the
  journal with it. One scheduled more than a minute off lets the copy catch up.
- Reopening prefers the journal when the copy opened is the one pushed. Where another app changed it, `Drafts._keep_apart`
  gives the waiting save a row of its own, so both stay. `/api/outbox` also answers `drafts` (`Drafts.waiting`), what should
  have reached its server and hasn't, for the tray, and `/api/draft/resume` opens one by `draft_id`.
- Reopening a draft (`/api/draft/resume` → `Drafts.resume`) reads the draft's own bytes, never
  the original. At each push `Drafts._push` records beside the `drafts` row what the composer held
  (migration 20: `own_text`, `tail_text`, `tail_html`, `bcc`, `raw_sha`). That record is
  trusted only for the very copy it describes, the same UID or the same sha256. The composer
  sends `resume` (threading and the quote's text) on every save and send. `Composing.spec`
  then builds `own + tail`, with HTML only where the journal or the row vouches for that exact tail (`book.draft_quote`). The
  original is looked up by `(account, Message-ID)` for `\Answered` only. `Drafts._live`, the
  "is this copy still there" probe, is a stateless 10-second connection outside the IMAP gate.
  Under the gate `Drafts.resume` re-reads the `drafts` row and answers 409 if it is gone or moved,
  so `Drafts._push` and the outbox's draft cleanup also move that row's copy only under the gate. Edit
  answers 423 while a queued or sending outbox row holds the draft. A save appends the new copy
  first and only then removes the old one (`Copies.append_to_drafts`), wherever it was, so one that
  fails or a crash leaves the old copy, at worst beside the new.
- A message is a draft if the server flags it `\Draft` or its folder is the account's Drafts
  (`Drafts.is_draft`): the role the last LIST gave it, else `roles.names_drafts`, the whole name
  at the top of the tree or under INBOX or Gmail's parent. A name that merely holds "draft" counts
  for nothing, and a removal has no Undo, so Discard, a Send and a replacing save remove a copy only
  if MailMind saved it, the server flags it or its folder is Drafts (`Outbox.draft_copy_is_ours`).
  Older `drafts` rows may name received mail: those are forgotten and the mail stays.
- A reply or forward names its original by key (`original_key`) until its first save, whose answer carries `resume`
  (threading, the quote) and a forward's `files`; the composer then works from those, as a reopened
  draft does, and needs no row. The outbox row keeps the original's account, Message-ID and thread
  (`orig_*`, migration 27) and its key (`original_key`), so `\Answered` (`server_ops.mark_answered`) and the follow-up land after an
  archive deleted the row.
- A copy that leaves the server takes its `messages` row with it, in the step that records the save,
  discard or send, and `reconcile` names the folder so a list of it on screen loads again (its
  catch-up brings the new copy). A copy the server wouldn't remove stays listed.
- A composer's saves go in turn. `save` (`features/compose/machine.ts`) chains on `x.saving` and names the copy the
  last save left, autosave skips a tick while one is in flight, and Send and Discard wait for it.
  It clears `dirty` only if `x.edits` hasn't moved since it began, so call `markEdited(x)` at
  every edit, never `x.dirty = true`. A closing save that fails goes to `failedSend`, with the
  float's Edit. On the server a first
  save finds an earlier one's row by the composer's key (`drafts.idempotency_key`, migration 26),
  as `Sending.send` does when it has no `draft_id`.
- A composer replaced after an `await` (Undo Send, a failed send's Edit → `makeRoom`) must
  close and open in the same task. In the gap `lib/backTrap.ts` would give back the tab's extra history
  entry with `history.back()`, a traversal is asynchronous, and a `pushState` lands before it does and is undone.
- **`pk` is not stable.** Reconcile can renumber rows, so anything durable keys on
  `(account, message_id)` — see `store.get_message_by_msgid`. A stored AI answer is checked
  against `mailmind.assistant.domain.fingerprint`, which names messages by Message-ID: a thread
  summary, a reply drafted ahead, the brief, the delta and the review all use it. Migration 22
  moved stored thread summaries onto it. A stored brief and a stored review name their messages by
  key and folder (below), and a read gives the copies they have now (`store.copy_now`).
  No column names a message by pk (migration 45 ended the last four): a send goes by `orig_*`, and with no Message-ID to go by by
  `original_key` (migration 44), a draft by its own In-Reply-To, a reply drafted ahead and a follow-up by thread or Message-ID.
  `in_reply_to_pk` is the pk a send's `original_key` gives as it comes, which `Composing` and `Outbox.enqueue` find the original by.
- **The API names no pk.** A request names a message by its key and a copy by its key, folder and uid, and so does every
  answer: a list row, the reader, filing's `filed`, a thread's `reply_key`, the brief, the review, Ask's sources, the Document
  Hub, an invite and `draft-ready`. The uid tells apart two copies in one folder under one key, which a sender who reuses a
  Message-ID makes. `mailmind.http.named` looks up the pk the row has as the request comes. A route that acts on a copy (filing,
  a flag, the reader's body, frame and attachment, a bulk row, a snooze's `copies`, a block's trash, a draft reopened) takes the
  row with that key, folder and uid and no other. With no uid it takes the only copy in that folder, and of two neither
  (`store.copy_pk`). A uid from before a new UIDVALIDITY names nothing, even where one copy is left. One that acts on the
  message (a summary, a reply drafted now or ahead, an RSVP, a send's `original_key`, mute, reply later, a follow-up's
  dismissal) takes the copy the uid names where it is there. Failing that, its copy in that folder, else another, a listed one
  first, then the newer (`store.message_pk`). One that isn't there is a 404, or for drafting ahead `none`, and nothing is done.
  The page always sends the uid, and its URL names what is open the same way (`?key=<key>&folder=<folder>&uid=<uid>`, with
  `&c=<thread>&ca=<account>` for a conversation). So a kept link opens the copy it named or nothing, never the one beside it,
  and a link with no uid or an old `?m=<pk>` opens nothing.
- **A message's key outlives its rows.** `store.mint_key` gives one key per account and Message-ID
  (`mailmind.mailbox.domain.identity`; a message with none goes by its envelope), and
  `messages.message_key` names it on each copy (migration 30). `store.save_summary` writes what the AI
  said under the key (`assessments`), and every query joins that on `message_key`, so a move, an Undo, a
  renumber or Gmail's All Mail copy pays for no second call. `needs_summary` offers one copy of each, and no
  row without a key. A key with no copy left waits 30 days (`gone_at`) for one to come back, then the `forget` job ends it.
  `store.init` (`store.repair_keys`) gives a key to a row an older commit wrote. Message rows in the API carry the key as `key`,
  and the copy's `folder` and `uid`. New durable state about a message keys on it. Migration 45 ended `summaries`, a row for each copy, and
  `messages.snoozed_until`, first bringing across what a run of an older commit wrote only there.
- **A snooze outlives its row.** `attention.book.set_snooze` writes a `snoozes` row (migration 33, by key and
  folder), and every query asks that (`kernel.snoozed`: `not_snoozed_sql`, `snoozed_sql`), so a resync keeps it,
  each copy in that folder is hidden and a copy filed elsewhere isn't. A row read alone says `snooze_until`
  (`store.get_message`), and the Snoozed view's row says `snoozed_until`, an alias, which its answer carries.
  An Undo of filing brings it back; one come due with
  no copy in its folder ends at `attention.book.due_snoozes`. The undo entry names rows by key and folder too (`where`).
  A message that wakes is unread on the server as well: `Snoozing.wake_due` queues the mark for read mail
  (`store.queue_flags`), or the next sync that read its flags would take it back.
- **Muting tells the server too** (decision D6). `Muting.mute` queues `\\Seen` for the thread's
  unread members and `Muting.on_ingest` for mail that arrives later, skipping one with a mark still open
  (a refused one waits in the tray). The undo entry names those members by key and folder, and Undo marks
  unread again only them.
- **A thread id is sticky.** A sync hands `store.thread_stored` what it stored, and any of the account's mail with no
  id yet goes along (a sync that died, `mailmind sync`). `threading.Threader` (`mailbox/domain`) mints an `rf:` id from the
  union-find root of what arrived for the thread (the smallest Message-ID it names) once, and keeps it as the thread grows;
  a Gmail thread id gives `gm:` instead. `thread_links` (migration 42) holds each id a threaded copy names, and goes with
  the copy, so a sync reads what its mail names and the threads that touches, and a reply writes only its own row. An id
  goes only when a merge absorbs it (two threads joined, or one that gained a Gmail id). The survivor is a `gm:` id over an
  `rf:` one, the smallest of several `rf:` ones, and of two Gmail threads joined by headers the one that comes later by
  folder, date and row. Every row holding the absorbed id takes the survivor, and `ConversationsMerged` goes to whoever
  keeps something under it, on the merge's connection, so a failure rolls the merge back: `muted`, `followups`,
  `thread_summaries`, `draft_ahead`'s `t:` keys and `outbox.orig_thread_id`. It isn't on the bus, whose handlers run after
  the commit. A new table keyed on a thread id gets a `rekey_thread` in its owner's adapter, called from
  `_conversations_merged` in `mailmind.bootstrap.mailbox`. A split doesn't split: mail left unconnected by a copy going, or
  by one stored again naming something else, keeps its one id. `store.recompute_threads` regroups a whole account and is
  the repair (`python3 -m mailmind db rethread`), which parts what no longer reaches a Gmail copy; no sync takes it.
