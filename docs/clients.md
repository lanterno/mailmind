# Clients

MailMind's clients are one Rust library and a shell per platform. The design is the "MailMind client
architecture" document; this page says what is built, how the pieces talk, and what each step found.

| Where | What it is | Read |
| --- | --- | --- |
| `core/` | The Rust core: the encrypted store, the contract (`Session`, `ListView`), its carriers | [core/CLAUDE.md](../core/CLAUDE.md) |
| `shells/apple/` | SwiftUI for iPhone, iPad and Mac, over UniFFI's Swift | [shells/apple/CLAUDE.md](../shells/apple/CLAUDE.md) |
| `shells/android/` | Compose, over UniFFI's Kotlin | [shells/android/CLAUDE.md](../shells/android/CLAUDE.md) |
| `shells/web/` | React, over the core compiled to WebAssembly in a dedicated worker | [shells/web/CLAUDE.md](../shells/web/CLAUDE.md) |
| `shells/desktop/` | Tauri around the web shell's page, over the core running natively | [shells/desktop/CLAUDE.md](../shells/desktop/CLAUDE.md) |
| `budgets.toml` | The limits every client is held to, and what was measured | |

The Python app in `mailmind/` stays what runs until the clients replace it. Until then both make and upgrade the same
encrypted files (a raw 32-byte key, SQLCipher 4's defaults), migration for migration ([migrations.md](migrations.md)).

## One contract, three carriers

Everything two platforms would have to agree on lives in the core, and a shell only shows what the core sends.
The contract is `mm_app`'s, and it reaches a shell one of three ways:

- **UniFFI** (`core/crates/mm-ffi`) for Swift and Kotlin. `just gen-bindings` writes both; CI fails when the
  committed bindings differ from the core.
- **JSON lines** (`mm_app::wire`) for the browser's worker (`mm-wasm`), the desktop's Tauri channel and the daemon
  (`mm-daemon`). Their TypeScript comes from the core through ts-rs (`just web gen`).
- A contract change starts in `mm-app`, then reaches every carrier in the same change.

## How a list works

A shell opens a list with a query and says which rows are visible; the core answers with batches.

- `set_visible(first, count)` is all a shell sends. The core holds a range from one page (50 rows) before the
  visible rows to one page after, and sends only the rows newly inside it, so scrolling within a page sends nothing.
- Each `ViewBatch` names the held range. The shell keeps the rows inside it and drops the rest, so its memory does
  not grow with the mailbox.
- Calls coalesce: while a batch is being made, only the newest window is kept, and a jump never serves the windows
  it passed through.
- Rows carry the message key (`message_identity.key`), never a row number. Refusals are values whose words never
  hold a person's data.
- Native shells serve views on a thread of the core's own (`workers: 1`); a batch reaches the shell's UI thread
  through its observer. The browser's worker has one thread, and serves views on it (`workers: 0`).

## Step 0: the walking skeleton

One message list from a 100,000-message fictional mailbox (`mm-fixtures`, every address at example.test), on
every target, with the budgets measured. Measured on an Apple silicon Mac (M5, macOS 26.4), 2026-11-12 and 13, on
a machine shared with other builds, so numbers vary by 10 to 20% run to run.

| Target | Status |
| --- | --- |
| Mac, native table | Runs and scrolls; measured |
| Mac, Tauri | Runs and scrolls; measured |
| Browser (Chromium) | Runs and scrolls; measured, and scrolled in CI |
| iPhone and iPad | Builds for device and simulator; CI's smoke test scrolls it in an iPhone simulator, not yet run |
| Android | Runs and scrolls on CI's emulator (API 29, x86_64); no SDK here, not measured |
| Windows and Linux, Tauri | Run and scroll in CI; not measured |

| What | Measured | Limit |
| --- | --- | --- |
| Core: open and first rows of 100,000 | 10.7 ms | 150 ms |
| Core: idle memory | 3.5 MB | 40 MB |
| Core: next page while scrolling, p95 | 0.05 ms | 3 ms |
| Mac: launch to first rows | 218 ms | 800 ms |
| Mac: memory idle, and after scrolling all 100,000 | 28 MB, 41 MB | 200 MB |
| Mac: main thread per frame, p95, fast scroll, whole-list sweep and far jumps | 3.3, 5.9, 5.8 ms | 8 ms |
| Mac (Tauri): launch to first rows, screen locked | 724 ms | 800 ms |
| Mac (Tauri): memory idle, app and its web view's processes, screen locked | 83.9 MB | 200 MB |
| Browser: navigation to first rows | 99 ms | 800 ms |
| Browser: the core as WebAssembly, Brotli | 0.51 MB | 2.5 MB |
| Apple: what the core adds to a stripped app | 1.61 MB | 8 MB |

What the numbers say:

- **Ordering the inbox was nearly all of opening it.** Version 50's indexes left SQLite sorting all 100,000 rows
  before the first page: 122 ms natively and 1,021 ms in the browser, where every page is decrypted without AES
  instructions. Version 51 adds `idx_msg_listed`, `messages(folder, ts DESC, pk DESC, account) WHERE
  pending_removal IS NULL`, the order a list shows, so the first page is the first rows read: 10.7 ms natively,
  99 ms in the browser. The Python app's combined inbox gains the same, about 120 ms to under 1 ms a page.
- **A native table keeps the Mac within a frame; SwiftUI's `List` did not.** The list is an `NSTableView` on the Mac
  and a `UICollectionView` on iPhone and iPad, each with one row height, behind a thin representable. Deep in the
  list it takes 5.9 ms a frame at p95 and 41 MB after scrolling everything; the `List` took 19.6 ms and 116 MB under
  the same conditions. Both were measured held awake: behind a locked screen macOS naps an app to about half speed,
  which is where the first recorded 296 ms came from. The iOS table has not run here: there is no simulator runtime.
- **The crossing is not a cost.** A 50-row batch through UniFFI takes about 85 µs, about 1% of a frame.

## Steps 1 and 2: the contract frozen, the leaves ported

**Step 1.** The golden suites and the browser tests run against the server as a program
(`tests/support/served.py`, `serve_python.py`): they reach it only through HTTP and a line protocol that sets its
clock and runs its jobs (`work outbox`, `work sync`, `settle`), so a Rust server can be held to the same tests. The
mail frame's tests take the sanitizer away through it too (`unsanitized on`), to show the sandbox holds alone.

**Step 2.** The pure functions the Python app calls now run in the core, through `mailmind_leaves`
(`core/crates/mm-py`, PyO3, built by maturin as a uv path dependency, so `uv run` builds it):

| Leaf | Crate | Python module that calls it |
| --- | --- | --- |
| A message's identity | `mm-kernel` | `mailbox.domain.identity` |
| The search grammar | `mm-discovery` | `discovery.domain.query` |
| The classifier and its rule sets | `mm-curation` | `curation.domain.classifier`, `rulebook` |
| Threading | `mm-mailbox` | `mailbox.domain.threading` |
| MIME | `mm-content` (mail-parser) | `content.mime.message` |
| The sanitizer | `mm-content` (html5ever's tokenizer) | `content.sanitize.html` |

Each Python module keeps its API, and its tests pass unchanged but where the port fixed a bug. Each leaf has a
`test_<leaf>_in_rust.py` that keeps the Python it replaced as the oracle and holds the core to it with Hypothesis
(see [testing.md](testing.md)). The port found and fixed: two conversations that shared a thread staying apart;
classifier patterns matching inside longer words; a forwarded email's attachment cut short; CSS a browser read
otherwise than the sanitizer had checked it, so that a message could make its frame's elements `position: fixed`;
and an `<embed>` or an unclosed `<head>` taking the rest of a message with it.

## Step 3: the Rust server, a route at a time

`core/crates/mm-server` is the server the Python app becomes, built a slice at a time and held to the served suites
(`tests/api`, `tests/e2e/browser`) through `core/crates/mm-served`. A test it passes goes on the ledger,
`tests/support/rust_ledger.txt`, and `just test-rust` runs those against it (see [testing.md](testing.md)).

The first slice was the perimeter, ported check for check with the same statuses and problem documents, the page and
its files, `/api/accounts` and `/api/folders`. The second, four areas side by side, brought the Mailbox's reads, a
message opened, search, rules and the Screener, the local states (snooze, mute, follow-ups, Reply Later, tasks), and
IMAP with the sync and the change queue: on 2026-11-13 the server answered 64 of the Python app's 103 routes, and the
ledger held 44 served tests. Any other route answers as no route does. How a slice is built:

- **A use case goes in its context's crate** (`mm-mailbox`, `mm-discovery`, `mm-curation`, `mm-attention`,
  `mm-compose`, `mm-accounts`), with what it needs of the world as traits in that crate's `ports`. Its SQL goes in
  `mm-store`, in a module named for the context. A network protocol has a crate of its own (`mm-imap`), on `mm-net`,
  which holds the TCP connection and the TLS every protocol shares.
- **One write block is `Database::write`**, BEGIN IMMEDIATE as `UnitOfWork.write` is; `Database::read` never writes.
  The documents a context keeps whole and the undo stack are `mm_store::blocks`.
- **A route is a plain `async fn` on a literal path** in `mm-server/src/routes/<context>.rs`, merged once in
  `routes::router` (axum refuses a path routed twice when the server starts). It reads a query with `query::Query` and
  a POST's body with `body::read`, runs the use case through `routes::run` (on the blocking pool, off the async
  threads) and answers its value as JSON or its refusal as a problem document. A route that answers only some of what
  the Python one does answers the rest with `answer::not_yet`, a 501.
