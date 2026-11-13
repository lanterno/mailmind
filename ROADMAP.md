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

- **Port the rest of the routes** · high value · effort L · [#6](https://github.com/lanterno/mailmind/issues/6) — The Rust server answers 64 of the Python app's 103
  routes: the page and settings, the Mailbox's lists, a conversation, a message opened, the digest and the Document Hub;
  search, saved views and contacts; rules, filters, the Screener, the audit trail and Tidy; snooze, mute, follow-ups,
  Reply Later and tasks; filing, Undo and the tray of changes; sync, refresh and reconcile. `just test-rust` holds 44 of
  `tests/api`'s 149 served tests. Still Python's: sending (`/api/send`, drafts, the outbox's retry, cancel and send-now,
  RSVP), cleanup (`/api/cleanup/*`, `/api/sweep`, `/api/tidy/undo`, unsubscribe over SMTP and one-click HTTPS), the
  assistant (`/api/ai/*`, the briefing, the review, the Claude API client, the cost governor, batches), accounts (add,
  edit, test, delete, trust-cert), the calendar (`/api/upcoming`, `/api/calendar/event`, which need an ICS reader and
  recurrence), the export's archive (`/api/export/archive` answers that it isn't served yet), and `/api/debug/health`
  and `/api/debug/jobs`. `just test-rust-all` says what passes.
- **The Rust server's background work** · high value · effort M · [#28](https://github.com/lanterno/mailmind/issues/28) — Nothing on the Rust server runs on its
  own: the change queue drains, a sync round runs, a snooze wakes and a follow-up comes due only through the served
  suites' `work` line (`mm_served::jobs`) or a request. Port the workers (`platform.workers`): the queue's worker and its
  kick, the sync round and what it starts (`attention.wake_due`, the `assess` job, `warm_tick`), and IDLE.
- **What Attention and Curation ask of the change queue** · high value · effort S · [#29](https://github.com/lanterno/mailmind/issues/29) — The queue is on the
  Rust server, but `bound::NoFlags` (Attention's `Flags::mark_seen`: a mute marking unread mail read, a snooze waking
  read mail unread) and `bound::NoFiling` (Curation's `Filing::trash_now`: a rule that blocks a copy) still answer that
  it can't. `/api/undo` has no reverser for `snooze`, `mute` or `unblock` (`Undo::new`'s list; the Python app's
  `Snoozing.reverse` and `Muting.reverse`).
- **Every change tells the page** · medium value · effort S · [#30](https://github.com/lanterno/mailmind/issues/30) — The Mailbox's events (`MailboxEvent`) go to
  `Unheard`, the writes of Discovery, Curation and Attention publish nothing, and `/api/stats` is counted afresh at every
  read. Forward each to the event stream (`events::Hub`) under the Python app's names (`outbox`, `commands-changed`,
  `undo-done`, `sync-done`, `new-mail`, `reconcile`), and keep the counts until an event says they moved.
- **The sync's remaining parts** · medium value · effort M · [#31](https://github.com/lanterno/mailmind/issues/31) — `mm_mailbox::ingest` leaves out a person's
  arrival filters (Curation's `arrival_flags`), the invite a message carries kept with its body, and holding back a login
  a server refused (`accounts.logins`, with its `forget` on a retry). A move's folder catch-up (`Folders.touched`) and the
  1Password preload aren't ported either.
- **One adapter for each port** · low value · effort S · [#32](https://github.com/lanterno/mailmind/issues/32) — The areas ported side by side left doubles: three
  readers of the rule book (`Database::rules_document`, `RulesDocument`, the Curation store's `RuleBook`), three ways of
  reading a POST's fields (`body::Fields`, `posted`, `schema`), two adapters naming the accounts (`wiring::KnownAccounts`,
  `bound::Names`), two tallies of a sender (`StoredTally`, `ContactMail::card`), and two writers of the undo stack
  (`blocks::push_undo`, `mailbox_commands`). Keep one of each.
- **Where the Rust server still answers apart** · low value · effort S · [#33](https://github.com/lanterno/mailmind/issues/33) — Discovery reads a folder's part
  from its name only (`bound::NamedRoles`), not from what LIST said; a domain that isn't ASCII is lowered and punycoded with
  no nameprep; a failed sync or reconcile keeps Rust's class name (`Timeout`, `ConnectionRefused`) where Python kept its
  exception's, and its log line names no account; a snooze commits twice, its undo entry in a block of its own.
- **Deep pages of a long list** · low value · effort S · [#34](https://github.com/lanterno/mailmind/issues/34) — `/api/messages` at offset 50,000 of a 100,000-message
  folder takes about 217 ms on the Rust server against 106 ms on Python's, with the same query plan and the same SQLite
  time in both, so the rest goes in the route. Profile it.
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

1. **Step 3: the Rust server on every route** (high) — its background work and what the local writes ask of the change
   queue, then sending, cleanup, the assistant, accounts and the calendar, with the Keychain and verified TLS beside
   them. See The move to the Rust core.
2. **Soak the three defaults, one at a time** (high) — `MAILMIND_WORKERS=account`, then IMAPClient, then `MAILMIND_POOL`. Each
   is written and tested, and waits on days of real use before it becomes the default.
