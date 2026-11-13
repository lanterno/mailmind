# Clients

MailMind's clients are one Rust library and a shell per platform. The design is the "MailMind client
architecture" document; this page says what is built, how the pieces talk, and what step 0 found.

| Where | What it is | Read |
| --- | --- | --- |
| `core/` | The Rust core: the encrypted store, the contract (`Session`, `ListView`), its carriers | [core/CLAUDE.md](../core/CLAUDE.md) |
| `shells/apple/` | SwiftUI for iPhone, iPad and Mac, over UniFFI's Swift | [shells/apple/CLAUDE.md](../shells/apple/CLAUDE.md) |
| `shells/android/` | Compose, over UniFFI's Kotlin | [shells/android/CLAUDE.md](../shells/android/CLAUDE.md) |
| `shells/web/` | React, over the core compiled to WebAssembly in a dedicated worker | [shells/web/CLAUDE.md](../shells/web/CLAUDE.md) |
| `shells/desktop/` | Tauri around the web shell's page, over the core running natively | [shells/desktop/CLAUDE.md](../shells/desktop/CLAUDE.md) |
| `budgets.toml` | The limits every client is held to, and what was measured | |

The Python app in `mailmind/` stays what runs until the clients replace it. Until then it owns the database's
schema and migrations; the core reads the same encrypted files (a raw 32-byte key, SQLCipher 4's defaults).

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
| Mac, SwiftUI | Runs and scrolls; measured |
| Mac, Tauri | Runs and scrolls; measured |
| Browser (Chromium) | Runs and scrolls; measured |
| iPhone and iPad | Builds and links for device and simulator; not run (no simulator runtime or device here) |
| Android | Builds in CI only (no SDK here); not run |
| Windows and Linux, Tauri | Build in CI only; not run |

| What | Measured | Limit |
| --- | --- | --- |
| Core: open and first rows of 100,000 | 10.7 ms | 150 ms |
| Core: idle memory | 3.5 MB | 40 MB |
| Core: next page while scrolling, p95 | 0.05 ms | 3 ms |
| Mac (SwiftUI): launch to first rows | 240 ms | 800 ms |
| Mac (SwiftUI): memory idle, and after scrolling all 100,000 | 41 MB, 109 MB | 200 MB |
| Mac (SwiftUI): main thread per frame, p95, fast scroll and whole-list sweep | 14 ms, 296 ms | 8 ms |
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
- **SwiftUI's `List` is the Mac's bottleneck, not the core.** Normal scrolling is near the 8 ms frame budget, but
  bringing a new screen in every frame deep in the list costs up to 296 ms at p95, and the `List` keeps about 65 MB
  for rows it has shown. No variant of `List` or `LazyVStack` fixed it. The next try is an `NSTableView` or
  `UICollectionView` with fixed row heights behind a thin representable.
- **The crossing is not a cost.** A 50-row batch through UniFFI takes about 85 µs, about 1% of a frame.

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

## Commands

```bash
just core-verify          # the core: format, clippy as errors, every test
just budgets              # the core against budgets.toml
just gen-bindings         # Swift and Kotlin bindings from the core
just apple build|test|run|measure
just android build|sizes
just web build|dev|test|check|measure|gen
just desktop build|dev|check|measure
```

`just verify` runs `core-verify`. The workflow `.github/workflows/clients.yml` builds every shell and writes what
it measured into each run's summary.
