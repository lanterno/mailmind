# The end-to-end test framework

[test-strategy.md](test-strategy.md) says what to test and at which ring. This document is the framework that runs
the whole-app rings (R3 to R5): its parts, how they connect, how it keeps time and knows when the app is quiet, how it
stays away from a person's data, and what a test looks like. Lower rings keep the tools they have (`cargo test`,
pytest's unit and served suites, XCTest, Vitest).

It is written for the people who will use it most: coding agents working in parallel worktrees on a Mac someone is
using, and the owner. So it puts three things first. The framework is safe by construction, not by a brief. It can be
driven by hand, a command at a time, as well as by tests. And it answers in text first.

## 1. The shape

```mermaid
flowchart LR
    subgraph Runner["mmtest: pytest plugin and the mm command"]
        T[Scenario] --> S[Screens]
        CLI[mm shell] --> S
        S --> C{Client}
        C -->|MacClient| MD[Driver link]
        C -->|PageClient| PW[Playwright]
        O[Oracle]
        TR[Trace]
    end

    subgraph World["World"]
        W[World controller] --> F[Fake IMAP, SMTP,<br/>DNS, Claude, OpenAI]
        W --> CLK[Test clock]
    end

    subgraph Mac["MailMind Dev.app"]
        DRV[Driver] --> UI[AppKit + SwiftUI]
        UI --> SEAMS[AppSeams:<br/>clock, pasteboard, links,<br/>notifications, layout]
        UI --> CORE[Core in process,<br/>test clock, paused jobs]
    end

    subgraph Web["Chromium"]
        PAGE[The page] --> SRV[mailmind-server]
    end

    MD <-->|socket, JSON lines| DRV
    PW --> PAGE
    T & CLI --> W
    CORE & SRV --> F
```

| Part | What it is | Where |
| --- | --- | --- |
| `mmtest` | The framework: the world, the clients, the screens, settling, the oracle and the trace, as a pytest plugin | `tests/framework/mmtest/` |
| `mm` | The same framework as a command, for driving a live app by hand (section 3) | `tests/framework/mm`, `just mm` |
| Scenarios | The tests, by feature | `tests/scenarios/<feature>/test_*.py` |
| World controller | The fake servers, the corpus, delivery, faults and the test clock | `tests/framework/mmtest/world.py`, over `tests/fakes/` |
| MailMind Dev | The Debug build of the Mac app, with its own identity and the driver compiled in (section 6) | `shells/apple`, Debug configuration |
| Driver | Carries out the runner's commands inside the app | `shells/apple/Sources/Dev/Driver/` |
| `AppSeams` | Every way the Mac app reaches the system, behind one seam, as the core's `Seams` does | `shells/apple/Sources/Seams/` |
| `MailMindTestKit` | Real input (keys under a layout, mouse, menus), lookup and settling in Swift, shared by the driver and the XCTest targets | `shells/apple/TestKit/` |
| Page client | Playwright on the real build, served by `mailmind-server` | `tests/framework/mmtest/page.py` |

## 2. A test

```python
from mmtest import clients, mail

@clients("mac", "page")
def test_words_typed_in_a_reply_all_go_above_the_signature(world, app):
    world.account("ada").signature = "Ada at the Guild"
    note = world.deliver(mail.from_("lin").to(6).cc(4).subject("Venue"))

    app.inbox.row(subject="Venue").open()
    composer = app.reader.reply_all()
    composer.type("Got it, thanks.")
    assert composer.send_button.visible
    composer.send()

    sent = world.smtp.wait_for_one()
    assert sent.text.startswith("Got it, thanks.\n\n-- \nAda at the Guild")
    assert sent.recipients == note.reply_all_recipients(me="ada")
```

Around each line the framework:

1. Turns the action into real input through the client (section 5).
2. Settles: waits until the app, the core and the world are quiet, moving the test clock through short delays (section 7).
3. Runs the oracle, which fails the test at the step that broke an invariant (section 8).
4. Adds a step to the trace, written out only when the test fails (section 9).