- **A change to mail on a server goes through `MailCommands`** (`mm_mailbox::commands`), and IMAP work through the
  account's gate (`mm_mailbox::gates`), as the Python app's `mailbox.commands` and `gates.run` do. The page hears of a
  change on the event stream (`mm-server/src/events.rs`).
- **Contexts meet in `mm-server`**: `wiring`, `sync_wiring` and `bound` adapt one context's use case to another's
  port, and `App::assemble` builds them all.
- **A test reaches the server only through `Seams`** (`Clock`, `Outside`, `Keychain`), which `mm-served` fills with
  the frozen clock, the tripwire and the fake Keychain. The server has no test hooks; the served suites' `work` line
  runs one pass of a job (`mm-served/src/jobs.rs`), and one not ported yet answers `failed not on the Rust server yet`.

## The six questions

1. **Does a SQLCipher file written natively open unchanged in the browser?** Not as written: native builds use a
   write-ahead log, which the browser's VFS (no shared memory) cannot open. The same file in rollback-journal mode
   opens byte for byte. It blocks nothing: the browser is a cache filled over JMAP, never handed a native file.
2. **Which JMAP library compiles cleanly to WebAssembly?** io-jmap 0.7.0, with no shims, for about 122 KB Brotli.
   jmap-client 0.4.3 does not: it calls reqwest builder methods that exist only natively.
