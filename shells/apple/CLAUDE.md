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
just apple measure      # cold start, memory, main-thread time, crossing cost, sizes, a web view's memory: build/measure/results.txt
just gen-bindings       # after a change to the core's contract: rewrites Generated/ (commit that diff on its own)
```

Needs Xcode 26 with the iOS and macOS 26 SDKs, XcodeGen (`brew install xcodegen`), and the core's Rust toolchain
with the `aarch64-apple-ios`, `aarch64-apple-ios-sim` and `aarch64-apple-darwin` targets. iOS builds compile without a
simulator runtime; running one needs a simulator or a device.

## What is where

| Path | What it is |
| --- | --- |
| `project.yml` | The Xcode project for XcodeGen. `MailMind.xcodeproj` is generated from it and never committed. |
| `Generated/` | The UniFFI bindings `just gen-bindings` writes. Never edit them by hand. |
| `Sources/ListModel.swift` | `HeldRows`, `ListModel` and `Relay`: the rows near the screen, and the core's batches handed to the main actor. |
| `Sources/Demo.swift` | Makes the fictional mailbox on first launch, then opens the session and the list off the main thread. |
| `Sources/InboxView.swift` | The list, its rows and their placeholders, and how a row's date is shown. |
| `Sources/Measure.swift` | Measure mode, run only when `MAILMIND_MEASURE` is set. |
| `Measure/` | `measure.sh`, and the probes it compiles: the crossing cost and a web view's memory. |
| `build/` | The XCFramework, derived data and measurements. Ignored by git. |

## Rules

- **The shell decides nothing about mail.** Order, which rows to keep, a sender's name, a snippet's length: the core
  sends them. The shell formats a date and draws. Anything two platforms would have to agree on goes in the core.
- **The core's thread is never the main thread.** Observer calls arrive on the core's thread and hop to the main
  actor in order (`DispatchQueue.main.async`). Opening the database and ordering the list run in a detached task.
- **The shell holds no list of its own.** It keeps only the rows in the range the latest batch says to hold, and drops
  the rest when a batch arrives.
- **Generated files are never edited by hand.** Change the core, run `just gen-bindings`, commit the diff on its own.
  The XCFramework is rebuilt by every `just apple build`, so bindings and library always come from the same core.
- **Fixtures are fictional.** Every address is at example.test, and the fixture key guards only fictional mail.
- **Comments say why,** in short plain sentences.
