# Schema migrations

The database's code is `mailmind.platform.db`; `store.conn()` and `store.init()` call into it.

- The driver is SQLCipher's build of SQLite, which encrypts the file and its log ([security.md](security.md#at-rest)).
  Code takes SQLite's names from `driver` (`Row`, `Connection`, `OperationalError`), never the standard library's `sqlite3`,
  whose classes are others; the frozen migrations' `sqlite3` is bound to `driver` in `migrations/__init__.py`, so their
  `except` clauses catch what it raises. `database.keyed(path)` opens any database file with the key: a backup, the
  upgrade check's copy, an export. A file still plaintext is refused there (`NotEncrypted`), never opened as it is.

- `database.connect` opens every connection, keyed, and makes a new file its owner's alone (`init` takes back one from before, with its
  log): WAL, `synchronous=FULL` with `fullfsync` (a commit
  survives a power cut, because `delivered` and `applied` are exactly-once gates),
  `busy_timeout` 60 s, `foreign_keys=ON`, which the cascades depend on, and `temp_store=MEMORY`, so what a
  big sort holds of the mail never lands in a temp file.
- No transaction stays open across a network call. `database.connect` tracks each connection for
  the thread that opened it (`Route` ends what a request left, with `txguard.release_thread`, as a pooled thread
  goes on to the next), and `txguard.refuse_network(what)` raises `TxAcrossNetworkError`
  while one has a transaction open, since a write held while a server answers keeps every other
  writer waiting. An IMAP session and each command (`mailbox.open_session`), an SMTP session and
  all it sends (`SmtpTransport.connect`), a Claude API request and a one-click unsubscribe check
  it, in tests and in use. Commit before talking to a server; a new network path calls
  `refuse_network` first, or wraps the connection's method with `txguard.guard_calls`.
- A use case never runs a connection by hand (`tests/unit/test_architecture.py` holds it to that). It gets a
  `UnitOfWork` as its first argument and says what each step is, one connection a block: `with self._uow.read() as c:`, or
  `with self._uow.write() as c:`, which takes `BEGIN IMMEDIATE` up front, commits as the block ends and rolls back if it
  raises. The order is read and let go, then the network, then a short write. A loop that commits midway is a block for each
  commit. A block that writes only sometimes reads first and opens a write only if there is something to do, unless what it
  read decides what it writes, where the read belongs in the write. `read()` raises `WroteInARead` if its block left a
  transaction open, since closing would lose it. `write()` raises `NestedWrite` inside a transaction on the same thread,
  which would otherwise wait out `busy_timeout` on itself. Hand-run connections remain outside use cases: `store.conn()`
  for the command line, the bootstrap and the tests, `Undo`, and `DurableQueue`, which takes `uow.connect`.
- `migrations.MIGRATIONS` is a list of `(target_user_version, callable)` run against
  `PRAGMA user_version`. Migrations 1 to 20 are frozen in `migrations/m0001_m0020.py`, beside
  `BASE_SCHEMA`, `LEGACY_COLS` (pre-versioning column adds) and copies of the code they call
  (`_frozen_threads.py`, `_frozen_mailparse.py`). Never edit those files. A schema change is a
  new migration in a new file after them, as `m0021_ai_usage.py` is; one that needs the app's
  code freezes a copy of it.
- Migrations are **append-only**. Never renumber or delete one — the version number is
  the on-disk contract, and retired features leave a historical no-op behind (`_m14_notes` →
  `_m18_drop_notes`).
- `migrate.init` runs the base schema, then each pending migration, each in a transaction of its
  own (`BEGIN IMMEDIATE`) that sets its version too, so one that fails leaves the database as it
  was before it. A migration gets the connection through `_Within`: `execute`, and
  `executescript` run one statement at a time inside the transaction. It never commits.
- Each must still be idempotent; `init()` runs on every process start. A database with no
  migration to run is left alone, and the base schema runs only on one older than the frozen migrations'
  last (`migrations.BASE_UNTIL`), or it would bring back a table a migration retired. Idempotent means at its place in the
  history (`tests/unit/platform/test_schema.py` runs each twice there): an older one may read what a later one ended, as m30
  does `summaries` and m44 `outbox.in_reply_to_pk`, so none runs over the newest schema, and a test wanting one again builds
  the database at its version.
- A migration that builds a table afresh says so in `migrations.REBUILT`, as m38 does `msg_fts`: the upgrade check
  reports `rebuilt` rather than a change of count, and leaves out the index's own tables.
- A migration that ends a table says so in `migrations.RETIRED`. The upgrade check then reports it
  as retired rather than as rows lost, and `tests/unit/platform/test_db_upgrade.py` holds it to that. A column it ends goes
  in that file's `ENDED`. One that carries what it ends over to another table says so in `migrations.FILLED`, as m45 does
  `assessments`, `snoozes` and `message_identity`: the check reports that table's growth as `carried over`, and its shrinking is
  still a loss.
  One that takes rows on purpose says so in `migrations.TRIMMED`, as m28 and m50 do, with how to count them beforehand (a table may be taken from by more than one): the check passes exactly that many gone, and fails one more. m50 takes the rows of mail deleted while an older MailMind left foreign keys off, in every table that goes with its message.
- Before it migrates a database that holds anything, `init` backs it up with SQLite's backup API
  to `backups/mailmind-v{N}-{UTC time}.db` beside it, encrypted with the same key and readable by its owner only, keeping the
  newest 3 (`backup.py` says how to roll back).
- `uv run python -m mailmind db check-upgrade` (`just db-check-upgrade`) migrates a read-only copy and
  prints only per-table row counts and integrity verdicts. A foreign-key problem the database
  already held (an orphaned body or summary from before foreign keys were on) is reported by
  table and passes; one the migrations add fails. The owner runs it on the real database before
  an upgrade; no agent does.
- `msg_fts` keeps no copy of the mail (m38: `content=''`, `contentless_delete=1`, SQLite 3.43 or newer, which `init`
  checks). Read rowids and ranks from it and columns from `messages`; its own columns read as NULL, and `snippet()`
  and `highlight()` have nothing to work on. `uv run python -m mailmind db compact` (`just db-compact`) gives back freed room.
- `tests/unit/platform/test_db_upgrade.py` upgrades a database seeded with data from every version. A
  migration that derives a column on purpose adds it to `DERIVED` there.