3. **What does a crossing cost, and does BoltFFI do better?** UniFFI 0.32.2: a call about 3.5 ns, a 50-row batch
   80 to 93 µs (Mac). BoltFFI carries the batch about 11 times faster (about 8 µs), but its Swift fails Swift 6's
   concurrency checks and its classes are not `Sendable`. Both are about 1% of a frame, so UniFFI stays. Phones are
   unmeasured.
4. **How large is the core per architecture?** On Apple, 1.61 MB added to a stripped app (SQLCipher's C is 1.09 MB
   of it); 3.7 MB with TLS (rustls, aws-lc) and OpenPGP key parsing; 5.0 MB with a full OpenPGP round trip. The
   WebAssembly is 1.61 MB raw, 0.51 MB Brotli. Android's per-ABI sizes and the Windows and Linux binaries come from
   CI's run summaries.
5. **How much memory does a system web view use for one HTML message?** On the Mac, WKWebView starts about 24 MB of
   WebContent, 5.6 MB of Networking and 16 MB of GPU process for one 50 KB message. iOS, Android's WebView,
   WebView2 and WebKitGTK are unmeasured.
6. **Does Stalwart let a browser in from one named origin, or only any origin?** Only any origin: its permissive
   CORS answers `*`. One named origin works only through static response headers, which cannot serve two origins.

CI runs a check that each list opens and scrolls: Chromium jumps nine tenths down, the desktop app scrolls itself
(`just desktop smoke`) on macOS, Windows and Linux, the Apple app does on the Mac and in an iPhone simulator
(`just apple smoke`), and Android's instrumented test jumps to row 90,000 and the last on an emulator. Each waits for
mail in the rows it lands on, not just for the scroll.

## Commands

```bash
just core-verify          # the core: format, clippy as errors, every test
just budgets              # the core against budgets.toml
just gen-bindings         # Swift and Kotlin bindings from the core
just apple build|test|run|smoke|measure
just android build|test|sizes
just web build|dev|test|check|measure|gen
just desktop build|dev|check|smoke|measure
```

`just verify` runs `core-verify`. The workflow `.github/workflows/clients.yml` builds every shell and writes what
it measured into each run's summary.
