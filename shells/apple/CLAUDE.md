# shells/apple/: the MailMind app for iPhone, iPad and Mac

One SwiftUI app over the Rust core (`../../core`), with an iOS target (iPhone and iPad) and a macOS target that share
every source file. The core decides everything about mail; this shell only shows what it sends.

On the Mac the app runs the whole of MailMind in its own process: the core's host (`mm-host`, through `mm-ffi`'s
`CoreHost`) opens a data folder, starts the background work, and answers the page's routes with no port open. The
window calls those routes through `Core` and their generated Swift types. iPhone and iPad, and the Mac's step 0
measure modes, still show step 0: one message list over a fictional mailbox of 100,000 messages, made on first
launch in a folder of its own.

## Commands

From the repo root (`just apple` lists them; recipes run from here whatever the working directory):

```bash
just apple build        # the core as build/MailMindCore.xcframework, the Xcode project, then the iOS simulator and macOS apps
just apple test         # the list models', the client's, the mailbox's, Settings' and the status's tests, on macOS; pictures in build/screens and build/screenshots
just apple demo         # the Mac app on fictional mail: fake servers on loopback, a temp home, a keychain stand-in
just apple demo smoke   # the same until the inbox shows rows (exit 0), or not; `demo measure`: first rows and memory
just apple run          # the Mac app as a person runs it: MailMind's own data folder and the login Keychain
just apple gen-api      # after a route or schema changes: rewrites Sources/Client/API.gen.swift (commit it on its own)
just apple smoke        # the Mac app, then the iPhone app in a simulator: each jumps nine tenths down and waits for mail
just apple measure      # cold start, memory, main-thread time, crossing cost, sizes, a web view's memory: build/measure/results.txt
just gen-bindings       # after a change to the core's contract: rewrites Generated/ (commit that diff on its own)
just apple icon         # the Mac app's icon, drawn by Icon/icon.swift into Icon/Assets.xcassets (commit the images on their own)
just apple release      # the Mac app universal, signed ad hoc, in a .dmg with its appcast: build/release (docs/releasing.md)
just apple release-check  # the release recipe end to end with a throwaway update key, then checks on what it made
```

Needs Xcode 26 with the iOS and macOS 26 SDKs, XcodeGen (`brew install xcodegen`), and the core's Rust toolchain
with the `aarch64-apple-ios`, `aarch64-apple-ios-sim` and `aarch64-apple-darwin` targets, and `x86_64-apple-darwin` for
a release (`core/rust-toolchain.toml` names them all). iOS builds compile without a simulator runtime; running one needs
a simulator or a device (`just apple smoke ios` uses the newest iPhone simulator). The first build fetches Sparkle
through Swift Package Manager.

The Mac app is `io.github.lanterno.mailmind`, version `MARKETING_VERSION` in `project.yml`;
[docs/releasing.md](../../docs/releasing.md) says how a release is cut and what the owner does once.

## What is where

