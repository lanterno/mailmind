# Concurrency

Every job that talks to an account's IMAP server runs through
`gates.run(account, fn, priority=..., wait=...)` (`mailmind.platform.workers.gates`): the
requests that open and file mail, undo, reconcile, expiry, the body backfill, the outbox, the
command path and the thread summarizer. `MAILMIND_WORKERS` picks the gate:

- On the Python app, unset (or `global`), one process-wide lock is every account's gate, so all live-IMAP work is
  serialized and a black-holed account holds up the rest until its connection times out.
- `account`, each account has a gate of its own. Waiting jobs go by priority, then in turn. An
  `EVERY_ACCOUNT` job waits until no gate is held and holds them all, and a job that would come
  after it waits behind it. The whole suite passes under either.
- On the Rust server (`environment::gate_per_account`, `mm_mailbox::gates`) a gate for each account is the default,
  and `one` (or `global`) keeps the single gate.

A job runs on its caller's thread either way, so context variables (`assistant.allowing_over()`)
and txguard's per-thread record stay with it. The connection pool's per-account lock is taken
*inside* the gate. The 300 s sync and IDLE's syncs don't take it yet. `mailbox.syncing.sync_guarded` is
their single-flight: a sync asked for while one runs is recorded, and the running one goes round
again for it as it ends.

On the Rust server that flight is each account's own (`mm_mailbox::syncing::Syncing`): a round syncs the accounts
not already syncing side by side, a thread each, and an account asked for while its sync runs goes round again as
it ends, so one slow server holds up no other account's sync. An Inbox's first scan stores its newest 500 messages
and leaves the rest to the `older-mail` lane (`Syncing::read_older`), which reads them newest first, 500 a step and
`OLDER_STEPS` steps of each folder a round, a thread an account, and takes no gate either. A first scan that leaves
older mail, the Inbox's or another folder's as it opens, sets the lane's round going at once, and so does an
account's next sync that works after a round of it met trouble that may pass. It runs in the read-only
profile too, where the sync doesn't: it only EXAMINEs and peeks and writes only the database, so a folder first
opened there is read to its oldest message. Opening an Inbox whose first scan hasn't begun joins the account's sync
(`InboxSync`) rather than scanning it again on the gate, so a message opens meanwhile.

- A job is a function, not a block: it returns what its caller needs instead of setting the
  caller's variables.
- Each names its account, or `gates.EVERY_ACCOUNT`
  for work across accounts, and a `gates.Priority`: INTERACTIVE, AFTER_USER, SYNC or
  MAINTENANCE. A run over every account is not work across accounts: `/api/sweep` goes through
  `Sweeps._each_account`, one job per account on its own gate, in turn. Reconcile
  (`mailbox.reconciler._reconcile_folders`) goes further: one job per folder with a `sync_state` row
  (`reconciler.reconciled_folders`), so it never holds a gate across folders.
- Background work passes `wait=False` and skips its turn on `gates.Busy`, so it never stalls
  the user. `deadline=` (seconds) raises `Busy` for a job not let in by then, and it never runs.
  A job that waits on a gate from inside a job raises `RuntimeError` instead of hanging; one that
  takes another account's gate with `wait=False` may.
- Tests ask `gates.held(account)`, and hold the gate with `held_elsewhere()` from
  `tests/support/gate.py`, which runs a job like any caller. `tests/e2e/test_chaos.py` holds what one
  account's trouble may do to the rest; `FakeIMAPServer.stall` black-holes a command.

`mailbox.pool` (persistent per-account IMAP sessions) and `mailbox.warm` (multi-folder sync)
are both **default-off** behind `MAILMIND_POOL` / `MAILMIND_WARM_FOLDERS`, or an account's own `"pool"` / `"warm_folders"` key while the variable is unset. Code must
work with them unset. `Pool` is a `KeptConnections`, which does the keeping. `Pool` says which accounts, that a
NOOP checks and a LOGOUT closes, and that the provider gives the numbers (`recycle_seconds`, `recycle_uses`).