`@clients("mac", "page")` runs the test on both. Only a scenario that holds a rule both clients share runs on both;
where the two are meant to differ (own windows, menus, the page's tabs), each client has its own scenarios.

**Why plain Python, not Gherkin.** pytest-bdd would put feature files and step patterns between the test and the
screens. A failure would then point into a step function matched by a pattern, and agents would hunt for the pattern
before the bug. It would also give up the loops, helpers and Hypothesis the scenarios need. The readability Gherkin
promises comes another way: each test is named as a sentence, each screen method carries the phrase a person would use,
and `just e2e catalogue` writes the readable list from what actually ran (section 9), so it never goes stale.

## 3. `mm`: driving a live app by hand

The review found its bugs by exploring the app, a command at a time, through a probe it had to build itself. The
framework gives that to everyone, with the same verbs the tests use:

```
$ just mm up mac --world daily          # a world, MailMind Dev on it, and a prompt
mm> shown                               # what's on screen, as text
window "Inbox — All accounts" (key)
  rail: [Inbox 9] [Needs you 4] [Waiting 2] …
  list.tab: Needs you* · Everything · Unread
  list: 4 rows, chosen 1
    > Lin Mori — Venue · 2m ago · needs you
      Pell Bank — Your statement · 1h ago
  reader: "Venue" from Lin Mori, 3 in thread
  keyboard: list
mm> press e
mm> shown --diff                        # only what changed
mm> deliver long --from noor
mm> clock +7h
mm> truth digest
mm> picture                             # a PNG path
mm> save-step "archive the first row"   # appends the step to a draft scenario
```

- `mm up mac|page` starts a world and a client and leaves them running; `mm down` stops them. `mm attach` reconnects.
- Every test takes `--keep`: when it fails, it leaves the world and the app up and prints the `mm attach` line.
- Every failure prints a one-line repro: `just e2e tests/scenarios/compose/test_reply.py::test_words_… --client mac`.
- `mm` replaces the review's file-polled probe and its control scripts. Review passes and scenarios use one driver.

## 4. The world

```python
class World:
    accounts: dict[str, Account]      # ada (IMAP), bea (Proton Bridge), cy (Gmail)
    imap: FakeIMAP; smtp: FakeSMTP; dns: FakeDNS; ai: FakeAI
    clock: TestClock

    def deliver(self, message, to="ada", folder="INBOX") -> Delivered
    def corpus(self, name)            # tiny | daily | large, or a named shape
    def fault(self, script)           # imap.down(), imap.drop_after("UID MOVE"), smtp.refuse(), ai.slow(12)…
    def restart_app(self, zone=None)  # the same home, a new process; a new time zone is a restart, never a system change
```

- **A world is a home.** A run's data directory, its keychain stand-in, its database key and its preferences live in
  one temp folder. A named corpus is built once per build and copied, so a scenario starts in a second, not after a
  sync.
- **Faults are scripts:** what fails, when, how, for how long on the test clock. Deterministic scenarios script them;
  chaos runs draw them at random from the same list.
- **The page's server is `mailmind-server`.** The Mac app runs only the Rust core, and the Python server is retired
  after the flip, so scenarios don't run twice for two servers. The served suites (`tests/api` and the ledger) keep the
  Python server honest until it goes.

## 5. The clients

Both clients implement one interface. Screens call only this interface.

```python
class Client(Protocol):
    def press(self, keys: str, layout: str = "us") -> None   # "e", "cmd+shift+f", "pagedown"
    def type(self, text: str) -> None
    def click(self, target: Element, count: int = 1) -> None
    def drag(self, source: Element, onto: Element) -> None
    def choose_menu(self, *path: str) -> None
    def find(self, role: str, label: str | None = None, within: str | None = None, **where) -> Element
    def shown(self) -> Shown          # what a person sees (section 8)
    def truth(self) -> Truth          # what the core holds (section 8)
    def picture(self) -> Path
    def settle(self) -> None
```

### 5.1 Finding things

Elements are found the way a person and VoiceOver find them: by **role and label, within a named region**.
`find("button", "Send", within="composer")`, `find("row", within="list", subject="Venue")`. Both clients already carry
most labels: about 270 accessibility labels in the Mac's sources and about 110 `aria-label`s in the page.

Only about thirty structural anchors get an identifier: the regions (`rail`, `list`, `reader`, `composer`, `brief`,
`toast`, `sheet`), the list's rows, the tabs, and parts nothing labels. They're kept in `tests/framework/anchors.toml`,
as `accessibilityIdentifier` on the Mac and `data-mm` on the page. A lint holds them once they stop changing. Every
other lookup is by role and label, so adding a test seldom means changing the app. And when a control has no label, the
fix is the one VoiceOver needed anyway.

### 5.2 The Mac client

The runner makes a socket in a short temp path (a Unix socket path can't exceed 104 bytes, and agents' homes are
longer). It starts MailMind Dev with `MAILMIND_TEST_DRIVER` naming the socket, and the driver connects out to it. The
link speaks JSON lines:

```json
{"id": 7, "do": "press", "keys": "e", "layout": "us"}
{"id": 7, "ok": true, "settled_at": "run-4:212"}
```

| Command | What the driver does |
| --- | --- |
| `press`, `type` | Builds the key from a key code with `UCKeyTranslate` under the named layout (the layout seam, section 6), posts it to the app's own queue and lets AppKit route it: menus first, then the key window's responder chain |
| `click`, `drag`, `scroll` | Posts mouse events at the element's visible frame |
| `menu` | Finds the item in the real menu bar, validates it, performs it; fails if it's disabled |
| `find`, `shown` | Reads the app's own accessibility tree, plus visibility (section 8) |
| `picture` | ScreenCaptureKit in a real session; otherwise the app draws itself, marked approximate |
| `settle` | Section 7 |
| `core` | The core's test clock, its paused jobs (`work`, `kicked`), `check`, and reads for `truth` |

The driver runs its own work off the main thread, except for accessibility reads and posting events. `MailMindTestKit`
holds the input code, so the hosted XCTest target sends keys exactly as the driver does.

### 5.3 The page client

Playwright on the real build, in Chromium, served by `mailmind-server`. `find` maps to `get_by_role` within a
`data-mm` region, `shown` reads the same text snapshot from the DOM (Playwright's aria snapshot, trimmed to the same
shape), and the page's timers run on `page.clock`, driven by the same test clock. WebKit runs nightly: Playwright's
WebKit isn't the Mac's `WKWebView`, so the Mac's own frame is held by its hosted target and the session lane.

## 6. Safe by construction

Every rule agents had to be told becomes something the code enforces.

| Rule agents were told | What enforces it |
| --- | --- |
| Never touch the person's MailMind, its preferences or its saved state | The Debug build is **MailMind Dev**, `io.github.lanterno.mailmind.dev`, executable `MailMindDev`: its own preferences, saved state, login item, notification permission and `mailto:` registration. Release keeps the real identity |
| Launch only with a temp home, a keychain stand-in and a key of its own | The driver refuses to start unless the home is under the system's temp folder, the keychain is the stand-in, and the identity is Dev's |
| Kill only your own processes | The runner starts the app and the fakes in its own process group, writes their pids into the home, and stops only those |
| Never change the clock or the time zone | The app reads time through `AppSeams.clock`; a zone is set with `TZ` at launch |
| Never change the keyboard layout | `AppSeams.layout`: `SingleKey` asks the seam, not the system, which layout is current |
| Save and restore the clipboard | `AppSeams.pasteboard`: a named pasteboard per run |
| Never open a link in a real browser | `AppSeams.links`: recorded, never opened; the oracle can read them |
| Never post a notification as the person's app | `AppSeams.notifications` and `AppSeams.loginItems`: recorded |
| Never reach a real server, AI or model server | The tripwire and the fakes, as today, in every process the runner starts |

`AppSeams` mirrors the core's `Seams`. The shipped app gets the system's versions; MailMind Dev under a driver gets the
test ones. The Mac sources hold about 45 `Date()` calls, 36 `Task.sleep`s, 5 uses of the general pasteboard and 11
`NSWorkspace` opens today; moving them behind the seam is work of its own, done once (section 11, step 2).

`release-check` fails if the shipped app holds the driver, the test seams, or the Dev identity.

## 7. Time and settling

**One test clock.** The world, the core, the Mac app and the page read one clock, frozen until a test moves it:

- The core reads it through its `Clock` seam. In the Mac's in-process core that needs `mm-host` to take a test clock and
  expose `mm-served`'s job pumping (`work`, `kicked`, `settle`, `check`), moved into a crate both use.
- The Mac app reads it through `AppSeams.clock`, which also schedules every delay: `after(seconds) { … }` in place of
  `Task.sleep`.
- The page reads it through `page.clock`.

**Background jobs are paused by default**, as in the served suites: a scenario pumps them (`world.run("sync")`) or opts
in to free running (`@free_running`), which the chaos and soak runs use.

**Delays have two kinds.** A short delay, two seconds or less (a debounce, coalescing events, the 400 ms wait before a
digest is read again), counts as the app being busy, and `settle` moves the test clock through it at once. A long one
(a toast's life, autosave, Undo Send's window, a snooze) moves only when the test says `world.clock.advance(...)`. So a
test never waits for a short delay and never misses a long one.

**Settled means all of these hold at once, checked until they stay true:**

1. Every client subscription has applied every core event up to the core's high-water id. The core's events already
   carry ids (`run-n`, in `mm-server/src/events.rs`), and each subscriber reports the last one it applied.
2. Nothing is in flight: no route call from the Mac's `RouteCaller`, `Wire` or `Routes`, no request on the page.
3. No short delay is pending.
4. No paused job was kicked and left unpumped, unless the scenario is free running.
5. The world's fakes have no command in flight.

`Truth` is read at that same mark. A step that can't settle within its ceiling fails with what was still busy.

## 8. The oracle

After every step the runner takes two readings:

- **`Shown`**, what a person sees, read from the accessibility tree or the DOM, never from the app's models: the
  window and place, the counts on screen, the rows in view, the row chosen, the message open, where the keyboard is, any
  toast, sheet or empty-state sentence. **Visible** means the element's frame meets the window's visible content and
  nothing covers it. For a mail body, it also means the document's height fits what its view can scroll to, which is
  what "a long message cut off" broke.
- **`Truth`**, what the core holds, read at the settled mark through one adapter. Today it reads routes; when the
  page's step 5 moves views into the core, only the adapter changes.

It starts with three invariants, each sharp enough never to need relaxing:

```python
@invariant
def the_keyboard_is_somewhere(shown):
    expect(shown.focus is not None and shown.focus.visible, "nothing visible has the keyboard")

@invariant
def typed_words_are_kept(shown, truth, history):
    for words in history.typed:
        expect(words in shown.composers or words in truth.drafts or words in truth.sent, f"{words!r} is nowhere")

@invariant
def the_open_message_is_where_it_says(shown, truth):
    if shown.open:
        expect(shown.open.key in truth.place_rows or shown.reader.says_where, "the reader holds a message the list doesn't")
```

**Counts agree** (the rail, the tabs, the brief and the core) joins once its rules are written down exactly: which
counts are of messages and which of conversations, which follow the scope and which don't. A vague version would train
everyone to switch it off. After it come "no sentence contradicts the state" and "nothing shows a message the core has
moved".

An invariant can be relaxed for one step that is about a lag on purpose, never for a whole test.

## 9. Output

- **Text first.** `shown` is a text tree. A failed invariant prints both readings and the difference. The trace is
  JSON lines with a text summary per step: the step, its phrase, the requests the fakes saw, the core events, the
  diff of `Shown`. Pictures and the HTML viewer come second.
- **Where.** A failing test writes `build/traces/<test>/`; CI uploads it.
- **Budgets per step.** Settling and the oracle are timed per step, and the slowest are reported, as the slowest
  tests are.
- **The catalogue.** `just e2e catalogue` writes `build/catalogue.md` from the tests that ran: each test's sentence and
  its steps' phrases, by feature. That is the readable specification, and it can't drift from the tests.
- **By feature.** Scenarios are marked by feature (`-m compose`), matching [test-strategy.md](test-strategy.md)
  section 8, so a change runs its own area first.

## 10. Where it runs: two lanes

A Mac app needs a real, active session for some things (the real menu bar as AppKit validates it, activation,
drag and drop, notifications, ScreenCaptureKit) and not for the rest. Only one app is active in a session at a time,
and this Mac has neither the disk nor the use for a VM per agent. So there are two lanes.

```mermaid
flowchart TB
    subgraph A["Lane A: on the host, in parallel, never takes focus"]
        A1[Agent worktree 1: MailMind Dev, world, runner]
        A2[Agent worktree 2: …]
        A3[The page: Chromium, mailmind-server]
    end
    subgraph B["Lane B: a real session, one app at a time"]
        B1[CI: GitHub's macOS runners, sharded]
        B2[Optional: a local VM or a spare Mac, behind a lock]
    end
    A -->|"@needs_session scenarios"| B
```

- **Lane A, the default.** Runs on the person's Mac beside their work, any number at once. MailMind Dev runs as an
  accessory app with its windows off-screen. In Dev under a driver, an `NSApplication` subclass answers `isActive` and
  `keyWindow` from the window the driver made key through AppKit's own `makeKeyWindow`. That's what the review's probe
  did, without swizzling. A result from lane A says it can't vouch for activation, the real menu bar or a drag.
- **Lane B, the gate.** Scenarios marked `@needs_session` (menus, activation, drag and drop, notifications, pictures)
  and the whole catalogue on merges. It runs on GitHub's macOS runners, which are throwaway VMs with an unlocked
  session, and free for a public repository, so the catalogue is sharded across several. They can't run a VM inside
  themselves, so the app runs on them directly. Locally, a VM (`tart`) or a spare Mac can serve lane B behind a lock;
  `just e2e --session` waits its turn and says where it is in the queue.
- **The page** runs in lane A anywhere, Linux in CI included.
- **Builds are reused.** One Debug build per worktree serves every run; `just e2e` rebuilds only what changed and never
  makes a DerivedData of its own.

## 11. Building it

Each step is useful on its own, and the first ones pay off before the Mac driver exists.

1. **`mm` and the page.** `mmtest` with the world over today's fakes, the page client on `mailmind-server`, the text
   `shown`, settling on event ids and network quiet, the trace, `--keep`, and `mm up page`. Port a handful of browser
   scenarios.
2. **The Mac's seams.** `AppSeams` with the clock and its scheduler first (every `Date()` and `Task.sleep`), then the
   pasteboard, links, notifications, login items and the layout. The Debug build becomes MailMind Dev.
3. **The core's test clock and jobs in process.** `mm-host` takes a test clock and pauses and pumps jobs, from a crate
   shared with `mm-served`.
4. **The driver, lane A.** `MailMindTestKit` from `Tests/RealKeys.swift` and `MailFrameTests.press`; the driver's link,
   input, `find`, `shown`, `settle`, `core`; `mm up mac`. The first scenarios are the review's findings in the list,
   triage, the composer and the reader.
5. **The oracle's three invariants**, run after every step.
6. **Lane B on CI**, sharded, with `@needs_session` scenarios and the catalogue on merges.
7. **Anchors and screens, area by area**, porting the review's findings as scenarios as each area gets its screen.
8. **Then:** the counts invariant once its rules are written; a model-based machine over screens; chaos with random
   faults; the soak; a local lane B.

**Decisions this takes.** It answers the first three questions in [test-strategy.md](test-strategy.md) section 10:

- Lane A on the host for everyday work, and lane B on CI's macOS runners, rather than a VM for every run. A local VM is
  optional.
- An in-app driver, not XCUITest: XCUITest needs the app active, can't read the core at the settled mark, and is slow.
- Scenarios in plain Python on pytest, not Gherkin, for the reasons in section 2.

A missed wall-time budget stops a release only beyond its tolerance ([test-strategy.md](test-strategy.md) section 5).