| Path | What it is |
| --- | --- |
| `project.yml` | The Xcode project for XcodeGen. `MailMind.xcodeproj` is generated from it and never committed. |
| `Release/mac.yml` | What only the Mac target has, which `project.yml` includes: the icon, Sparkle (pinned), the update key and the switch for update checks, the hardened runtime. |
| `Release/` | Besides `mac.yml`: Sparkle's Info.plist keys, the entitlements, `release.sh` and `check.sh` (`just apple release` and `release-check`), `eddsa.swift` (an appcast's signatures checked as Sparkle will), `acknowledgements.py` and `licences/` (every licence the app carries), `dmg-background.swift` and `dmg.py` (the .dmg's window), and the release notes' template. |
| `Icon/` | `icon.swift`, which draws the Mac app's icon, and the asset catalog it renders into. iOS has no icon yet: compiling one needs an iOS simulator runtime installed. |
| `Generated/` | The UniFFI bindings `just gen-bindings` writes. Never edit them by hand. |
| `Sources/ListModel.swift` | `HeldRows`, `ListModel`, `OncePerFrame` and `Relay`: the rows near the screen, the window asked for at most once a frame, and the core's batches handed to the main actor. |
| `Sources/Demo.swift` | Makes the fictional mailbox on first launch, then opens the session and the list off the main thread. |
| `Sources/InboxView.swift` | The inbox screen, the list in it, and how a row's date is shown. |
| `Sources/MessageTable+macOS.swift` | The Mac's list: an `NSTableView` with one row height, its rows and placeholders drawn with Core Text. |
| `Sources/MessageTable+iOS.swift` | The iPhone and iPad list: a `UICollectionView` over a fixed-row layout, its rows and placeholders. |
| `Sources/Measure.swift` | Measure mode, run only when `MAILMIND_MEASURE` is set. |
| `Sources/Acknowledgements.swift` | **MailMind › Acknowledgements**: the licences a release put in the app, in a window of their own. |
| `Sources/Updates.swift` | Sparkle's updater and **Check for Updates…** in the app menu (`UpdateCommands`), on the Mac only. |
| `Sources/Client/Core.swift` | `Core`: the host opened over a home, a route's GET or POST as its generated types, a refusal thrown as its `Problem`, the event stream as an `AsyncStream`. `CoreHome.ofThisProcess` picks the home. |
| `Sources/Client/APISupport.swift` | What the generated types lean on: `JSONValue`, the `GetRoute` and `PostRoute` protocols, `NotJSON`, a union's decoding. |
| `Sources/Client/API.gen.swift` | The routes' answers and bodies as Swift types, all inside `API`, each route as `API.Get.X` or `API.Post.X`. Written by `just apple gen-api`; never edit it. |
| `Sources/Window/` | The Mac app's window: `Root` (the window or step 0), `MailApp` (the core, the sidebar's accounts and folders, where the window is), `MailList` (`Place`, `Selection`, the list a page at a time), `Reader` (the message chosen, its frame's URL), `MailWindow+macOS` (the views, the frame's web view), `HomeMeasure` (`MAILMIND_MEASURE=home`). |
| `Sources/Mac/Settings/` | Settings (⌘,): Accounts (each connection, its health, Test, Trust It, + with the page's presets, Edit, Remove), General, Notifications and AI; the Claude key and the Bridge's password (`KeptSecretView`, `SecretPost`), and the first run's `Welcome`. |
| `Sources/Mac/Status/` | `AppStatus` (what the status, Settings and the first run show, heard on an event stream of its own), the status pill and its menu, the account alert, the meeting reminder, new mail's notifications and Dock badge (`NewMailAlerts`), Help and the guide, and `StatusHooks`, the one modifier the window applies. |
| `Sources/Mac/Mailbox/` | The Mac window's mailbox: `Mailbox` (the sidebar's accounts, folders, labels, views and counts, each place's list, the rows chosen), its verbs (`Verbs`) and ways about (`Going`), the lists' reads (`Lists`), the sidebar, the list's table (`Table`), its column and toolbar (`ListColumn`), the lanes and the Document Hub (`Hub`), Move To and the palette (`Sheets`), a sender's card, and the menu bar's commands (`Commands`). Its hooks (`asks`, `plans`, `composeTo`, `more`, `keys`, `rowMenu`) are where the other areas join in. |
| `Generator/swift_api.py` | Writes `API.gen.swift` from the OpenAPI document `tests/support/openapi_doc.py` makes. `just apple check-api`, in `just verify`, fails when the two differ. |
| `Measure/` | `measure.sh` and the probes it compiles (the crossing cost, a web view's memory), `smoke.sh`, which CI runs, and `demo_home.py`, which `just apple demo` runs. |
| `build/` | The XCFramework, derived data, measurements and releases. Ignored by git. |

## Rules

- **The shell decides nothing about mail.** Order, which rows to keep, a sender's name, a snippet's length: the core
  sends them. The shell formats a date and draws. Anything two platforms would have to agree on goes in the core.
- **The core's thread is never the main thread.** Observer calls arrive on the core's thread and hop to the main
  actor in order (`DispatchQueue.main.async`). Opening the database and ordering the list run in a detached task.
  `Core` makes every call to the host on a queue of its own, and a request blocks that queue, never the main thread.
- **The list is a native table with one row height.** Row n is at n times the height, so the table asks only for
  the rows on screen, and a batch redraws only the rows it changed. SwiftUI's `List` spent up to 20 ms a frame deep in
  the list and kept memory for every row it had shown.
- **The shell holds no list of its own.** It keeps only the rows in the range the latest batch says to hold, and drops
  the rest when a batch arrives.
- **Generated files are never edited by hand.** Change the core, run `just gen-bindings`, commit the diff on its own.
  The XCFramework is rebuilt by every `just apple build`, so bindings and library always come from the same core.
  A route or schema change runs `just apple gen-api` the same way.
- **Never run the Mac app on a person's data by accident.** With no `MAILMIND_HOME` it opens MailMind's own folder in
  Application Support and the login Keychain: that is `just apple run`. A demo, a measure run or a test names a
  temporary home with `MAILMIND_HOME`, a key with `MAILMIND_DB_KEY` and the keychain's stand-in with
  `MAILMIND_TEST_KEYCHAIN` (as `just apple demo` does), and one that names no home opens nothing. Step 0's mailbox
  lives in a folder of its own, never inside the app's.
- **The window speaks the page's routes,** in-process, through the same perimeter as the served app, and keeps no
  closed set or limit the server owns: those come with the generated types.
- **A mail body's frame has walls of its own.** The web view runs no JavaScript, keeps nothing on disk, follows no
  link or redirect, and its scheme (`mailmind-frame:`) answers only `/api/message/body.html` through the core. The
  document's own sandbox policy stays the boundary, as in the page.
- **Fixtures are fictional.** Every address is at example.test, and the fixture key guards only fictional mail.
- **Only a release checks for updates.** `MAILMIND_CHECKS_FOR_UPDATES` stays `NO` in `Release/mac.yml`; only
  `just apple release` turns it on. A debug build, `just apple build`'s, a test or a demo never asks the network.
- **The update key's private half is the owner's alone,** in their login Keychain. Only its public half is in the
  repo (`SPARKLE_PUBLIC_KEY`). A test signs with a throwaway key made for it, never the Keychain's.
- **A release is published by the owner,** with the `gh release create` command `just apple release` prints.
- **A secret a person types goes straight to its route** (`SecretPost`), and its field is emptied once it is sent to
  be kept, or its form is closed. Settings shows only whether one is kept and where the one in use comes from, never
  the secret, and nothing that holds one describes it.
- **Comments say why,** in short plain sentences.
