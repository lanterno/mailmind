# MailMind — Roadmap

Only outstanding work is listed, re-verified against the tree on 2026-11-10: shipped behaviour is
described in the README and in the code itself, not here. The app is the React one in `web/src`, at `/`,
served by `uv run python -m mailmind serve`. Each item says what is still missing there or on the server.
The Rust core's items, the clients' and those on showing and sorting mail were added on 2026-11-13, from the tree.

Each item links its GitHub issue, which closes when the item leaves this list. Items cite functions and symbols rather
than line numbers, which drift. Value and effort are relative to this codebase — S = an afternoon, M = a few days,
L = a week-plus.

## Search, rules and organization

- **PDF preview in the Document Hub** · low value · effort M · [#2](https://github.com/lanterno/mailmind/issues/2) — The Hub previews images in a frame whose sandbox allows nothing
  (`perimeter.attachment_preview_headers`). Chromium won't show a PDF in a sandboxed frame, and a same-origin one would break the
  security model, so a PDF needs a renderer of its own (pdf.js, say), as a new dependency, running with no origin.

## Speed and the connection layer

- **Make a gate for each account the default** · high value · effort S · [#3](https://github.com/lanterno/mailmind/issues/3) — `MAILMIND_WORKERS=account`
  gives each account its own gate (`mailmind.platform.workers.gates`), so a slow Proton fetch no
  longer blocks archive, send or open on another account; unset, one lock is still every
  account's gate. The whole suite passes under either. The owner soaks it on real accounts, then it
  becomes the default and the flag goes. Then
  give each account's worker the connection it owns (the pool dissolved into it), and split the
  jobs that still pass `gates.EVERY_ACCOUNT`, which wait for every gate: a draft save whose old
  copy is in another account (it needs just the two accounts' gates, which `gates.run` can't take
  yet), and an undo entry that names no account, as a snooze's does (`snooze.undo_entry`), though undoing a snooze asks
  no server anything.
- **Soak IMAPClient on real accounts, then make it the default** · high value · effort S · [#4](https://github.com/lanterno/mailmind/issues/4) — Waits on
  the owner. `uv run python -m mailmind serve` reaches an account through IMAPClient when its config asks (`"imap_adapter":
  "imapclient"`; the startup line counts them). Both adapters pass the contract against the fake
  and against Dovecot (`just test-dovecot`), and the whole suite passes under either. On real
  Gmail, IMAP and Proton Bridge accounts the two answered every read alike (capabilities, folders,
  status, searches, headers, flags, bodies, IDLE), but nothing has written through IMAPClient
  there yet, nor run on it for days. The owner asks for it on one account, then on all, and uses
  it daily for a week, watching Settings' sync health and the server's output;
  removing the key goes back. Then `front.build` calls `mailbox.use("imapclient")` and the ask
  goes. The command line never comes through `front.build`, so it stays on imaplib until then; it runs on
  the server's packages now, so it can take IMAPClient in the same step, and imaplib's adapter go after it.
- **Turn the connection pool on by default** · high value · effort M · [#5](https://github.com/lanterno/mailmind/issues/5) — Waits on the owner's soak.
  `Pool` is complete, safety-reviewed and wired into four read call sites (`Ingest.fetch_body`,
  `Folders._live_folder_list` backing the folder rail, `Sweeps.plan` ahead of a sweep, and reconcile's read of UIDs), but
  `Pool._enabled` reads `MAILMIND_POOL`, then each account's `"pool"` key, both unset by default, so every read falls through to a
  fresh session and a logout. Its docstring has a cold Proton body fetch at ~37s, against ~2s warm.
  Remaining: run it on real accounts for days; validate against Proton Bridge (`ProviderProfile`
  recycles a Bridge connection sooner, and `Pool._enabled` leaves Bridge out of the `MAILMIND_POOL=1`
  catch-all, so it has to be listed by address); extend borrowing to mutations (only reads borrow
  today); and default it on.

## The move to the Rust core

Step 3 of the "MailMind client architecture" document is under way ([docs/clients.md](docs/clients.md)): the Rust server
(`core/crates/mm-server`) takes over the Python app's routes one by one, and `tests/support/rust_ledger.txt` lists the
served tests it passes against the same scripts. Until the flip, the Python app gets fixes only.

- **Port the rest of the routes** · high value · effort L · [#6](https://github.com/lanterno/mailmind/issues/6) — In progress by area: the page and settings, the mailbox's
  reads, search, rules and the local states, and IMAP, sync and the change queue. After those: sending (the outbox,
  drafts and RSVP, over SMTP), cleanup and unsubscribe (SMTP and one-click HTTPS), the assistant (the Claude API client,
  the cost governor, batches) and accounts (add, edit, test, delete, trust-cert). `just test-rust-all` says what passes.
- **The login Keychain in the Rust server** · high value · effort S · [#7](https://github.com/lanterno/mailmind/issues/7) — `mailmind-server` reads secrets through
  `NoKeychain` (`mm-server/src/real.rs`), so it needs `MAILMIND_DB_KEY` and can't log in to an account whose password the
  Keychain keeps. Port `platform.keychain`: `/usr/bin/security`, with the secret on standard input.
- **Verified TLS to mail servers off this machine** · high value · effort M · [#8](https://github.com/lanterno/mailmind/issues/8) — `mm_imap::Server::connect` refuses them
  (`VerifiedTlsNotOnTheRustServerYet`); only loopback servers with a pinned certificate are reached. It needs the system's
  roots and tests of its own.
- **Fakes that stand alone** · medium value · effort S · [#9](https://github.com/lanterno/mailmind/issues/9) — `tests/fakes/imap_server.py` imports `mailmind` (the Keychain
  item's name and `platform.keychain`). Before the flip, the fakes must run with no Python app beside them.
- **The in-process browser tests on the Rust server** · medium value · effort M · [#10](https://github.com/lanterno/mailmind/issues/10) — `tests/e2e/browser/in_process/` patches
  the Python app's internals, so the ledger can't hold them. Each needs a way in through the Rust server's seams
  (`Clock`, `Outside`, `Keychain`), or a served test that states the same thing.
- **The flip** · high value · effort M · [#11](https://github.com/lanterno/mailmind/issues/11) — Step 4: when the served suites pass against both servers and the owner has used
  the Rust server daily for two weeks, the Python server goes. Then step 5 moves the page from routes to the core's
  views and commands, screen by screen, starting with the message list.

## The clients

- **Measure the desktop app on Windows and Linux** · high value · effort S · [#12](https://github.com/lanterno/mailmind/issues/12) — CI shows only that their lists open and
  scroll (`just desktop smoke`). Measure launch, idle memory and frame times on the runners, add budgets for each to
  `budgets.toml`, and decide each platform's shell from them (see Open decisions).
- **Measure the Mac's Tauri build with the screen awake** · low value · effort S · [#13](https://github.com/lanterno/mailmind/issues/13) — `desktop.mac_first_rows_ms` and
  `desktop.mac_idle_memory_mb` were taken behind a locked screen, where macOS runs the app at about half speed.
- **Run the iPhone smoke check once** · medium value · effort S · [#14](https://github.com/lanterno/mailmind/issues/14) — `just apple smoke` scrolls the list in an iPhone
  simulator on CI's Mac, and has yet to complete there.
- **Real app identifiers** · medium value · effort S · [#15](https://github.com/lanterno/mailmind/issues/15) — `app.mailmind` is a placeholder (`shells/apple/project.yml`,
  `shells/android/app/build.gradle.kts`). The real ones need the domain and the entity the store listings will name.
- **Tauri's accepted advisories** · medium value · effort S · [#16](https://github.com/lanterno/mailmind/issues/16) — `shells/desktop/src-tauri/osv-scanner.toml` ignores seven
  until 2027-02-12. Upgrade past them, or look at each again, before then.

## Showing and sorting mail

- **Keep a message's head styles** · medium value · effort S · [#17](https://github.com/lanterno/mailmind/issues/17) — `mm_content::sanitize` drops a `<head>` with all it holds,
  its `<style>` elements included, so newsletters styled from the head show plainer than they were sent. Keep those
  sheets, sanitized as any other `<style>` is.
- **End nested dropped elements where a browser ends them** · low value · effort S · [#18](https://github.com/lanterno/mailmind/issues/18) — a `<form>` inside a `<form>`, a
  `<button>` inside a `<button>` or a `<select>` inside a `<select>` loses the text after it (`DROP_WITH_CONTENT` in
  `sanitize/html.rs`). The head's fix (`HEAD_HOLDS`) is the mechanism.
- **Show what a browser shows of dropped elements** · low value · effort M · [#19](https://github.com/lanterno/mailmind/issues/19) — `<noscript>` (the frame runs no script), an
  `<object>`'s fallback, an unclosed `<xml>`, and the HTML a browser breaks out of `<svg>` and `<math>` to show are dropped
  whole.
- **Reclassify mail sorted under the old patterns** · medium value · effort S · [#20](https://github.com/lanterno/mailmind/issues/20) — the core's classifier
  (`mm_curation::classifier`) no longer matches its patterns inside longer words, but mail sorted before keeps its bucket:
  only rows a rule covers are sorted again.
- **A lone surrogate in a sender's name** · low value · effort S · [#21](https://github.com/lanterno/mailmind/issues/21) — the core reads one as U+FFFD where Python read it as
  itself (the strict xfail in `tests/unit/curation/test_classifier_in_rust.py`).
- **Simplify the search grammar after the flip** · low value · effort S · [#22](https://github.com/lanterno/mailmind/issues/22) — `mm_discovery` copies Python's quirks (its
  `fromisoformat`, a Unicode 16 case table) only so the two answer alike. Once Python goes, keep strict `before:` and
  `after:` dates and Rust's own Unicode.

## The desktop app

- **Proton Mail goes with the desktop app** — decided on 2026-11-08. Proton offers IMAP only through its Bridge, which has
  to run on the same computer as the app, so only a desktop app can count on it. The `proton` account type, the
  `proton_bridge` credential and the loopback handling stay as they are until that app exists; add no new Bridge special cases.

## Open decisions

These are carried forward from the original phase plan because they need a call, not an
implementation.

- **How aggressively to prefetch folders** · [#23](https://github.com/lanterno/mailmind/issues/23) — on-demand sync bounds disk and first-sync cost, but
  unified-inbox-across-accounts and global search both want broader coverage.
- **Where the data directory lives by default** · [#24](https://github.com/lanterno/mailmind/issues/24) — `MAILMIND_HOME` names one, and unset it is the repo
  folder, where it has always been. Left for later on 2026-11-03.
- **Default remote-image policy** · [#25](https://github.com/lanterno/mailmind/issues/25) — block-by-default with per-message "Load images" (current, and the
  privacy-preserving choice) vs. allowing images for known/personal senders.
- **Native shells on the desktop** · [#26](https://github.com/lanterno/mailmind/issues/26) — on the Mac the native list opens in 218 ms and idles at 28 MB; the same page in
  Tauri, 724 ms and 83.9 MB (`budgets.toml`). The recommendation: commit to the native Mac app, shipped after the Tauri
  one once the iPad shell has the screens, and keep the web shell on Windows and Linux unless their measurements miss a
  budget, with a GTK4 shell in Rust as Linux's fallback.
- **The client's licence, a legal entity, and who runs the push relay** · [#27](https://github.com/lanterno/mailmind/issues/27) — the architecture document's risks section
  recommends an answer for each. The push relay waits on the iPhone app; the other two on the first store listing.

## Next up

1. **Step 3: the Rust server on every route** (high) — the areas in progress, then sending, cleanup, the assistant and
   accounts, with the Keychain and verified TLS beside them. See The move to the Rust core.
2. **Soak the three defaults, one at a time** (high) — `MAILMIND_WORKERS=account`, then IMAPClient, then `MAILMIND_POOL`. Each
   is written and tested, and waits on days of real use before it becomes the default.
