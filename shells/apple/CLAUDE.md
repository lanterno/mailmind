# shells/apple/: the MailMind app for iPhone, iPad and Mac

One SwiftUI app over the Rust core (`../../core`), with an iOS target (iPhone and iPad) and a macOS target that share
every source file. The core decides everything about mail; this shell only shows what it sends. Step 0 is a walking
skeleton: one message list over a fictional mailbox of 100,000 messages, made in Application Support on first launch.

## Commands

From the repo root (`just apple` lists them; recipes run from here whatever the working directory):

```bash
just apple build        # the core as build/MailMindCore.xcframework, the Xcode project, then the iOS simulator and macOS apps
just apple test         # the list model's tests, on macOS
just apple run          # the Mac app
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
| `Release/` | Besides `mac.yml`: Sparkle's Info.plist keys, the entitlements, `release.sh` and `check.sh` (`just apple release` and `release-check`), `eddsa.swift` (an appcast's signature checked as Sparkle will), and the release notes' template. |
| `Icon/` | `icon.swift`, which draws the Mac app's icon, and the asset catalog it renders into. iOS has no icon yet: compiling one needs an iOS simulator runtime installed. |
| `Generated/` | The UniFFI bindings `just gen-bindings` writes. Never edit them by hand. |
| `Sources/ListModel.swift` | `HeldRows`, `ListModel`, `OncePerFrame` and `Relay`: the rows near the screen, the window asked for at most once a frame, and the core's batches handed to the main actor. |
| `Sources/Demo.swift` | Makes the fictional mailbox on first launch, then opens the session and the list off the main thread. |
| `Sources/InboxView.swift` | The inbox screen, the list in it, and how a row's date is shown. |
| `Sources/MessageTable+macOS.swift` | The Mac's list: an `NSTableView` with one row height, its rows and placeholders drawn with Core Text. |
| `Sources/MessageTable+iOS.swift` | The iPhone and iPad list: a `UICollectionView` over a fixed-row layout, its rows and placeholders. |
| `Sources/Measure.swift` | Measure mode, run only when `MAILMIND_MEASURE` is set. |
| `Sources/Updates.swift` | Sparkle's updater and **Check for Updates…** in the app menu (`UpdateCommands`), on the Mac only. |
| `Measure/` | `measure.sh` and the probes it compiles (the crossing cost, a web view's memory), and `smoke.sh`, which CI runs. |
| `build/` | The XCFramework, derived data, measurements and releases. Ignored by git. |

## Rules

- **The shell decides nothing about mail.** Order, which rows to keep, a sender's name, a snippet's length: the core
  sends them. The shell formats a date and draws. Anything two platforms would have to agree on goes in the core.
- **The core's thread is never the main thread.** Observer calls arrive on the core's thread and hop to the main
  actor in order (`DispatchQueue.main.async`). Opening the database and ordering the list run in a detached task.
- **The list is a native table with one row height.** Row n is at n times the height, so the table asks only for
  the rows on screen, and a batch redraws only the rows it changed. SwiftUI's `List` spent up to 20 ms a frame deep in
  the list and kept memory for every row it had shown.
- **The shell holds no list of its own.** It keeps only the rows in the range the latest batch says to hold, and drops
  the rest when a batch arrives.
- **Generated files are never edited by hand.** Change the core, run `just gen-bindings`, commit the diff on its own.
  The XCFramework is rebuilt by every `just apple build`, so bindings and library always come from the same core.
- **Fixtures are fictional.** Every address is at example.test, and the fixture key guards only fictional mail.
- **Only a release checks for updates.** `MAILMIND_CHECKS_FOR_UPDATES` stays `NO` in `Release/mac.yml`; only
  `just apple release` turns it on. A debug build, `just apple build`'s, a test or a demo never asks the network.
- **The update key's private half is the owner's alone,** in their login Keychain. Only its public half is in the
  repo (`SPARKLE_PUBLIC_KEY`). A test signs with a throwaway key made for it, never the Keychain's.
- **A release is published by the owner,** with the `gh release create` command `just apple release` prints.
- **Comments say why,** in short plain sentences.
