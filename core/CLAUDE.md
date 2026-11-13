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
| `mm-kernel` | Names, refusals, problems and a message's identity, and the seams a program meets the world through (`Clock`, `Outside`, `Keychain`, the log). Nothing in it reads the disk, the network or the clock; nor do the context crates (`mm-accounts`, `mm-mailbox`, `mm-discovery`, `mm-curation`, `mm-compose`, `mm-assistant`) or `mm-content`. |
| `mm-store` | The encrypted database, and the only place the app opens a file: the database, `mailmind.config.json` and the page's build, each through `Outside`. It implements the context crates' ports, with each context's SQL in a module of its name. |
| `mm-accounts` | The mail accounts and how MailMind reaches each: the config's entries, where a secret is, a loopback server's pinned certificate. |
| `mm-mailbox` | The mail on each server: how its copies thread into conversations, which folder plays which part, the folder rail, and what is stored counted: each inbox by bucket, a Gmail account's labels, each account's syncs. |
| `mm-net` | What every mail protocol shares: the TCP connection with its time limits, TLS on it (a certificate verified on the roots the system trusts off this machine, and pinned on it), and the stream. |
| `mm-imap` | An account's IMAP server, as the Mailbox's `MailSession`, on `mm-net`. |
| `mm-smtp` | An account's SMTP server, as the outbox delivers through it, on `mm-net`: the certificate handed back for the caller's pin check before the login. |
| `mm-server` | The Rust server: an HTTP adapter on axum that decodes a request, calls a context's use case and encodes the answer, behind the Python app's perimeter. `environment` is the only module that reads the environment; `app` binds the contexts. The binary is `mailmind-server`. |
| `mm-served` | The Rust server as the served suites start it (`tests/support/served.py`'s line protocol): a frozen clock, the tripwire `guard.py` is, and `fake_keychain`'s file, through the server's seams. Never shipped. |
| `mm-discovery` | Finding mail by what a person types: the search grammar, the Document Hub, what is said of a sender. |
| `mm-compose` | Drafts, building a message, the outbox and delivery: a message built byte for byte as the Python app builds it, a send queued and delivered, a draft saved locally first and reopened from its own bytes, the outbox tray and the snippets. |
| `mm-assistant` | Everything asked of Claude: so far how AI stands with no call made, and its daily limit. |
| `mm-curation` | What stays in the inbox: the classifier, the rule sets it reads, and a sweep's plan from the stored inbox. |
| `mm-content` | What a message holds: its MIME parts (on mail-parser), its HTML made safe to show (html5ever's tokenizer), and the document its frame loads. |
| `mm-app` | The contract: `Session`, `ListView`, `ViewObserver`. `mm_app::wire` is the same contract as JSON lines. |
| `mm-ffi` | The contract for Swift and Kotlin, through UniFFI. The only crate that knows UniFFI exists. |
| `mm-py` | The leaves for the Python app, as the module `mailmind_leaves` (PyO3), until the Rust server replaces it. The only crate that knows Python exists. |
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