On the Rust server and in the Mac app every account's connection is kept, with no switch (`mm_mailbox::kept`, bound in
`mm_server::app` and `kept_wiring`). The jobs that hold an account's gate (a message opened, a folder synced as a
person opens it, a change and its undo, a folder made, renamed or deleted, the Trash found in the server's LIST, the
body backfill, the outbox's copy to Sent, a draft kept on the server) share one logged-in session, lent only to a job
holding that gate (`Gates::holds`) and to one at a time; any other job opens a session of its own, as before. A NOOP,
under ten seconds, checks it before each lending; one ten minutes old or lent 200 times is logged out and another
opened, and a Proton Bridge connection after 90 seconds or 40 lendings (`kept_wiring::keeping_of`). Each job starts as
on a new connection: a command that needs a folder is refused until the job selects one, though the server may still
hold the last job's folder selected (an APPEND, which names its own, goes on regardless). The connection remembers
that folder, whichever job selected it: a rename or delete of it, or of a folder it sits inside, ends what the job may
ask of it there, and the connection is logged out as it comes back rather than kept, since servers differ in what they
do with a session whose folder went. A lending that ends with the connection lost, a failure or a panic drops it. A
wake from sleep (`Host::woke`) drops every kept connection without a LOGOUT, as the server forgot them while the Mac
slept; an account changed or removed (`kept_wiring::KeptHeard`) logs its own out, now or as it comes back, even when the
removal comes as a job is logging it in. A quit (`Host::close`, and `mailmind-server` once it stops serving) logs them
all out together, each on a thread of its own, and waits `QUIT_SECONDS` (four) at most. `mailmind-server` has no signal
handler yet, so a Ctrl-C or a SIGTERM ends it without those LOGOUTs. Each kept connection sends ENABLE CONDSTORE as it
opens, where the server offers it, whether or not a job will ask for it. The background sync, the older-mail lane,
IDLE and the folder rail take no gate, so they still log in for themselves; they are most of an account's logins.
`/api/debug/pool` says what is kept; `MAILMIND_POOL` and an account's `"pool"` are the Python app's alone.

IDLE's workers follow the configuration. `Realtime` is an `IdleSupervisor`, which runs them, and says what a
worker does: which accounts, how each is reached, the IDLE loop and what a change does.
`realtime.reconcile`, which the account events call,
retires the worker of an account removed, disabled or now reached another way (kind, host, port,
TLS, credential), and starts one for each account without one, up to `MAX_IDLE_ACCOUNTS`. An alias
or signature change leaves the worker be. A worker also checks its account after each IDLE cycle,
so an edit of the file, which publishes nothing, still reaches it. The command path gives up a queued
move whose account is gone, as the outbox does a send.

A worker whose connection failed rests (5 s, doubling to 5 minutes) and asks for a sync of its account once its next
connection is ready: the server answers again, and the sync says so. One whose connection served 10 s or more and was
lost connects again at once, with no rest (`AT_ONCE_AFTER`, the Rust core's `Rests::at_once_after`), so a server gone
away is found gone within moments, and a connection the server dropped is back. Meanwhile every login, IDLE's and every job's,
goes through `open_session` (the Rust server's `ImapLogin::open`), which tells the sync of one that couldn't be made
(`Syncing.unreached`): trouble that may pass shows at once as trying again, the tabs hear `sync-trouble`, and the
sync runs now where this process runs one, rather than the status saying Synced until the next round, minutes on.
One dropped connection that connects again at once logs in, and shows nothing. While an account's trouble may pass, the
`retry` job tries its sync every 20 s (`Syncing.try_unreached`, `sync_health.RETRY_SECONDS`), so it says it synced within
moments of the server answering again, not when IDLE's rest or the next round ends, minutes on. A try that fails changes
nothing and says nothing: it counts no failed sync, so trouble shows as it would have.

`realtime.stop()` and a retirement only interrupt a worker's session (`MailSession.interrupt`, a
socket shutdown); the worker closes its own connection. Closing a socket another thread may be
about to wait on frees its descriptor number for the next socket opened.

`mailmind.platform.profile` reads the profile from `MAILMIND_PROFILE` (`full`, the default, or
`read-only`) and the older `MAILMIND_NOSYNC`, which still selects read-only and wins when the two
disagree. A value either doesn't take fails toward read-only (`environment` says how), and the app
refuses to start on a profile name it doesn't know. `background.nosync()` asks the profile.
