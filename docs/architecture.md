# Architecture

Pipeline, roughly in data-flow order:

```
mailmind.config.json → mailmind.accounts → the Mailbox's Ingest → curation's classifier → mailmind.mailbox's store (SQLite)
                                                                ↓
                                              mailmind.assistant (Claude API) → mailmind.http + web/
```

Around that spine: `mailmind.mailbox` (IMAP writes, IDLE), `mailmind.compose`
(send), `mailmind.discovery` (FTS + operator grammar), `mailmind.calendar` (invites), and `mailmind.assistant`
(everything asked of Claude).

`discovery.build` reads a date with no zone at this machine's midnight. `discovery.visible` leaves snoozed mail out of
a search and of what a command acts on, unless the query says `is:snoozed`. `discovery.BM25` is the one table of
ranking weights, which Ask uses too. All three come from `mailmind.bootstrap.discovery`, below. A message is one search result
however many folders hold it: among the copies the query found, the one in the Inbox, else any but All Mail's
(`StoredMail._in_folders_playing`, by what the last LIST said and else by name).

`mailmind.content.mime`'s `dec` and `sender_of` are the one way to read a header off a compat32 message: they unfold
it first, since `decode_header` and `parseaddr` both go wrong across a fold. `mime.walk` takes an
attached email as one part of its message, as a reader does; what it holds isn't the outer message's.
`mime.recipient_addrs` reads To and Cc as bare addresses before any encoded word is decoded, so a name
can't pass for one.

`message_labels` (migration 47) holds a Gmail copy's labels, read as a sync fetches it and going with the copy; `label:` searches it. Mail stored before has none until a
scan fetches it again.

`messages.recipients` is the decoded To and Cc as text, for search. `message_recipients` (migration 35,
`store.set_recipients`) holds each address of each copy, written at ingest and again when a body is stored,
and goes with the copy. It is what `discovery.known` asks. Mail stored before it was read from the text,
which was cut at 2000 characters then, or for a column that was cut from the raw message where one is stored;
with none, the whole list comes when a body is stored.

The app is one view, a real folder-and-account client whose Inbox opens on today's brief
(`features/assistant/Brief.tsx`, built from the `/api/digest` items and the stored `/api/briefing` narrative; its
side column from `/api/upcoming` and `/api/waiting-on-others`).

## The `mailmind` package

The app lives in `mailmind/`.

- `mailmind.platform.environment` declares every environment variable MailMind reads, `MAILMIND_*` and the two
  `ANTHROPIC_*` ones: its type, what unset means and what it does. Its docstring and declarations are the one list,
  and say how each reads: one rule for every switch, and what a value a variable doesn't take reads as, which the
  log says once, by the variable's name, never its value. It is the only module that reads the environment
  (`tests/unit/platform/test_environment.py`), and it reads at each call, so a test changes a variable with
  `monkeypatch.setenv`. A new variable is declared there, and the README's table names it. It is standard library
  only, not the pydantic-settings the proposal named.
- `mailmind.platform.settings` is the one source of the data paths and the port. Code calls
  `settings.current()` where it opens a file or checks the port, never once at import, so a test
  can swap the whole data directory with `settings.use(...)`. `MAILMIND_HOME` names the data
  directory, and nothing creates it. `uv run python -m mailmind serve [port]` puts the port in the settings.
- Each context's wiring (`mailmind.bootstrap.<context>`) makes what it holds between requests, its use cases with their
  caches, the pool, the sync status, Undo's stack, in its `assemble()`, which importing the module runs once.
  `wiring.assemble()` makes a new set of every context's, in the order they use each other, with no event kept and nothing
  subscribed. Nothing outside a module keeps one of those objects: code reads it through its module when it uses it,
  `mailbox.ingest`, never by a name bound at import (`tests/unit/test_architecture.py`). The command line uses the set its
  imports made; the tests assemble a new one for each test.
- `mailmind.bootstrap.wiring.wire()` subscribes the app, on `mailmind.platform.bus`, to
  what the workers publish: Compose's `Sent`, `Failed` and `DraftCopyRemoved`, the Mailbox's `Applied` and
  `InboxChanged`, and what Attention's use cases say happened, which refreshes the stats and tells the tabs.
  It also hands each account event (below) to `realtime.reconcile`. `Restored` (a
  queued move that won't happen) refreshes the tabs as `Applied` does. Wiring again replaces the last wiring, and `unwire()` ends it (each test's end does).
  `wiring.start()` is the startup: the profile check, the schema, `wire`,
  then the workers. Full, those are the outbox's and the command path's drains, the
  scheduler's jobs and IDLE; read-only, the snooze job alone.
