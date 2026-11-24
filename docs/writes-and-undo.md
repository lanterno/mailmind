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
  straight to the server, since the outbox retries it. Once it is stored the lists hear `flags-changed` for the original's
  folder, and the account's follow-ups are resolved at once (`FollowUps.resolve`), so Waiting lets the thread go with a
  `followup` event rather than at the next minute's round.
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
- A queued send goes only while MailMind is open. The Mac app, asked to quit (`Quitting.settle`), first saves each open
  composer's draft on this Mac as an autosave does (`Composer.saveBeforeQuitting`: the core answers once the journal
  holds it, and the push job sends the server's copy once MailMind is open again; a read-only run keeps it here),
  waiting up to 5 seconds and asking before it quits with one that was refused (it is lost) or hasn't answered yet (it
  may be); then it sends a row in its
  Undo window at once (`/api/outbox/sendnow`) and waits up to 30 seconds for it to go; a `scheduled` or `retrying` row
  is asked about first ("Quit anyway? It sends when MailMind is next open"), and Don't Quit leaves every row as it was.
- What a delivered send still owes is each its own flag: its Sent copy, `\\Answered`, its draft's copy. One that fails
  (a connection lost, not a refusal, which would answer the same again) puts the row back in the queue, its tries counted
  from the delivery, and the next pass does only what's owed, until it is done or goes to `error`. The retry looks for the
  copy by Message-ID first (`Copies.append_to_sent`). `outbox.list_recent` shows such a row as `sent`, or as `error` once a
  Sent copy gives up (what else gives up asks nothing of anyone, so that row stays `sent`), and `cancel_queued` never
  deletes a row past its gate.
- On an account whose server files sent mail itself, a person's Retry of an `unsure` send looks in Sent first
  (`Outbox._in_sent_already`, on the gate for `LOOK_IN_SENT_WAIT`). A copy there marks it delivered, only its bookkeeping
  is left, and the answer says `gone: true`. A look that can't tell sends it again, as they were told it might.
- A message goes out dated as it leaves: before each try on the wire the outbox restamps its own Date header
  (`builder.restamped`) and keeps the bytes, so the copy in Sent is the same and its Message-ID stays. A send scheduled
  for later, or one that waited out its server, no longer goes dated as it was written.
- A message MailMind sends for itself goes through the outbox too: a mailto unsubscribe (`SystemSend`), keyed
  `unsub:<sender>:<account>`, waits out a server that's away and is filed in Sent.
