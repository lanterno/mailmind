# shells/android: the Android app over the Rust core

A Compose and Material 3 app that shows what the core (`../../core`) sends. At step 0 it is one screen: the
inbox of a fictional mailbox of 100,000 messages that the core makes on first launch.

Two Gradle modules:

| Module | What it is |
| --- | --- |
| `core` | The core as an Android library: UniFFI's generated Kotlin (`app.mailmind.core`), JNA, and `libmailmind_core.so` per ABI in `src/main/jniLibs`. |
| `app` | The app: `MailListModel` holds the rows the core sent, `MailList.kt` draws them. |

## Commands

Run from the repo root. They need the Android SDK and NDK (`ANDROID_HOME`, or `ANDROID_NDK_HOME` for the NDK),
JDK 17, `cargo install cargo-ndk --locked`, and Gradle until the wrapper is committed.

```bash
just android build     # the core per ABI (arm64-v8a, x86_64), then the debug app
just android test      # the core per ABI, then the instrumented test on the emulator or device adb sees
just android libs      # only the core per ABI, into core/src/main/jniLibs (ignored by git)
just android sizes     # the core's stripped size per ABI, as a Markdown table
just gen-bindings      # rewrite core/src/main/kotlin from the core (with the Swift bindings)
```

Versions are in `gradle/libs.versions.toml`, Gradle's in `gradle/wrapper/gradle-wrapper.properties`; CI reads
both. There is no wrapper jar yet, since none can be made without a JDK: CI runs the Gradle that the properties name
through `gradle/actions/setup-gradle`. To add the wrapper, run `gradle wrapper` once here with that Gradle and
commit `gradlew`, `gradlew.bat` and `gradle/wrapper/gradle-wrapper.jar` on their own; `just android build` then
uses it.

The instrumented test (`app/src/androidTest`) launches the app on the demo mailbox and jumps its list to row
90,000 and to the last row, waiting each time for the row the core sends. A row the core sent carries a test tag
naming its position (`messageRowTag`); a placeholder carries none. CI runs it on an x86_64 emulator at `minSdk`, the
oldest Android the core is compiled for.

## Rules

- **The shell decides nothing about mail.** Which rows exist, their order, which sender name shows and how much of
  the text: the core decides all of it. The shell formats dates and draws placeholders, and nothing else.
- **The core's thread is never the main thread.** Observer calls arrive on the core's thread; `OnMainThread` posts
  each to the main thread, in order, and only there does state change. Opening or making the mailbox reads the
  disk, so it runs on a thread of its own.
- **The shell keeps only the held range.** Each `ViewBatch` names the rows to keep; every other row is dropped, so
  memory does not grow with the mailbox.
- **Generated files are never edited by hand.** `core/src/main/kotlin` is written by `just gen-bindings`; change the
  core and regenerate, and commit the generated diff on its own. CI fails when they differ.
- **Fictional data only.** The demo mailbox is the core's fixture, every address at example.test.
- **Package names are placeholders** (`app.mailmind.core`, `app.mailmind.android`) until the domain is settled;
  change them with `core/crates/mm-ffi/uniffi.toml`.