- `uv run python -m mailmind serve` (`mailmind/__main__.py`) is how the app runs.
  `mailmind.bootstrap.front.serve` starts the app as above and serves it with FastAPI on uvicorn,
  bound to 127.0.0.1. Its `build` offers IMAPClient to an account
  that asks for it (see the Mailbox paragraph). A line at startup says which IMAP adapter
  serves how many accounts (`[imap] ...`). Every route runs on a FastAPI router, on a thread of
  AnyIO's pool, whose limit `http.app` raises to 64 at startup (see [Routing](routing.md)).
  `mailmind.http.middleware.Perimeter` runs the perimeter (see [Security model](security.md)) before any of it,
  and before a body is read. A tab on the event stream holds none of that pool: `routers/events.py` waits for its next event on
  a thread under a limiter of the stream's own, and how many there are is what the hub lets listen. `mailmind.http.serving.Server`
  ends open streams as shutdown begins (`Hub.end_streams`), so Ctrl-C stops at once. A request still blocked after the 3 s of grace
  (`serving.GRACE_SECONDS`) is cancelled, and its thread, a daemon, doesn't hold the exit.
- `mailmind.platform.profile` says how the process runs, `full` or `read-only` (see Writes,
  undo, and offline).
- `mailmind.platform.workers.gates` is the IMAP gate every live-IMAP job passes (see
  Concurrency). `account_worker` beside it holds the gate for each account, and the connection kept
  for one between jobs (`KeptConnections`, `ConnectionPolicy`). `idle_supervisor.IdleSupervisor` keeps
  one worker thread per key in line with what's wanted. Both are standard library only, and know nothing of IMAP.
- `mailmind.platform.workers.scheduler.Scheduler` runs the background jobs. A job is a round (a
  function doing one round of the work) on a lane of its own, a thread named `job:<name>`, so
  its rounds never overlap. A round may return the seconds until its next; one that raises is
  logged and counted, and the job goes on. `mailmind.bootstrap.background.schedule(jobs)` is the job table: `snooze` in
  every profile, then `sync`, `draft-push`, `assess`, `backfill`, `draft-ahead`, `brief-ahead` and `tidy` (each only for a person who turned it on), `reconcile`, `expire` and `forget`
  in a full one. `assess` triages the mail that has no summary yet, outside the sync's single-flight,
  and a sync that brought new mail kicks it (`background._kick`). With AI off, or triage held back
  by the cost governor (see [AI calls](ai.md)), it tries nothing: `assistant.assessments.summarize_pending` returns 0
  at once, and a round (`assistant.assessments.round`) that is only yielding to a click comes back in `ASSESS_YIELD` seconds.
  `/api/debug/jobs` shows each job's timing and last outcome, an error as its class name only.
  A test calls the round itself (`mailbox.syncing.backfill_round()`) or `jobs.run(name)`. It is
  standard library only.
- `mailmind.platform.logging` is the log, on the standard library's `logging`. A module takes
  `from mailmind.platform.logging import get_logger`, never `from mailmind.platform import logging`, so that
  name stays the standard library's, then `log = get_logger("jobs")` and
  `log.warning("%(job)s failed", {"job": name}, exc_info=e)`. A message is a literal, and a test holds the
  package to it. `Redact` withholds what it can't vouch for: a value shows only under a name on `ALLOWED`,
  anything that looks like an address is withheld even there, and of an exception only the class shows. A new
  field joins `ALLOWED` only if its value can never be a person's. A line goes to stdout for someone at a
  terminal (`[jobs] sync failed: ConnectionResetError`), from the moment the module loads, the command line
  included. Once `wiring.start` has called `setup()`, it also goes as a JSON object to `mailmind.log` in the data
  directory, rotated and readable by its owner only, with `request_id` (`Route`) and `job_id`
  (`Scheduler.run`) from `contextvars`. `MAILMIND_LOG_LEVEL=DEBUG` shows debug lines and each exception's text at
  the terminal, never in the file. The `mailmind` logger doesn't propagate. `/api/debug/health` says how the job
  lanes, IDLE's workers, the queues, the breaker and today's AI ledger stand, in ids and counts, and reading it
  doesn't count as someone here. Only a command's own output still prints (`bootstrap/cli.py`, `__main__.py`, `wiring.assess_batch`, `Directory.check` and `materialize`), which a test holds. It is standard library only.
