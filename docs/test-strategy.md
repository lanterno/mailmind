# Test strategy and architecture

[testing.md](testing.md) says how to run the tests that exist. This document says what MailMind should test, at which
level, on which machine, and why: for its features, its performance and its security. It starts from what a long review
of the Mac app found that the tests had missed, sets the principles that would have caught it, lays out the rings of
tests and the harnesses each needs, and ends with the order to build them in. [test-architecture.md](test-architecture.md)
is the framework that runs the whole-app rings: its parts, its protocol, and what a test looks like.

## 1. What the review found

In November 2026, eleven passes drove the Mac app as a person does: fake mail servers and a fake AI on loopback, keys
and clicks posted into the running app, screenshots of every step. By then the project already had about 1,850 Rust tests,
5,400 Python tests, 780 Vitest tests and 1,130 Mac tests. The passes still found well over a hundred bugs, about forty of
them high or medium. The worst had shipped in every release: a long message was cut off after its first screen and its
links did nothing, because the tests loaded the mail frame without the content security policy the app serves it with.

Sorted by kind, the high and medium findings say where the tests are thin:

| Kind | About | Examples | Why the tests missed it |
| --- | --- | --- | --- |
| Input routing: keys, focus, menus, the responder chain | 15 | Page Down did nothing in a message body (WebKit took it as a key equivalent); `e` and `d` did nothing in a message window (the Edit menu's Emoji & Symbols and Start Dictation took them); digits typed in Send Later's time field scheduled a preset; a stale menu item opened a second window; single keys were dead under a non-Latin keyboard layout | Tests called `keyDown` on a view. The test process is never active, has no key window, and runs no real menu bar, so AppKit's own routing never ran |
| One surface stale after another changed | 15 | Needs you stayed empty for five minutes after a new account's first sync; the rail, tabs and brief disagreed on counts; a list stopped updating once a second window closed; Your Week kept counts after mail was filed | Each test checks one surface. Nothing checks that every surface agrees after an event, or that the event was published at all |
| Acting on the wrong message | 7 | The pane bar's Delete trashed the marked rows instead of the message shown; `e` before a conversation chose nothing; snoozing a conversation snoozed only its newest message | Targeting was tested per entry point, not across every entry point and row shape |
| Production conditions not reproduced | 3, the worst | The frame without its CSP; the probe's forced key window | The harness differed from the app in exactly the property that broke |
| Words that say something untrue | 8 | "Nothing needs you" while mail was still being read; "sent 0"; "1-click" for an emailed unsubscribe | Words were tested as strings, not against the state they describe |
| Data and protocol | 8 | A draft twice on the server; a draft pushed 15 minutes late; a Send Later dated when it was written; editing a Proton account dropped its password | The fakes were kinder than real servers and real timing |
| Layout at the edges | 5 | 31 recipients pushed Send out of the composer; the pane bar truncated its verbs at the minimum size | Layouts were tested at one comfortable size with a little content |
| Time and locale | 5 | Events after midnight said "today"; "This evening" after 18:00 meant tomorrow; zone-less times were read as midnight | Fixed clocks at noon UTC |
| Twins drifting apart | most fixes | Nearly every Mac fix had a twin bug in the page: link risk, event times, reply subjects, chips | Each surface has its own tests and its own copy of the rule |

Two smaller findings matter beyond their count. Test runs of the Mac app once shared the owner's preferences domain,
so a test could have changed a person's settings. And CI ran far less than `just verify` does (section 7).

## 2. Principles

1. **Test the path a person's input takes.** A test drives the code through the same process boundaries, policies and
   dispatch the shipped app uses: the frame under its real CSP, a key through `NSApplication`'s queue and the real menu
   bar, a route through the server as a program. Where a test must take a shortcut, it says so in a comment, and a test in
   a higher ring covers the real path.
2. **Every escape becomes a test, at the lowest ring that can see it.** A bug found by a person, a review pass or CI gets a
   test that fails on the code before the fix. If only a high ring can see it, the fix also asks why no lower ring could.
3. **One behaviour, one statement, every surface.** A rule the page and the Mac both show is stated once, as a shared
   fixture or scenario, and both are held to it. Where the rule can move into the core, it moves.
4. **Check invariants, not only examples.** After every step of a scenario, a consistency oracle checks what must always
   hold: counts agree across surfaces, the open message is where the reader says it is, the keyboard is somewhere a person
   can see. One oracle finds what a thousand example tests each forgot to look at.
5. **Deterministic by default, adversarial on purpose.** Gating tests use a frozen clock, fake servers and no wall-time
   assertions, as [testing.md](testing.md) already requires. A separate ring is deliberately hostile: real latency,
   dropped connections, a long session, fuzzed input. It runs on a schedule, and its findings become deterministic tests.
6. **CI is the gate, not this Mac.** Everything that can run unattended runs in CI. `just verify` is a convenience for
   running the same checks before pushing.
7. **Performance and security are features.** Every budget in `budgets.toml` is read by a check, and every claim in
   [security.md](security.md) names the test that holds it.
8. **No test reaches a person's data.** The tripwire, the keychain stand-in and the kept-apart preferences hold for every
   ring, including the launched app. The Mac app under test is a build of its own, MailMind Dev, and what it would do to the system goes through seams that record it ([test-architecture.md](test-architecture.md) section 6).

## 3. The rings

Tests are arranged in rings, from the pure core outwards. A ring owns the behaviour it can see best; it doesn't repeat
what a lower ring already holds.

| Ring | What it holds | How | Runs |
| --- | --- | --- | --- |
| R0 Core logic | Pure functions and domain rules: parsing, threading, classification, the grammar, the sanitizer, time words | Rust unit tests, property tests, fuzzing; the Python oracle while Python lives | Every PR, under 2 min; fuzzing nightly |
| R1 Core contracts | What any MailMind server answers over HTTP; how the core talks to IMAP, SMTP, DNS and the AI providers | Served suites on both servers (`tests/api`), adapter contracts against the fakes and against Dovecot | Every PR (served), nightly (Dovecot, all-Rust) |
| R2 Client logic | A client's models and words over a stand-in server | Swift tests over `FakeServer`, `DeskRoutes`, `FakeRoutes`; Vitest over stubs | Every PR |
| R3 Client UI in process | Views, keys, focus, menus and windows, through the toolkit's own dispatch | A hosted Mac test target with the real app scene and menu bar; Playwright on the real build | Every PR (page), every merge (Mac) |
| R4 Whole app, scripted | A person's flows end to end against a fake world, with the consistency oracle after each step | The Mac app launched with an in-app test driver; Playwright on the page; one scenario catalogue for both | Lane A on any Mac; lane B on every merge (section 4.5) |
| R5 Hostile and long | Real latency, outages, renumbering, a long session, big worlds, fuzzed mail, exploratory review passes | Chaos and soak runs, the performance lab, agent-driven review passes | Nightly and before a release |
| R6 Release | The built artefact: signing, the update feed, upgrading from every shipped version, the oldest macOS | `release-check`, `mac-floor`, an upgrade matrix | Each release candidate |

### 3.1 R0: core logic

The core already has about 1,850 Rust tests, but no property tests, no fuzzing and no benchmarks of its own; its
property tests are Python's Hypothesis, run against the core through `mailmind_leaves`.

- **Property tests in Rust** (`proptest`) for every parser and every rule with an algebra: addresses, the search
  grammar, threading, MIME and its parameters (ROADMAP #48), ICS and recurrence, relative times, the sanitizer's
  invariants. Python's oracle tests stay until Python is retired, then their recorded answers become fixtures.
- **Fuzz targets** (`cargo-fuzz`, under `core/fuzz/`) for whatever reads bytes from outside: an IMAP response, a MIME
  message, an ICS file, HTML and CSS bodies, a List-Unsubscribe header, the search grammar, Markdown, the served protocol.
  Each target asserts more than "no panic": the sanitizer's output, for instance, must sanitize to itself. Corpora live in
  the repo; nightly runs grow them, and a crash becomes a regression test.
- **Mutation testing** (`cargo-mutants`) runs weekly on the modules that decide for a person: the classifier, needs-you,
  filing, the sanitizer, the cost governor. A surviving mutant is a missing assertion.
- **Time and locale tables.** Every time word is tested across a table of zones (UTC, London, Paris, Auckland, Los
  Angeles, Kolkata, a half-hour and a DST-edge zone) and across midnight, the DST changes and the day's word boundaries
  (21:00, 18:00). The table is one fixture, read by the Rust, Swift and TypeScript tests alike.

### 3.2 R1: core contracts

The served suites (`tests/api`, 406 tests, and the browser suite on top) are the contract any server keeps, and the
ledger holds the Rust server to it. Three changes make that contract stand on its own:

- **A world builder in Rust.** Today the `world` fixture is synced by Python's ingest, and 27 of the 42 `tests/api` files
  import `mailmind.*` to read or seed the database. A Rust `mm-world` builds the same worlds, and served tests read state
  only through routes or a test-only route behind the served protocol. Then the contract outlives the Python server.
- **Security suites on both servers.** The perimeter matrix, the walk of every POST route and the malformed-input cases
  run in-process against Python only; the Rust server has 11 perimeter tests of its own. They move into `tests/api`
  so both servers are held to one set (section 6).
- **Fakes held to real servers.** The fake IMAP server is checked against itself, and against Dovecot only when someone
  asks. The Dovecot contract runs nightly. Each server kind the fake imitates (Gmail's labels and All Mail, Proton
  Bridge) gets recorded sessions from a real server, replayed against the fake, so its kindness shows up as a diff.
  Adding latency, slow reads, `[ALERT]`s and a login cap to the fakes lets R5 reproduce real servers' timing.

### 3.3 R2: client logic

This ring is in good shape on both clients. Two gaps:

- **The page's Vitest has no DOM.** Adding `happy-dom` for the component tests that need one lets the page test its
  views without a browser, and keeps Playwright for flows.
- **Shared fixtures for twin rules.** A rule both clients compute (link risk, subject prefixes, event time words, the
  send guard, chip words) reads its cases from one JSON file in `tests/fixtures/`, as the send guard already does. A
  case added for one surface then holds the other.

### 3.4 R3: client UI in process

This is where most of the review's escapes would have been caught. The Mac test bundle compiles the app's sources into
itself and runs with no host app, so the real `MailMindApp` scenes and menu bar never exist, and the process is never
active. `Tests/RealKeys.swift` and `MailFrameTests.press` already emulate AppKit's key-equivalent step; that is the seed.

- **A hosted test target**, `MailMindAppTests`, whose host is the app itself. The app's real `Commands` build the real
  menu bar, its scenes open real windows, and tests reach them through `NSApp`. This target needs an active session, so
  it runs in lane B (section 4.5), not on a person's Mac.
- **A small test kit**, `MailMindTestKit`, shared by the hosted target and the in-app driver:
  - `press(_:)` makes a `CGEvent` from a key code under a named keyboard layout (US, Arabic, Swiss French, German),
    posts it to the app's queue and lets AppKit route it: menus first, then the key window's responder chain.
  - `click(_:)`, `drag(from:to:)` and `scroll(_:)` post real mouse events at an element found through the accessibility
    tree.
  - `keyboardIsSomewhere()` asserts the first responder is a visible view a person can type into or act on.
  - `menuItem(_:)` reads an item's state as the real menu bar validates it.
- **Production-shaped web content.** Every test that loads mail does it through the core's `mailmind-frame:` scheme with
  the headers the app serves. A test that sets up WebKit any other way fails a lint.
- **The page** already has this ring: Playwright on the real build, with console errors and CSP violations failing a test.
  It gains Playwright traces and screenshots on failure, and a nightly run in WebKit. Playwright's WebKit isn't the
  Mac's `WKWebView`, so the Mac's own frame is held by its hosted target.

### 3.5 R4: the whole app, scripted

The review passes found what they did by driving the launched app with a probe injected into it. That probe can't be
kept as it is: it swizzles private selectors, polls files with sleeps, and has hard-coded paths. What it showed is kept
in the framework [test-architecture.md](test-architecture.md) lays out:

- **MailMind Dev**, the Debug build with an identity of its own and a driver compiled in, which the runner speaks to
  over a socket. What the app would do to the system (time, the pasteboard, links, notifications, the keyboard layout)
  goes through `AppSeams`, which records it in Dev.
- **A world controller** over today's fakes: deliver mail, script faults, move one test clock, restart the app.
- **Scenarios in plain Python**, named as sentences, through screens shared by the Mac and the page where a rule is
  shared, and their own where the two differ.
- **`mm`**, the same framework as a command, so a person or an agent can drive a live app step by step, and turn what
  they did into a scenario.
- **The consistency oracle** after every step, comparing what a person sees with what the core holds, starting with
  three invariants and growing as each is specified exactly.

### 3.6 R5: hostile and long

- **Chaos.** `tests/e2e/test_chaos.py` grows into a scheduled run: random outages, drops mid-command, renumbering,
  certificate changes, an AI that times out or refuses, a full disk, all under the oracle.
- **Model-based runs.** The Hypothesis state machines over the command queue and Undo run with fresh seeds nightly
  (`MAILMIND_MACHINE_RUNS=1500`). The same idea goes up a ring: a state machine whose actions are the scenario steps,
  run against the launched app with the oracle after each step.
- **Soak.** An eight-hour run against a world that receives mail steadily, files and snoozes, with memory, handles and
  threads sampled every minute. A leak is any of them still growing over the last hours.
- **Review passes.** Agent-driven passes keep their place, with a charter each time (an area, a kind of person, a
  device size) instead of an open hunt. Their findings go through principle 2.

### 3.7 R6: release

`release-check` and `mac-floor` already check signing, the update feed's signatures, the disk image and the oldest
macOS. Added:

- **An upgrade matrix.** A database made by each shipped version (kept as fictional fixtures) opens in the new build,
  migrates, and passes the oracle. `db check-upgrade` runs the same over a copy, as an owner step.
- **An update from the previous release** through Sparkle, in lane B: the old app finds the new appcast, installs it and
  relaunches.
- **The launched-app scenarios on the release build**, not only the Debug one.

## 4. Harnesses and infrastructure

### 4.1 Fake worlds

- **One world, many sizes.** Named worlds share a generator: `tiny` (today's 25 messages over 3 accounts), `daily`
  (a few thousand, a week of a working person's mail), `large` (100,000, as `mm-fixtures` makes), `huge` (1,000,000, for
  the performance lab), plus shapes the review hit: 30 recipients, a 500-message conversation, 200 folders, a 25 MB
  attachment, every right-to-left and mixed script, a body with deep `<details>` and quotes.
- **Fixtures are fictional,** under `example.test`. The tripwire stays on in every ring.

### 4.2 Fakes that misbehave like real servers

The fakes gain knobs for what real servers do: round-trip latency, bandwidth, a login cap (Proton Bridge's), slow
literals, `NO`/`BAD`/`BYE` at any command, UIDVALIDITY changes, untagged EXPUNGEs mid-command, Gmail's label semantics,
SMTP throttling, AI rate limits and partial streams. Deterministic tests use them with fixed scripts; R5 draws them at
random.

### 4.3 The framework

[test-architecture.md](test-architecture.md) is the framework for R3 to R5: the world, the clients, `mm`, settling,
the oracle and the trace.

### 4.4 Twin fixtures

Every rule both clients compute has one fixture file of cases. Each client's test suite reads it. A new case goes in
the fixture, not in one client's test.

### 4.5 Where UI rings run: two lanes

Some of what the Mac app does needs a real, active session (the menu bar as AppKit validates it, activation, drag and
drop, notifications); the rest doesn't. So the whole-app rings run in two lanes
([test-architecture.md](test-architecture.md) section 10):

- **Lane A** on any Mac, a person's included, many at once, never taking focus: MailMind Dev as an accessory app with
  its windows off-screen, its key window made through AppKit's own path.
- **Lane B** in a real session, one app at a time: GitHub's macOS runners (throwaway VMs, sharded), and optionally a
  local VM or a spare Mac behind a lock. Scenarios that need a session are marked, and the whole catalogue runs here on
  every merge.

### 4.6 Traces and artefacts

Every failing run keeps what explains it: Playwright traces, the Mac's `.xcresult`, the scenario trace, each server's
log, and the core's event log. CI uploads them.

## 5. Performance

`budgets.toml` holds every limit, but only the core's four are enforced (by `mm-budgets`, on 100,000 messages). The Mac's,
the page's and the desktop's were measured once, by hand.

**What to measure**

| Area | Measures | World |
| --- | --- | --- |
| Start | Launch to first rows, cold and warm; first rows after the first sync | `large`, `daily` |
| The list | Frame p95 while scrolling; a jump to the end; opening a folder; grouped totals | `large`, `huge` |
| The reader | Opening a long HTML mail; WebContent memory | `daily` |
| Sync | A first scan of an account; a delta sync; IDLE wake to row | `daily`, `large` |
| Filing | Filing a 500-message conversation; Undo of it | shapes |
| Search | Query latency at 100,000 and 1,000,000 | `large`, `huge` |
| The server | p95 latency of every route on both servers | `daily` |
| Memory | Idle; after scrolling; over an eight-hour soak | soak |
| Size | The core library, the app, the page's bundle, the WASM | — |
| AI | Calls and tokens per typical day, by feature, through the fake provider's ledger | `daily` |

**Deterministic first.** Wall time varies by 10–20% from run to run on the same Mac, and more on CI. So the gating
measures are the ones a machine doesn't change: IMAP round trips per operation (counted by the fakes), database queries
per route, bytes read per sync, allocations, the number of AI calls and tokens, sizes. These fail a PR when they go over
their limit in `budgets.toml`, and they catch the slow paths the review found (a move that also SELECTs, SEARCHes and LISTs;
a first scan reading 64 KiB a message).

**Wall time on a quiet machine.** Times run nightly in the performance lab: a Mac with nothing else running,
median of seven. Results go into a history file per measure; a regression of more than the noise band against the last
fortnight's median opens an issue rather than failing a PR.

**What a missed wall-time budget does to a release.** Each wall-time entry in `budgets.toml` has its `limit` and a
`tolerance`, 10% unless the entry says otherwise (about the run-to-run noise on a quiet Mac). Before a release, every
such budget is measured in the lab:

- At or under its limit, the release goes.
- Over its limit but within the tolerance, the release still goes, and an issue is opened for the budget.
- Over the limit by more than the tolerance, the release stops until the time is back within it or a commit raises
  the limit and says why.

The deterministic counts have no tolerance: a count over its limit fails the PR.

`tests/support/budgets.py` holds this rule, and `mm-budgets` the same for the core's entries. Its unit test reads the
whole file: every entry has its fields, was within its limit when last measured, and is named by a script of its
section, or says it is measured by hand and how. Each shell's measure writes `name=value` lines, and
`just apple budgets` judges the Mac's before a release ([releasing.md](releasing.md), step 3).

**Benchmarks in the core** use `divan`, one bench per hot path: list paging, grouping, the sanitizer, MIME parsing,
search. They replace the ad-hoc ratio tests where they can.

## 6. Security

[security.md](security.md) states the model. Each claim in it gets a test, on both servers where it is a server's, and
an owner in the table below.

| Claim | Holds today | Add |
| --- | --- | --- |
| Only loopback, only same-origin, only with the CSRF token | `test_perimeter*` (Python); `perimeter.rs` (11, Rust) | Move the matrix and the route walk into `tests/api`, so both servers are held to one set |
| A request that won't read gets a 400, never a 500 | `test_malformed_input` walks every POST route (Python) | The same walk on the Rust server; a fuzzer over routes and bodies |
| The mail frame runs nothing of the mail's | Browser frame tests, sanitized and not; `MailFrameTests` | A WKWebView policy test of the Mac's configuration (JavaScript off in content, navigation decisions, the frame world); the same `wall` cases in WebKit and Chromium |
| The sanitizer removes everything active | 24 attack classes; Python oracle; Rust tests | Differential tests against browser parsers (ROADMAP #37): Chromium and WebKit parse the sanitized output, and no script, handler, `foreignObject` or form survives; a fuzz target whose output must sanitize to itself |
| Nothing hostile reaches IMAP or SMTP | `mailbox_arg`, `imap_quote`; `test_imap_wire` | Fuzzing folder names and addresses through the command builders; a lint for `f'"{folder}"'` and its Rust equal |
| Secrets stay secret | Keychain through stdin; scrubbed errors; logging tests | Canary secrets in every world (a password, an API key, a body phrase); a check over every log, error, crash report and request to the AI that no canary appears where it mustn't |
| The database is encrypted at rest | `test_db_encryption`, `database_version.rs` | Migrations over a corrupt, truncated or hostile database; a backup after an account is removed holds none of its mail (ROADMAP #60) |
| What goes to the AI is what the docs say | The governor and ledger tests; prompt wording | A prompt-injection corpus: mail that asks the assistant to file, send, forward or reveal other mail. The fake provider returns the injected action, and the test asserts nothing happens without the person and nothing outside the asked messages goes out |
| The app updates only to what the owner signed | `release-check`: the appcast and feed signatures, a tampered feed | An update from the previous release in lane B (R6) |
| The FFI boundary holds | Nothing of its own | Tests of `mm-ffi`: bad UTF-8, huge strings, calls after close, from many threads |
| The supply chain | `just audit`; `cargo deny`; pinned actions and hashes | Run `audit` in CI; secret scanning (gitleaks) in CI and as a pre-commit hook; a check that no real address or count enters the repo |

**Project rules as lints.** [CLAUDE.md](../CLAUDE.md)'s rules that a change can break without noticing become checks: no
`sqlite3` import outside the driver, no SQL in a use case, no bulk `EXPUNGE`, no folder quoted by hand, no new
environment read outside `platform.environment`, no log message that isn't a literal. A `semgrep` rule set in the repo
holds them for Python and Rust, and runs in `just lint`.

## 7. CI

CI runs three workflows. `served.yml` runs `tests/api` and the browser suite on both servers; `clients.yml` runs
`core-verify`, the core's budgets, `cargo deny`, the Mac's unit tests, its smoke run and the release check; and
`checks.yml` runs `just lint`, every Python test `just test` runs, `check-frontend`, `check-web` (the page's types,
ESLint and Vitest) and `just audit`, which also runs each Monday. Until November 2026 the third didn't exist, and those
ran only when someone ran `just verify` on their own Mac. **Still no workflow runs** the Dovecot contract or
`test-rust-all`; both join the nightly tier.

The target:

| Tier | When | What | Time |
| --- | --- | --- | --- |
| PR | Every push to a PR | `lint`, `audit`, `check-web`, `core-verify`, `just test`, the Mac unit tests (R2), served suites on both servers, deterministic budgets | Under 20 min, in parallel jobs |
| Merge | Every push to main | The PR tier, plus the browser suite in WebKit, the hosted Mac target (R3) and the scenario catalogue (R4) in lane B | Under 45 min |
| Nightly | Once a day | Fuzzing, mutation sample, Hypothesis with fresh seeds, Dovecot, `test-rust-all`, chaos, the performance lab, a soak once a week | Hours |
| Release | Each candidate | R6, every shell budget, every scenario on the release build | — |

**Flakes.** A test that fails and then passes is a bug, in the test or the code. It is never retried automatically in a
gating tier. It gets a `flaky` marker naming an issue and a date the marker lapses on, and runs in a quarantine job until
it is fixed. Every test gets a timeout (`pytest-timeout`, Rust's per-test timeout, Playwright's), and CI reports each
suite's slowest tests so they don't creep.

**Coverage** is measured, not targeted: `cargo llvm-cov`, `coverage.py`, Vitest's coverage and Xcode's, merged into one
report per merge. It points at code no test reaches; mutation testing says whether the tests that reach it would notice
a change.

## 8. Features and their rings

Each feature lists the rings that must hold it. A change to a feature runs at least those.

| Feature | R0 | R1 | R2 | R3 | R4 | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Accounts: add, edit, remove, credentials, status | • | • | • | • | • | Every credential kind kept by an edit; Proton |
| Sync, IDLE, recovery | • | • | | | • | R5 chaos |
| The list: places, scopes, tabs, conversations, counts | • | • | • | • | • | The oracle's counts |
| Reading: the frame, links, Find, print, Quick Look | • | • | • | • | • | Under the real CSP, in WebKit |
| Filing, moving, deleting, Undo | • | • | • | • | • | Every entry point × row shape |
| Search and saved views | • | • | • | | • | |
| Curation: the Screener, rules, sweeps, unsubscribe | • | • | • | • | • | |
| Attention: snooze, mute, reply later, follow-ups, Waiting | • | • | • | | • | Time tables |
| Calendar: invites, recurrence, RSVP, Today & next | • | • | • | | • | Time tables |
| Compose: drafts, signatures, recipients, Send Later, the outbox | • | • | • | • | • | Typed words never lost |
| The assistant: summaries, the brief, Your Week, ask and do | • | • | • | • | • | Injection corpus; cost |
| Menus, keys, windows, focus | | | | • | • | Every layout |
| Settings | | • | • | • | • | |
| Notifications, Dock badge, open at login | | | • | • | • | Lane B |
| Updates, packaging | | | | | | R6 |

## 9. Building it

In order, each step useful on its own:

1. **CI runs what exists.** Add the PR tier's missing jobs: `just test`, `just lint`, `just audit`, `check-web`. Fix the
   stale lines this survey found: `served.yml`'s `--ignore` of a folder that doesn't exist; [testing.md](testing.md) on
   `it.fails` (none are used) and on `just verify` leaving browser tests out (it runs them against the Rust server);
   `property_runs.py`'s path. Add `pytest-timeout` and failure artefacts.
2. **Budgets read.** A check reads every entry of `budgets.toml`; the deterministic counts join it; the shell entries
   run in the performance lab before a release.
3. **The Mac test kit and hosted target (R3).** Move `RealKeys` and `MailFrameTests.press` into the kit, add layouts,
   mouse events and `keyboardIsSomewhere`. Turn every key, menu and focus finding of the review into a hosted test.
4. **Lane B.** The hosted target and `@needs_session` scenarios on CI's macOS runners, sharded.
5. **The end-to-end framework (R4),** in the order [test-architecture.md](test-architecture.md) section 11 gives: `mm`
   and the page first, then the Mac's seams, the core's test clock in process, the driver, the oracle. Start with the
   flows the review walked most: first run, triage of Needs you, reply and reply all, Send Later, filing and Undo, two
   windows.
6. **Security breadth.** Perimeter suites on both servers, the sanitizer's differential tests, fuzz targets, canary
   secrets, the injection corpus, the FFI tests, secret scanning, the project-rule lints.
7. **The performance lab.** Worlds up to `huge`, `divan` benches, nightly timings with history, the soak.
8. **The contract stands alone.** The Rust world builder; served tests that import no Python; the oracle's answers
   recorded as fixtures for when Python goes.

**How we know it works.** The measure is escapes: how many high and medium bugs a charted review pass or a person
finds that no ring caught, per pass and per release. It should fall pass by pass. Alongside it: the time a PR waits for
CI, the flake count, and the number of budgets and security claims with a check behind them.

## 10. Decisions for the owner

[test-architecture.md](test-architecture.md) settles three questions this strategy first left open:

- **Where the UI rings run:** lane A on the host for everyday work, lane B on CI's macOS runners; a local VM is optional.
  This Mac's free disk doesn't allow a VM per run, and CI's runners can't run one inside themselves.
- **The driver or XCUITest:** an in-app driver. XCUITest needs the app active, can't read the core at the moment the app
  settles, and is slow.
- **The scenario format:** plain Python on pytest, not Gherkin; the readable catalogue is written from what ran.

The fourth was the owner's: **a missed wall-time budget** is fine within a reasonable margin and stops the release
beyond it (section 5: a 10% tolerance by default).
