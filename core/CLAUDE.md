# core/: the Rust library every MailMind client runs

The design is the "MailMind client architecture" document. In short: everything two platforms would have to agree
on lives here, and each shell (Apple, Android, web) only shows it. What is here now is step 0, the walking
skeleton: one message list, read from an encrypted database and served to every shell, with the budgets measured.

## Commands

```bash
just core-verify        # format check, clippy with warnings as errors, every test
just budgets            # measure the core against ../budgets.toml (release build, 100,000 fictional messages)
just fixtures PATH [N]  # a fictional mailbox at PATH
just core-serve [N]     # the core over standard input and output, as JSON lines, on a fictional mailbox
just gen-bindings       # Swift and Kotlin bindings, into shells/apple/Generated and shells/android
```

## The crates

| Crate | What it is |
| --- | --- |
| `mm-kernel` | Names and refusals. Nothing in it reads the disk, the network or the clock. |
| `mm-store` | The encrypted database, and the only place a file is opened. |
| `mm-app` | The contract: `Session`, `ListView`, `ViewObserver`. `mm_app::wire` is the same contract as JSON lines. |
| `mm-ffi` | The contract for Swift and Kotlin, through UniFFI. The only crate that knows UniFFI exists. |
| `mm-fixtures` | The fictional mailbox the tests, the budgets and the shells' demo modes use. |
| `mm-daemon` | The core as a program: JSON lines over standard input and output, or a Unix socket. |
| `mm-budgets` | Measures the core against `../budgets.toml` and fails when it is over. |

## Rules

- **A shell never sees a row number.** Rows carry the message key (`message_identity.key`); `pk` stays in the store.
- **Refusals are values,** and their words never hold a person's data. Never echo a request back in one.
- **The database.** Open it only through `mm_store::Store`, which keys it before anything reads it: a raw 32-byte
  key and SQLCipher 4's defaults, so the Python app and the core open the same files.
- **Keys.** A key never prints (`DbKey`'s `Debug` says `DbKey(..)`) and never goes on a command line: programs read
  `MAILMIND_DB_KEY`. Key bytes that cross from a shell are wiped once the core holds them.
- **The contract changes in `mm-app` first,** then in every carrier in the same change: `mm-ffi`, and `wire` with
  its TypeScript types. Run `just gen-bindings` and commit the generated diff on its own.
- **Budgets.** `just budgets` passes before a commit. A limit changes only in a commit that says why.
- **Fixtures are fictional.** Every address is at example.test.
- **`unsafe` is denied.** A crate that needs it allows it on one item, with a `SAFETY:` comment.
- **Commits** follow the root rules: tests in their own commit after the code, and a dependency change in its own
  commit with `Cargo.lock` in the next.