- `mailmind.platform.bus` is an in-process publish and subscribe. An event is a frozen dataclass
  of ids and counts, and its class is its topic. Handlers run in the publisher's thread once the
  change has committed; one that raises is logged by class name and the rest still hear. Nothing
  on it is durable: work that must survive a crash is a queue row written with the change.
- `mailmind.platform.sse` is the hub behind `/api/events`, the live event stream. `sse.publish`
  numbers each event `<run>-<n>` and keeps the last 512. A tab resumes after the `Last-Event-ID`
  its EventSource sends; one the hub can't resume (an earlier run, or further back than it keeps)
  gets one `resync`. `sse.TOPICS` lists every topic and `publish` refuses any other;
  `tests/unit/platform/test_sse.py` checks that `useRealtime` in `web/src/app/realtime.ts`
  listens for every one, and for `resync` and `hello`, and nothing else. Data carries ids and counts, never a subject. Golden files write the
  run as `<run>`.
- `mailmind.platform.workers.durable_queue.DurableQueue` is the queue the outbox and the offline
  move queue share (see [Writes, undo, and offline](writes-and-undo.md)). It is standard library only.
- `mailmind.platform.workers.singleflight.SingleFlight` makes each costly AI read once. Only one
  run goes at a time for each key. A caller that finds one going waits, then reads what it
  stored. The leader reads again before it makes, unless `recheck=False` asks for a fresh one.
  The brief and delta, the review and thread summaries each hold one (`_FLIGHTS`), and so does
  drafting ahead (`assistant.drafting.DRAFT_FLIGHTS`, whose `busy(key)` answers "generating"). It is standard
  library only.
- `mailmind.platform.db` opens the database, runs its migrations, backs it up before one and
  checks an upgrade on a copy (see [Schema migrations](migrations.md)), and holds `uow.UnitOfWork`, how a use case gets a connection. It is
  standard library only.
- `mailmind.content` is the library every context reads mail through, standard library only, and it knows nothing of
  mailboxes or accounts: `mime` (a raw message as parts and header values), `sanitize` (the HTML sanitizer, verbatim),
  `text` (what a list shows and what search indexes), `headers`, `quote` (the part of a reply you typed, and the
  quote after it) and `frame` (a body as the document its frame loads). A `domain` package may import it.
- `mailmind.kernel` stays standard library only and imports nothing else from `mailmind`. So far it
  holds `draft_tone` and `send_window` (a set and a limit two contexts share), `proposed` (what someone proposes for a calendar or a to-do list, read with care), `conversation` (a list's badge), `when` (`now_iso`, `rel_time`, `ago`), `addresses` (a typed recipient, split, validated and IDNA-normalized), `snoozed` (whether a row is snoozed, as SQL), `assessment` (`Assessment`, what the AI said of a message and how it came to say it, its importance held to 1 to 5) and `attention`, what the AI's verdict on a message asks of you: `needs_you`, `reply_worthy`,
  `commitment_worthy` and `safe_to_file`, and the first two as SQL. Queries ask it, list rows in the API carry
  each answer, and the page reads those and never works them out. `row` (`Row`, a stored row read by column name) and `parsed`
  (`is_list` and `is_object`, the narrowing of JSON as it parsed) are for the types of those that read one, and `parsed` also
  reads a number off a request: `as_int` gives None for what won't read, or for one past 2^53 (JSON can say Infinity, and SQLite
  raises past 64 bits), and `required_int` refuses it. `errors` holds what a use case raises when it can't do what was asked
  (see [Routing](routing.md)). `ids` holds `Pk`, `MessageKey`, `AccountId`, `ThreadRef` and `FolderName`, each a `NewType`: plain at run time
  and to the database, and a type of its own to pyright. A port that takes several ids of one base type names them, so a pair handed
  over in the wrong order fails `just lint`. So far: all of Attention's book, the Assistant's `Mail` and `Answers`, the methods of
  the Mailbox's `StoredMail` and Compose's ports that take an account and a folder or thread together. A pk stays an `int` in the
  Mailbox, where naming it costs far more than it would catch. What a row holds is `Any` and goes in as it is, so a swap of two
  values read straight off rows isn't caught.
- Two checks hold the shape. `tests/unit/test_architecture.py` holds every use case to no SQL, which goes through a port to an adapter, each table to its owner, and other contexts' wiring to the `stored_mail` surface of the Mailbox's store. import-linter's contracts in `pyproject.toml` run in `just lint`:
  bootstrap, then http, then the contexts, then platform, then content, then kernel; within each context,
  adapters over application over domain; no adapter imported outside bootstrap; and the
  `anthropic`, `imapclient`, `icalendar` and `recurring_ical_events` packages each reached only through its adapter.
