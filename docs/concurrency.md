# Concurrency

Every job that talks to an account's IMAP server runs through
`gates.run(account, fn, priority=..., wait=...)` (`mailmind.platform.workers.gates`): the
requests that open and file mail, undo, reconcile, expiry, the body backfill, the outbox, the
command path and the thread summarizer. `MAILMIND_WORKERS` picks the gate:

- Unset (or `global`), one process-wide lock is every account's gate, so all live-IMAP work is
  serialized and a black-holed account holds up the rest until its connection times out.
- `account`, each account has a gate of its own. Waiting jobs go by priority, then in turn. An
  `EVERY_ACCOUNT` job waits until no gate is held and holds them all, and a job that would come
  after it waits behind it. The whole suite passes under either; the owner soaks `account`
  before it becomes the default and the flag goes.

A job runs on its caller's thread either way, so context variables (`assistant.allowing_over()`)
and txguard's per-thread record stay with it. The connection pool's per-account lock is taken
*inside* the gate. The 300 s sync and IDLE's syncs don't take it yet. `mailbox.syncing.sync_guarded` is
their single-flight: a sync asked for while one runs is recorded, and the running one goes round
again for it as it ends.

- A job is a function, not a block: it returns what its caller needs instead of setting the
  caller's variables.
- Each names its account (a message's through `server_ops.account_of`), or `gates.EVERY_ACCOUNT`
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

IDLE's workers follow the configuration. `Realtime` is an `IdleSupervisor`, which runs them, and says what a
worker does: which accounts, how each is reached, the IDLE loop and what a change does.
`realtime.reconcile`, which the account events call,
retires the worker of an account removed, disabled or now reached another way (kind, host, port,
TLS, credential), and starts one for each account without one, up to `MAX_IDLE_ACCOUNTS`. An alias
or signature change leaves the worker be. A worker also checks its account after each IDLE cycle,
so an edit of the file, which publishes nothing, still reaches it. The command path gives up a queued
move whose account is gone, as the outbox does a send.

`realtime.stop()` and a retirement only interrupt a worker's session (`MailSession.interrupt`, a
socket shutdown); the worker closes its own connection. Closing a socket another thread may be
about to wait on frees its descriptor number for the next socket opened.

`mailmind.platform.profile` reads the profile from `MAILMIND_PROFILE` (`full`, the default, or
`read-only`) and the older `MAILMIND_NOSYNC`, which still selects read-only and wins when the two
disagree. A value either doesn't take fails toward read-only (`environment` says how), and the app
refuses to start on a profile name it doesn't know. `background.nosync()` asks the profile.