- The read-only profile writes nothing to a mail server or anywhere else outside (decision D4); what AI is asked still
  goes to its provider, though no background job asks it anything; the brief and the since-you-looked line a client
  asks for still go.
  A session opens every mailbox with EXAMINE and raises `profile.ReadOnlyError` on any write,
  before it is sent: `ReadOnlySession` over IMAPClient, over imaplib `accounts.ReadOnlyIMAP`,
  which refuses any command not known to only read, and the core's `mm_mailbox::session::ReadOnly`, whose refusal
  counts as unreachable (`Unreachable("ReadOnlyError")`), as `ReadOnlyError` does in `commands.UNREACHABLE`.
  `SmtpTransport.connect` and `safe_http.one_click_post` refuse too, as do the core's `SmtpTransport::send`
  (`compose_wiring`) and `OneClickHttp::one_click_post` (`cleanup_wiring`), and the core's outbox refuses before a row
  is written (`Delivery::enqueue`).
  Above them, a change a person asks for that would reach a server is refused before anything is queued or hidden
  here, so nothing waits for a full run: a move or a flag change and the tray's Retry (`MailCommands`' accepts and
  `retry`), a send, a reply, Send later, an invite's answer or an unsubscribe by mail (the outbox's `enqueue`, and its
  Retry and Send now), a draft's discard that would take its copy off a server, a mute, a snooze (its waking marks the
  mail unread on the server), a cleanup, a sweep, a folder made, renamed or deleted, and a plan the Assistant froze.
  Each answers 409 `/problems/read-only` ("MailMind is running read-only", `read_only: true`): `shared.failure_answer`
  on the Python server, and `answer::failure` on the Rust one, from `mm_kernel::errors::READ_ONLY`. What only changes
  this Mac still works: a draft saved here, a rule (a block's trash of the message it names is skipped), reading, AI.
  What a full run queued waits for the next: neither queue drains read-only, and a try that got as far as a server
  would meet `ReadOnlyError`, a member of `commands.UNREACHABLE`, and stay queued. Nor does what a sync or a job does on
  its own queue anything: a person's filters ask nothing of new mail (`Ingest._apply_filters`, the core's
  `Ingest::reading_only`), mail arriving in a muted thread is marked read here alone (`Muting.on_ingest`, the core's
  `SyncStore::settle` with `queue` false), and the snooze job wakes mail unread here alone (`Snoozing.wake_due`, the
  core's `AttentionBook::wake_due` with `queue` false). The next read of the server's flags takes those marks back. An
  Undo that would ask a server (a move or a Gmail archive a full run applied, a sweep's way back, a mute that marked
  mail read) is refused with the same 409 before any server is asked, and stays on the stack for a full run; one that
  changes only this Mac (a move still waiting, which is cancelled, a snooze, a block) goes back as ever. A draft saved
  read-only is kept on this Mac until it is saved again in a full run (below). A new write path calls
  `profile.refuse_writes` first.
- A server NO or BAD means the message didn't move. A session raises it as `IMAP4.error`,
  `_imap_mutate` returns `(False, None)`, and trash, archive and junk answer a 409 (`Conflict`)
  and keep the local row. `set_flags` does the same for a star or a read mark. Never send a bulk
  `EXPUNGE`: `MailSession.expunge(uids)` removes the UIDs named and no others, and
  `move_by_copy` flags each original only after its COPY succeeded.
- A folder a person makes, renames or deletes (`/api/folders/create`, `rename` and `delete`; the Rust server's
  `FolderChanges` in `mm-mailbox`) takes the same path, `MailCommands.change_folder`, as a job on the account's gate that a
  read-only process refuses before anything is sent. Nothing queues and nothing is undone: a person waits to see the
  folder, and a change tried later could meet a folder changed since. Its name goes as the server's LIST gives it, through
  `mailbox_arg`: a new folder where the account keeps a person's own (under Proton Bridge's `Folders/`, the one place
  Bridge makes one, or under `INBOX.` on a server that keeps every folder there), and a rename keeps the folder under its
  parent. A rename to the name a folder has, as a person reads it (`own_name`: LIST's modified UTF-7 and escapes read),
  sends nothing. The folders that play a part (the Inbox, Sent, Drafts, Archive, All Mail, Junk and Trash), one at the
  top named as one of them and the account's own Trash are never renamed or deleted, nor is a folder the server keeps:
  one with any SPECIAL-USE attribute (Gmail's Starred, `\Flagged`, and Important among them) or one in Gmail's
  `[Gmail]` tree. Nor is one with folders inside it deleted (`domain.folder_change`). `/api/folders` says of each folder
  whether a person may change it (`changeable`) and its `own_name`, so the Mac app's rail offers its menu only there. A
  folder that holds mail answers a 409 with how many (`messages`) and whether they go to Trash (`to_trash`) until the
  person confirms (`confirm`); then its mail moves by UID first to the Trash the server lists (`roles.trash`, as a
  trash and a sweep find it), and the folder is deleted only if none is left. On Gmail a folder is a label, so nothing
  moves: the DELETE goes alone and the mail stays in All Mail, as Gmail's
  own delete does, since a move to Gmail's Trash would take each message out of the Inbox and every other label. A
  delete that stops once mail has gone says so: the server keeping the folder is a 409 saying how many went
  (`trashed`), a lost connection says some may be in Trash already, and either way the folder's rows go so it is read
  again. A rename's rows follow it in the database, and a deleted folder's go.
- The queue's idempotency key carries the request's `mutation_id`, or a random one when the
  client sends none, so only a resubmit of the same request dedupes.
- `ingest.ingest_message` (undo's one-message re-ingest) never moves the sync watermark or
  creates a folder's `sync_state` row; only a sync that saw every UID may.
- On the Python server a folder counts as synced (`ingest.folder_synced`) only when its `scan_state` (migration 24)
  is `synced`: a whole scan sets it, one cut to warming's newest messages sets `recent`, and an
  incremental sync, a failed scan or a one-message re-ingest leaves it be. Anything else, the
  first open scans the folder whole, as a job of its own (`mailbox.folders._folder_sync`, one per folder at
  a time). On the Rust server a folder's first scan has begun (`StoredLists::scan_begun`) once its `scan_state` is
  `synced` or `down-to:<uid>` (`Scan::begun`): the first open stores its newest step and leaves the rest to the `older-mail`
  lane (below), which it sets going at once, and every later open is a catch-up. A folder no scan has begun is read as a
  first scan whatever its watermark (`sync_plan::plan_folder`), so one a quit left with a watermark and `never` begins
  its scan at its next sync, the Inbox's background sync too. A sync as a folder opens that panics ends as one that
  failed, so the folder's next open syncs again rather than join it. The list request waits for a first scan up to `FIRST_SYNC_WAIT`
  seconds (`Waits::first_scan`). Past that it answers
  with what is stored and `syncing: true`; a scan that failed answers `sync_failed: true`.
  Opening a folder already read, other than INBOX, catches it up the same way, an incremental
  sync waited for up to `CATCH_UP_WAIT` seconds (`catching_up: true` past that), at most once
  every `CATCH_UP_EVERY` seconds unless `mailbox.folders.touched` says MailMind put mail in it.
  Each sync ends by publishing `folder-synced` with how many it stored.
- An Inbox's first scan stores mail that was there before, so on both servers it is no new mail: the sync counts
  none ("0 new") and publishes no `new-mail`, so no badge or notification follows, only `sync-done`. So a list open as
  it runs doesn't wait for `new-mail`: the page reads its lists again on a `sync-done` that stored any mail (`kept`
  or `junk`), and the Mac app on every `sync-done`; both on each `folder-synced` that read its folder.
- On the Rust server a first scan reads newest first, in steps of `sync_plan::CHUNK` (500). The sync stores the newest
  step and leaves `scan_state` at `down-to:<uid>`, the lowest UID it stored; the `older-mail` lane reads on below it
  (`Ingest::read_older`), a step at a time, each step's messages, its watermark and its new `scan_state` committed in
  one transaction (`SyncStore::store_step`), so a quit goes on from the last step stored. A step names the
  `scan_state` it expects (`holds`), and a sync that read the folder afresh meanwhile makes it store nothing. Its
  last step leaves `synced`. While it reads, `/api/digest`'s `status` gives the account `done` and `total`. An Inbox
  message's text comes from its first 64 KiB (`MailServer::heads`, `BODY.PEEK[]<0.65536>`), so a first scan
  downloads no attachment; only one whose cut-off opening says it may carry an invite is fetched whole. A FETCH of
  openings the server refuses (Gmail's "Some messages could not be FETCHed") keeps what it sent and asks for the rest
  a message at a time, so a message it can't send costs only its own text. A read of older mail does the same with
  headers and leaves out a message whose headers are still refused, unless the server sends none of the step, which
  then stores nothing and waits for the next round. A connection lost part way stores nothing of its step, which the
  next sync or round reads again.
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
  On the Rust server and in the Mac app (`mm_mailbox::backfill`, `body_backfill` and `fetching`) a round asks for about
  four dozen, of the last twelve months only (`REACH`), a folder's up to twelve in one FETCH of their first 64 KiB with
  each one's whole size, on the account's kept connection. With the gate held it looks at each message again and asks
  for none opened, moved, removed or in a folder renamed since the round found it. An opening that holds the whole
  message (`Opening::is_whole`:
  it ended before 64 KiB, or is exactly the size the server says; a size shorter than what came, as an estimate can be,
  counts it cut off) is kept as its body; one cut off is fetched whole, about 2 MiB a FETCH, where the message is at most
  256 KiB (`WHOLE_MOST`); a larger one keeps only its opening's text (`KeptBodies::keep_text`), which search indexes,
  and its attachments are fetched when it is opened. A batch the connection was lost over sets each of its messages
  aside, and each goes alone from then on. A sync of the Inbox keeps the opening it reads as the body where it holds the
  whole message (`Incoming::body`), with the row in the step's one transaction, so that mail is never asked for again;
  one cut off keeps only its snippet and waits for the backfill, unless it carries an invite, whose calendar needs its
  whole body at once. A draft's opening is read the same way in any folder, so the Drafts list says what each says,
  and so is each message Undo reads back (`ingest_messages`), junk too, so its row shows as it did before it left
  (`sync_plan::reads_text` and `sync_plan.reads_text`).
  The Python server's sync reads the Inbox's mail whole and keeps a message under 64 KiB (`sync.WHOLE_BELOW`), or one
  carrying an invite, the same way.
- Undo is a LIFO stack in the database (`undo_entries`, migration 25; `mailmind.platform.undo`; `undo_group` reaches one group wherever it stands, for Tidy's review strip),
  so it outlives a restart (decision D3). It holds 50 groups
  (`Undo.UNDO_MAX`, an action alone counts as one) or 24 hours of them (`undo.KEEP_FOR`), whichever comes first, and an entry leaves it, committed, before
  any server is asked to reverse it. It knows nothing of what an entry means: `mailmind.bootstrap.mailbox` hands it a reverser for each kind, the Mailbox's in `application.reversal` (`Reversals`). Bulk actions push one grouped entry with children. `mailbox.undo.undo_last` tries every child, newest first; the ones it
  couldn't reverse (a refusal, not found, a raise) go back on the stack as a smaller group, and
  the answer counts `reversed` and `failed`. When none went back it raises `Conflict`, and with nothing to undo `NotFound`
  (`empty`). A reversal reports a server NO as `(False, why)`
  (`server_ops.move_by_msgid`, `relabel_by_msgid`), never as done. A read-only process refuses one that would ask a
  server (`profile.refuse_writes` in `Reversals` and `Muting.reverse`, the core's `READ_ONLY`) before it is tried: an
  entry goes back on the stack and the answer is the read-only 409, and a group keeps those members on the stack and
  answers the 409 only when none of it went back. A group id must be unique for the database's
  life: the frontend's `undoGroup()` is a UUID, since the stack outlives a page load and a restart. A sweep pushes every
  child of its group at one write (`undo.push_many`, the core's `blocks::push_undo_many`), as pushing each in turn would
  leave the stack, and caps itself at 500 a run, since a click waits on its Undo.
- A kind of entry may go back a run at a time: `Undo`'s `together` (the core's `Reverser::together` and
  `reverse_together`) names it, and a group's children of that kind on one account, newest first, go to its reverser at
  one turn of the account's gate, each still counted on its own. A sweep's children are `sweep_back`
  (`Reversals.sweep_back`, `mm_mailbox::reversal`): one session finds each in the Trash it went to by its Message-ID and
  moves them back 200 to a MOVE (`server_ops.move_back_many`), a refused batch one at a time, and one more session reads
  them all in (`Ingest.ingest_messages`), so an Undo of 500 is two logins, not a thousand. One not in the Trash but still
  in the Inbox is back already: a sweep the server stopped part way looks at the Inbox again on a fresh session and
  gives a way back only to what left it, and gives one to all it meant to move only when the server can't be asked.
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
  minutes for as long as a server refuses or is away. The job waits its turn for the gate for `PUSH_WAIT` too, at
  AFTER_USER, ahead of the upkeep waiting but behind a person's own change: one that only asked whenever the gate was
  free met it taken, round after round, and a draft closed reached its server minutes on. A push that lands deletes the row only if no newer save has replaced it. Read-only the job
  doesn't run and a save is kept on this Mac: it asks no server, and its row's due time is `draft_journal.KEPT_HERE`,
  which never comes, so no full run pushes it either. The tray lists it at once, `kept: true`, until a save in a full run
  gives it a due time again, an autosave the one a first save gets. A discard that would take a copy off the server is
  refused (one with no copy there goes at once).
- A draft keeps whatever was typed, as Mail does: a recipient that isn't an address yet never stops a save. The builder
  (`builder.recipients`, `draft=True`) writes each such chip of To and Cc on one line (`one_line`: control characters and
  line separators a space) into a header of MailMind's own, `X-MailMind-Draft-To` and `X-MailMind-Draft-Cc`, which
  another app shows none of; one of Bcc goes only into the save's record, never the bytes. Reopening reads them back
  after the addresses (`resume.recipients`, `resume.bcc_of`). A send is refused at the first, in words naming it as it
  was typed, on one line, cut short past 80 characters.
- A try adds its Message-ID to `tried` before the APPEND (`draft_journal.trying`), keeping every earlier try's, since each
  save has a Message-ID of its own and each failed try may have left its copy. The next try looks for each of them in
  Drafts: the one of this very save stands, and every other goes once a newer copy has taken over. A discard and a send
  take them all too, and the row leaves only after the copies have (a server that refuses has the last word: the row goes,
  and the copy stays listed).
- A save names the `version` it saw (`base_version`). One that never saw the latest gets a 409 with `conflict: true`, unless
  the last save was this composer's (`saved_by`), whose answer was lost. Autosave then stops, a closing save fails into
  `failedSend`, and only a save someone asks for leaves `base_version` out and overwrites.
- A send about to go holds its draft: the job leaves it, a save answers 423, and delivery removes the copy and the row, the
  journal with it. One scheduled more than a minute off lets the copy catch up. Both clients save a Send Later's draft
  first, a save asked for, so its copy waits on its server with the words that go.
- Reopening prefers the journal when the copy opened is the one pushed. Where another app changed it, `Drafts._keep_apart`
  gives the waiting save a row of its own, so both stay. `/api/outbox` also answers `drafts` (`Drafts.waiting`), what should
  have reached its server and hasn't and what a read-only run kept here (`kept`), for the tray, and `/api/draft/resume`
  opens one by `draft_id`: from the journal while a save waits there, else from its copy, which a Drafts not caught up
  since the push lists first (`Drafts._resume_reached`, a full sync of that folder under the gate). A copy gone by then
  answers 404, and its row answers no message any more (`draft_answers_nothing`).
- Reply opens the reply already saved to a message, never a second, empty one. Every save writes the Message-ID its
  In-Reply-To names into `drafts.in_reply_to` (migration 52), which outlives the journal: between a push and Drafts'
  next catch-up nothing else here says what a pushed draft answers. The message as it opens (`/api/message/body`) names
  the newest such draft as `reply_draft` (`Drafts.answering`): MailMind's own by `draft_id`, and another app's, listed in
  Drafts with that In-Reply-To (`thread_links`), by its copy (`in_drafts`), with how many `more` answer it. None a send
  holds or a discard is taking. Both clients already read that answer for the quote, so knowing costs no request. The
  page's `reply` and the Mac's `Composing.reply` wait for a closing save, open that draft through `/api/draft/resume`,
  start afresh on a 404, and on any other refusal say the reply waits in Drafts rather than open an empty one; a quick
  reply's line starts a reply of its own, and a reply written ahead never replaces the saved words.
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
  fails or a crash leaves the old copy, at worst beside the new. In Drafts it removes every copy under the Message-ID the
  draft was saved with too (`UNDELETED HEADER Message-ID`, asked before the APPEND): another app may have left more than
  one there, Thunderbird its earlier saves, and a draft opened from one of them would otherwise stay on the server twice.
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
  catch-up brings the new copy). Every push names its Drafts so, a first push too, which removes none. A copy the
  server wouldn't remove stays listed.
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
  The page always sends the uid, and the place its URL names holds what is open the same way, as does a link typed the
  readable way (`?key=<key>&folder=<folder>&uid=<uid>`, with `&c=<thread>&ca=<account>` for a conversation). So a kept link
  opens the copy it named or nothing, never the one beside it, and a link with no uid or an old `?m=<pk>` opens nothing.
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
  (`store.queue_flags`), or the next sync that read its flags would take it back. A read-only process marks it unread
  here alone and queues nothing (the core's `queue_or_show`), and its next read of the flags takes the mark back.
- **Muting tells the server too** (decision D6). `Muting.mute` queues `\\Seen` for the thread's
  unread members and `Muting.on_ingest` for mail that arrives later, skipping one with a mark still open
  (a refused one waits in the tray). The undo entry names those members by key and folder, and Undo marks
  unread again only them. Read-only, a mute and the Undo of one that marked mail read are refused, and `on_ingest`
  marks what arrives read here alone, queueing nothing.
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
  the repair (`uv run python -m mailmind db rethread`), which parts what no longer reaches a Gmail copy; no sync takes it.
