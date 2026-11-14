# The bounded contexts

See [Architecture](architecture.md) for the layers around them.

- `mailmind.assistant` is the first bounded context: what MailMind asks Claude (see [AI calls](ai.md)).
  `application` holds the ports, the `Gateway` and the `CostGovernor`, `calls` (`Calls`, the way every call goes), `status` (`Status`, how AI stands, and each call's outcome),
  `assessment` (`Assessments`, a message's verdict, its batches and the assess job's round), `context` (`ContextAssembler`, what the model reads of a conversation: the first message and the
  last 19, trimmed and within the caps, with a missing body asked of the `Mail` port at interactive priority), `thread_digest` (`ThreadDigest`), `brief` (`Briefs`), `review` (`Reviews`),
  `reply_draft` (`ReplyDrafts`), `rewrite` (`Rewriter`), `ask` (`Asker`), `asking` (`Asking`, what a request asks of those), `prepared_replies` (`PreparedReplies`, drafting ahead) and
  `extract` (`Extractor`, what a message proposes for a calendar or a to-do list, on a person's click) and `bulk` (`BulkCommands`, a command in words, planned and frozen; `Filing.carry_out` runs its confirm). `domain` holds what it knows about the models and the features, `prompts` (the book of what the model is told, below), `prepared_reply`
  (a reply drafted ahead's rules: its key, what's eligible, when one may be served, the backoff and the three strikes; `reply_worthy` gates what is spent, not what is served),
  `brief`, `review` and `digest` (what is taken from a model's answer, within its caps, and what stands in when it won't do), `citations` (only the ids the model was shown)
  and `action_plan` (what is taken from a plan, and how far a verb may reach), and
  `adapters` the `LanguageModel`s: `AnthropicModel` (the SDK, for the server and every command)
  and `DisabledModel`, and `sqlite` what the AI said (`answers`). `mailmind.bootstrap.assistant` binds them, resolves the key, and is how the routers, the other
  contexts and the tests reach the Assistant (`assistant.calls`, `.status`, `.assessments`, `.context`, `.threads`, `.briefs`, `.reviews`, `.replies`, `.rewriter`, `.asker`, `.asking`, `.drafting`, `.bulk`).
  The mail it reads goes through its `Mail` port and a bulk command's filter through `Search`, both bound there.
- `mailmind.accounts` is the second: the mail accounts and how MailMind reaches each. `mailmind.bootstrap.accounts` binds it, and the app reaches it there: `config`, the `Directory` of the file, `connections`, `health`, `logins`, and `accounts.get(email)`, `accounts.connect(acct)` and the like, the last through `adapters.imap_connection`. `mailmind.accounts.api` names what the other contexts' wiring asks of it (the events, `provider_of`, `TLSPinMismatch`, and `PlaintextSecret` for the entry point).
  `application.ports` holds `AccountRepository`, `SecretStore` and `ConnectionProbe`. `adapters.config_file` is the first, which `application.directory.Directory` reads and writes `mailmind.config.json`
  through. `ConfigFile.read` parses the file again only when its inode, size or modification
  time changed, and hands each caller a copy of its own; `write` replaces it atomically, readable
  by its owner only. `adapters.secrets` is the second, caching an `op` read and a Keychain item's (once for each version
  the file names) only when it succeeds, and keeping a secret in the login Keychain or taking one out (`keep`, `discard`,
  through `mailmind.platform.keychain`). `domain.credential.CredentialRef` is the one reader of the styles the file allows
  (`keychain`, `op`, `env`, the Bridge password). The file never holds a secret itself: a `{"plain": ...}` or a bare
  string reads as `plain` only to be refused, `PlaintextSecret` naming for each the command that keeps it in the Keychain
  (`accounts password <address>`, `ai key`),
  and the app won't start while the file holds one (`Directory.refuse_plaintext`). A password typed into Settings is
  `domain.credential.Typed`, which no file can hold, until `Directory` keeps it in the account's own item,
  `account:<address>`, and writes `{"keychain": item, "version": n}`: a new one replaces what the item held and is the next
  version (`keychain_credential`), so IDLE logs in afresh and a process that read the last one reads it again. Removing the
  account takes its item out, and a Keychain that won't is `Unreachable`, the file left as it was. `accounts password` and
  `ai key` do the same from the command line. A draft's test tries a `Typed` password and keeps it nowhere. The Claude
  key and the Proton Bridge's password are kept and taken out from Settings through `/api/ai/key` and
  `/api/accounts/bridge-password` (`Directory.set_api_key`, `clear_api_key`, `set_bridge_password`,
  `clear_bridge_password`), in `anthropic-api-key` and `proton-bridge-password`; the Bridge's is found after the
  environment and 1Password and before its file (`secrets.bridge_password`), and a new one tells each account on the
  Bridge it changed.
  `Directory` and `mailmind.bootstrap.assistant` (the API key, the daily limit) read through
  both. `ConnectionProbe` is bound in `mailmind.bootstrap.accounts`, and is no adapter of Accounts': a login goes through the Mailbox's sessions, which pick the IMAP adapter.
  An account is still a dict, as the file holds it or as `domain.account.normalized` fills it in, and the whole app reads `acct["host"]`. `Endpoint` (`imap_endpoint`, `smtp_endpoint`) works out a server's host, port and TLS from an entry and its type,
  and an SMTP host left out is the IMAP host, so an account with one endpoint has both. `add_account` refuses an address already there. Both it and `update_account` refuse a credential no login could read, and an endpoint no connection could reach, the last only for what the
  write changes (`refuse_bad_endpoints`): what the file already held is the owner's, and an entry is read however it stands. `validate_new` still judges a whole entry, as it did: an edit of one with an unknown type, a port out of range or a generic account's host gone says so. `domain.draft` says what an unsaved account may try (see [Security model](security.md)).
  `domain.provider.ProviderProfile` says what an account's server does on its own and its
  quirks: Gmail files sent mail itself, archives by dropping `\Inbox` and threads by X-GM-THRID;
  Proton Bridge (or any loopback STARTTLS server) is fragile, recycled sooner in the pool and never
  bulk-expunged, and marks trashed mail read; `with_capabilities` adds what CAPABILITY says.
  `provider.resolve(acct)` replaces checking `kind == "gmail"`. `domain.health` says how an account stands: `error_kind` is what kind of trouble an error names, from its class and never its text, and `AccountHealth` the
  read model. The Mailbox only reports (`syncing.sync_record`, and `accounts.error_kind` through its port), and `application.health.Health` answers `/api/account-health`. `domain.events` holds
  `AccountAdded`, `AccountChanged` and `AccountRemoved`, which `config.add_account`,
  `update_account` and `remove_account` publish on the bus once the file holds the change (a
  refused change publishes nothing), and `CredentialsRejected`, which `mailbox.try_login` publishes when a server says no to a login. `application.logins.Logins` hears it and holds that login back (`domain.login_hold`):
  five minutes, twice that for each refusal after a hold ended, up to an hour, since a server counts them and everything the app does opens a connection of its own. `mailbox.open_session` asks first and raises `LoginUnreachable` with nothing sent, so the sync, IDLE's workers,
  the backfill, the queues and a click all do as they did on the refusal, and health reads the same. A connection lost during LOGIN and `[UNAVAILABLE]` are no refusal. A hold names the server, address and credential together, so another of any of them isn't held, a file edited by hand included. `AccountChanged`,
  `AccountRemoved`, a login that works and the page's Retry (`syncing.do_sync_retry`) end it, and Test goes whatever it says (`mailbox.try_login`); a draft (`mailbox.session_for`) neither asks nor tells. It is process state, wired by `wiring.wire`, so a restart ends it and the command line has none. SMTP has its own: a refused AUTH is a 5xx, and the outbox gives up on its first try.
  `domain.tls_pin` says how a loopback certificate is pinned, and `is_loopback` is the one list of hosts whose certificate checks `tls_context` may skip
  (see [Security model](security.md)). It is standard library only.
- `mailmind.mailbox` is the third: the mail on each server and its folders. So far it holds
  `domain.roles`, which says which folder plays which role (`inbox`, `sent`, `drafts`, `archive`,
  `all`, `junk`, `trash`, else `custom`): INBOX by name, then the SPECIAL-USE attribute, then the
  whole last segment of the name, never part of one ("Bulk orders" is nobody's junk). Where
  several could play a role, an attribute beats a name, a shallower path a deeper one, then the
  lower name, never LIST's order (Proton Bridge shuffles it). `adapters.imap.imap_list` reads an
  imaplib LIST answer into `Folder`s, literals included.
  `session.find_role(s, *roles)` finds a folder on a live session; the rail gives each role to
  the one folder `find` picks, and `all` (Gmail's All Mail) is where archiving goes only when
  there is no `archive`. `application.session.MailSession` is the port for one logged-in
  connection: its capabilities (what the server says after login), `enable_condstore` (before any
  select, or the folder says no `highest_modseq`), what changed since a modseq, folders, status, select, UID
  search, a message by Message-ID, headers (in batches of `HEADER_BATCH`, with Gmail's thread id,
  its labels and the arrival date when asked), flags, a body, flag and Gmail label changes, copy, move, a UID
  expunge of the UIDs named, append (the new UID when the server says it), NOOP, and IDLE with
  `interrupt()` from another thread. Names are as LIST gave them, and the errors are imaplib's
  classes through either adapter: a refusal raises `IMAP4.error`, a lost connection
  `IMAP4.abort` or `OSError`. `move_by_copy` is the move a server without MOVE gets.
  `domain` also holds `flags` (a server's flags as columns, and what a read of them may write), `prune` (what a reconcile may take away), `try_target` (which message a try acts on), `filing_answer` (what a person hears of filing), `listing` (what a row of a list says), `inline_images` and `ingested` (what a message's headers come to as it is stored).
  `domain.destination` says where a filing goes (`resolve` on a try, `known_without_server` what holds as a command is accepted), and `domain.move_strategy.choose` which way, as a value (`MoveStrategy`):
  `Way.LABELS`, `Way.MOVE` or `Way.COPY`, and whether to read `\Seen` first because the server will set it. It goes by the
  verb, not a folder's role, and takes what the profile says and the session's capabilities as plain values. No way sends a
  bulk EXPUNGE, on any server. `ServerOps.apply` carries it out, and each adapter's `move` (what a sweep and a reversal call) asks
  the same `offers_move`. The adapters
  are `adapters.imap.imaplib_session` (what an account runs on unless it asks otherwise) and `adapters.imap.imapclient_session`
  (only it imports `imapclient`). `adapters.imap.imap_names` holds `mailbox_arg` and `imap_quote`,
  which `accounts` re-exports, and `gmail_labels`, the one form both send Gmail.
  `adapters.sqlite` is a package with a module for each concern, holding what is stored of the mail, the command log's rows and the undo stack: the app calls it `store` (`from mailmind.bootstrap.mailbox import store`), and what the other contexts' wiring may call of it is `mailbox.stored_mail`, the list `tests/unit/test_architecture.py` pins. It asks `domain.threading` for thread ids.
  `mailmind.bootstrap.mailbox` picks each account's adapter: imaplib, unless the account asks for
  IMAPClient (`"imap_adapter": "imapclient"`) and the process offers it, which only
  `front.build` does (`offer`). The command line ignores the ask and never imports `imapclient`.
  `use(kind)` makes one adapter every account's, as the contract tests do, and an autouse fixture
  resets both before each test. It opens a session (`open_session(acct)`) the same way for both:
  TLS, the pin, login, txguard, `ReadOnlySession` read-only, and a login its server refused not at all until its hold ends (`accounts.logins`, above). Every IMAP connection the app opens
  comes from it, and only the imaplib adapter calls `accounts.connect`.
  `tests/contract/test_mail_session_contract.py` runs every case through each against the fake, and
  `tests/contract/test_mail_session_dovecot.py` against Dovecot. `application` also holds `commands` (`MailCommands`, the one path a change to the mail on a server takes), `server_ops` (`ServerOps`, what a try asks of the server, and the reversals), `sync` (`Ingest`, mail into the database), `syncing` (`Syncing`, each account's sync and the body backfill), `reconcile` (`Reconciler`, what the server no longer holds, and the record of each account it pruned through its `ReconcileTrail` port, which `adapters.reconcile_trail` writes in the Reconciler's own write block, after the server and before the next turn asks it anything), `folders` (`Folders`, the rail and a folder's sync as it opens), `queries` (`Queries`, what the page lists), `bodies` (`Bodies`, a message opened), `filing` (`Filing`, a person's filing, and the tray), `reversal` (`Reversals`, what reverses a move), `stats` (`LiveStats`), `pool`, `warm` and `realtime` (IDLE's workers), and `domain.events` the events they publish. `mailmind.bootstrap.mailbox` binds them, and it's how the routers and the tests reach them: `commands`, `server_ops`, `ingest`, `syncing`, `reconciler`, `folders`, `queries`, `bodies`, `filing`, `undo`, `stats`, `pool`, `warm` and `realtime`. A snooze, a mute and a block are reversed by their own contexts (`Snoozing.reverse`, `Muting.reverse`, `Cleanup.reverse_block`), and `Undo`, in `platform`, is handed each kind's reverser there. The rest of the context is standard
  library only.
- `mailmind.discovery` is the fourth: finding mail by what a person types. `domain.query.QuerySpec` is the search
  grammar parsed, by a parser no input can make raise. `narrows()` is what a bulk command must pass: a folder or an
  account alone spans nearly everything, and a negation never narrows by itself. `domain` also holds `guard` (a filter
  resolving to 60% of all mail is a sweep of the whole mailbox), `search` (what a request asks around its `QuerySpec`, one
  result a message, a page and how a hit reads), `views` (what makes one and which may go), `documents` (what the hub asks,
  counts as one file and shows), `known` (what is asked after, in which forms, who the stored mail vouches for given the
  facts, and how the answer reads) and `contacts` (autocomplete's needle and the card). None of them reads a clock: a use
  case hands in `rel_time` or `now_iso`'s value. `adapters` holds `fts` (`to_sql` turns a parsed query into a MATCH and bound SQL, and
  `index_message` and `forget_message` keep the index up to date), `sqlite` (`StoredMail`, which reads `messages` and the
  rest as read models and writes nothing) and `views_store` (the saved views, the `views` document in the database). `application`
  holds `ports.py` and the use cases `Search`, `SavedViews` (which reads, changes and writes the views in one write block, so two saves
  at once lose neither), `DocumentHub`, `Contacts`, `KnownContacts`, `Suggesting` (what the search box offers as you type) and `Conditions` (whether a query can be judged of mail as it arrives, and whether a message fits it). `mailmind.bootstrap.discovery` binds them, and it's how
  the routers and the command line reach Discovery (`discovery.search.answer(qs)`, `discovery.known.ask(addrs)`).
  A folder's role goes through the Mailbox (`mailbox.folders.role`), and the account list through Accounts.
  It is standard library only.
- `mailmind.curation` is the fifth: what stays in your inbox. `domain` holds `classifier`, `rulebook` (one domain
  matcher, `domain_covers`, for the classifier, the Screener and expiry; what a token is, who is left to decide on and what a keep
  rule covers), `cleanup_plan` (`CleanupPlan`: who it blocks, how far its sweep goes, reading one past the cap to know it was hit,
  and what its moves came to), `sweep_plan` (how an inbox falls, how a stored row is judged and what an AI sweep may take),
  `expiry`, `live` (a message as a sweep reads it), `noise` (the score, its bar and the order) and
  `unsubscribe` (`Attempt`, what each way out comes to and what is latched; what a mailto may send and what a preview shows). What goes
  to the server, in what order, stays in the use cases: audit before a move, block before sweep before unsubscribe. A rule change's
  `reclassify` stays a direct call, since its count is in the answer, it runs after the sweep, and when it fails an Undo of a block
  says so, none of which a handler after the commit could do. `adapters` holds `rules_document` (the rule book, the `rules`
  document in the database, through `platform.db.documents`),
  `safe_http` (the one-click POST, the one request MailMind makes to an address a message named, vetted as hostile),
  `sqlite` (reads `messages` as a read model, owns `unsub_log` and `sender_unsub`) and `audit_events` (`AuditEvents`, the `SweepTrail`
  port: a record of each message a sweep or expiry trashes and of each sender's cleanup, in the database's audit trails, and both trails
  read as one for `/api/audit`); `application` holds
  `ports.py` and the use cases `Cleanup`, `Unsubscribing`, `Screener`, `Sweeps`, `Expiry` and `Filters` (a search that stars or marks read new Inbox mail). `mailmind.bootstrap.curation`
  binds them, and it's how the routers and the command line reach Curation (`curation.cleanup.execute(body)`,
  `curation.sweeps.sweep(acct)`). Filing goes through the Mailbox (`mailbox.filing`), and a message's rows through `stored_mail`. The CSRF token comes
  from the perimeter (`perimeter.CSRF`). A sweep's
  `move_verified` is `mailbox.application.bulk_moves`. It is standard library only.
- `mailmind.attention` is what asks for you later: snooze, mute, reply later and follow-ups. `domain` holds `snooze` (a
  wake time from a preset or a date within its window, what a request names and what Undo gives back), `mute` (a repeat does nothing,
  and what Undo gives back), `events` and `followups`: how long a thread may wait, and what a send, a pin, a dismissal and an un-pin
  make of a follow-up. `followups` also states what the every-minute round asks. `RECEIVED_NUDGE`, `COMES_DUE`, `EXPIRES` and `MET` each
  name their conditions, a predicate for each, and answer for one `Thread`. The round asks that of every thread inside one write, so it
  runs in SQL: `adapters.sqlite` says each condition as a fragment and builds its statement from the domain's names, and a name it
  doesn't know stops it loading. `tests/unit/attention/test_followups_in_sql.py` holds the two to the same answers, over named cases
  and random threads (`MAILMIND_FOLLOWUP_RUNS` asks for more). A new condition goes in both. A dismissal stays through every round.
  `adapters.sqlite` holds the SQL of `snoozes`, `muted`, `tasks` and `followups`, and reads a follow-up, asks the domain and writes what it says.
  `application` holds `ports.py` and the use cases `Snoozing`, `Muting`, `FollowUps`, `Tasks` (what a person confirmed from their mail, migration 46, by message key and never by pk) and `Clocked`, the every-minute job. They publish
  what happened once it has committed (`SnoozeSet`, `ThreadMuted`, `FollowUpsChanged` and the like), and `wiring.wire` does what that
  makes stale and tells the tabs. `mailmind.bootstrap.attention` binds them, and it's how the routers reach Attention
  (`attention.muting.mute(body)`, `attention.book.list_waiting(c, account)`). A message's row and `queue_flags` go through `stored_mail`, and
  undo through the Mailbox (`mailbox.undo`).
  `mailmind.kernel.attention` is a different module, what a verdict asks of you, which the modules that need both import as `verdict`.
  It is standard library only.
- `mailmind.calendar` is the sixth: invites, recurring series, Today & next and RSVP replies. `domain` holds `invite` (what the reader
  finds in an event, `Fields`, and what that comes to, `Invite`, the card's words included), `agenda`
  (which event a message speaks for, and what a cancellation or a moved occurrence does to its series) `publish` (a new event as a `METHOD:PUBLISH` file, which invites nobody) and `rsvp` (an iMIP
  REPLY and its idempotency key); `adapters` holds `icalendar_acl`, the reader of an invite's text behind the `IcsReader` port (`parse`, the event a
  card shows, `events`, every event the text holds, and `next_occurrence`), and `sqlite` (reads stored bodies and the outbox's keys, and
  writes nothing); `application` holds `ports.py` and the use cases `Agenda`, `Invites`, `Publishing` and `Rsvp`. `mailmind.bootstrap.calendar`
  binds them, and it's how the routers reach Calendar (`calendar.agenda.upcoming()`, `calendar.rsvp.respond(body)`). An
  RSVP goes through Compose's outbox, and a message through `stored_mail` and `ingest` until the Mailbox holds it.
  `icalendar_acl` is the only module that imports `icalendar` and `recurring_ical_events`, and nothing of either leaves it. So Calendar is
  standard library only but for that module. `mailmind.bootstrap.calendar` binds it as it loads, so nothing the command line loads imports
  that at its top: the command line reads no invite. An invite is a
  stranger's text and the reader runs on a request thread, so it never raises. `icalendar_acl` takes a text of at most `_MAX_TEXT`
  with at most `_MAX_EVENTS`, expands nothing finer than a day and gives each call a budget of its thread's time (`_within`: a tracer
  raises at the next plain call of the reading, since a rule that never comes to a date walks to year 9999 inside one `next()`
  of dateutil's). `Agenda.upcoming` has an allowance for one list on top (`_EXPAND_SECONDS`), past which a series shows its first date.
  One rule says what a TZID means, `invite.zone`: a name neither the zone database nor Windows' list of zones knows floats, so a VTIMEZONE counts for
  nothing, and `icalendar_acl` lets the library keep none and look up no TZID. It keeps what isn't text as its line said it (`_AS_SAID`),
  since an RSVP echoes a UID and writes to an organizer. `Invite.rdates` is there only where the text has some.
  `tests/contract/test_ics_reader_contract.py` holds what the reader must do whatever the text says, and
  `tests/unit/calendar/test_invite_text.py` what a text comes to.
- `mailmind.compose` is the seventh: drafts, building a message, the outbox and delivery. `domain` holds `markdown` (a body written in Markdown, run back through the sanitizer), `snippets`, `builder` (`build_message`, which nothing
  else builds with, and `reference_chain` and `thread_ids`), `reply`, `forward`, `composition` (what a request to send or save comes to: its text,
  its quote and the HTML that goes with it), `schedule` (the limits on a send's time), `draft` (what makes a save, whether one may go in, what
  is a draft and which copy is ours to remove), `draft_journal` (what a save records, when its push is due and what its row says), `resume` (a saved
  draft split back into your text, its quote and its files, and which copy may be believed), `attachments`, `limits`, `outbox_item` (a try's numbers,
  what one leaves on a row and what a delivered send still owes), `events` and `errors`. A row's state changes stay in `DurableQueue`, whose conditional
  UPDATEs the worker can't race, so `outbox_item` holds no copy of them. `adapters` holds `smtp` (`SmtpTransport`, the only module that speaks SMTP),
  `snippets_store` (the `snippets` document in the database) and `sqlite` (`outbox` and `drafts`). `application` holds `ports.py` and the use cases `Composing` (a request made into a message), `Snippets` (a change one write block, as the saved views'), `Sending`, `Drafts`
  and `Outbox`, the queue and its worker. `mailmind.bootstrap.compose` binds them, and it's how the routers, the other contexts and the tests reach
  Compose (`compose.sending.send(body)`, `compose.drafts.save(body)`, `compose.outbox.drain()`, `compose.book` for its tables). `mailmind.compose.api` names
  what others use without its wiring (`build_message`, the events). The copies on the server go through the Mailbox's `application.copies.Copies`
  (`mailbox.copies`). A message's row through `stored_mail`, and what's kept about folders through `mailbox.folders`. It is standard library only.
