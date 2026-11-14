# MailMind — Roadmap

Only outstanding work is listed, re-verified against the tree on 2026-11-14: shipped behaviour is
described in the README and in the code itself, not here. The app is the React one in `web/src`, at `/`,
served by `uv run python -m mailmind serve`. Each item says what is still missing there or on the server.
The Rust core's items, the clients' and those on showing and sorting mail were added on 2026-11-13, from the tree.

Each item links its GitHub issue, which closes when the item leaves this list, and says which items it waits on; the
issues carry the same links. Items cite functions and symbols rather
than line numbers, which drift. Value and effort are relative to this codebase — S = an afternoon, M = a few days,
L = a week-plus.

## Search, rules and organization

- **PDF preview in the Document Hub** · low value · effort M · [#2](https://github.com/lanterno/mailmind/issues/2) — The Hub previews images in a frame whose sandbox allows nothing
  (`PREVIEW_POLICY` in `mm-server/src/routes/message.rs`). Chromium won't show a PDF in a sandboxed frame, and a same-origin one would break the
  security model, so a PDF needs a renderer of its own (pdf.js, say), as a new dependency, running with no origin. Waits on the
  flip (#11): its frame is a new route, and until then the Python app gets fixes only.

## Speed and the connection layer

- **Make a gate for each account the default** · high value · effort S · [#3](https://github.com/lanterno/mailmind/issues/3) — `MAILMIND_WORKERS=account`
  gives each account its own gate, so a slow fetch on one account no longer blocks archive, send or open on another;
  unset, one lock is still every account's gate. Both servers read it (`mailmind.platform.workers.gates`, and the Rust
  server's `environment::gate_per_account`), so the owner can turn it on now and keep it through the flip. After a soak on
  real accounts it becomes the Rust server's default and the flag goes. Then split the jobs that still pass
  `EVERY_ACCOUNT`, which wait for every gate: a draft save whose old copy is in another account (`mm_compose::drafting`;
  it needs just the two accounts' gates, which the gates can't take yet), and an undo entry that names no account, as a
  snooze's does (`mm_mailbox::undo`), though undoing a snooze asks no server anything.
- **Keep each account's IMAP connection on the Rust server** · high value · effort M · [#5](https://github.com/lanterno/mailmind/issues/5) — The Rust
  server opens a session and logs out for every read: `MAILMIND_POOL` is declared in `environment.rs`, and nothing reads
  it. The Python app's `Pool` (complete, opt-in, never soaked) goes with the flip; its docstring has a cold Proton body
  fetch at about 37 s against 2 s warm. Give each account's worker the connection it owns, for reads and then for
  changes, with a Bridge connection recycled sooner as `ProviderProfile` does. Waits on the gate per account (#3).

## The move to the Rust core

Step 3 of the "MailMind client architecture" document is under way ([docs/clients.md](docs/clients.md)): the Rust server
(`core/crates/mm-server`) takes over the Python app's routes one by one, and `tests/support/rust_ledger.txt` lists the
served tests it passes against the same scripts. Until the flip, the Python app gets fixes only.

- **Port the rest of the routes** · high value · effort L · [#6](https://github.com/lanterno/mailmind/issues/6) — The Rust server answers 85 of the Python app's
  103 routes: the page and settings, the Mailbox's lists, a conversation, a message opened, the digest and the
  Document Hub; search, saved views and contacts; rules, filters, the Screener, the audit trail and Tidy; snooze,
  mute, follow-ups, Reply Later and tasks; filing, Undo and the tray of changes; sync, refresh and reconcile;
  accounts (add, edit, test, delete, trust-cert); sending (`/api/send`, drafts, the preview, the outbox's Undo,
  retry and send-now, over `mm-smtp`); a sender's cleanup, its unsubscribe by mail, the sweep and Tidy's Undo; and the
  calendar (`/api/upcoming`, `/api/calendar/event`, `/api/invite/rsvp`, on `mm-calendar`'s own bounded ICS and
  recurrence reader). `just test-rust` holds 99 of `tests/api`'s 146 served tests and 7 of the browser's. Still
  Python's: the assistant (`/api/ai/*`, the briefing, the review, `/api/cleanup/rationale` and `/api/screener/suggest`,
  the Claude API client, the cost governor, batches), a one-click unsubscribe (it needs an HTTPS client:
  `cleanup_wiring::NoHttps`), the export's archive (`/api/export/archive` answers that it isn't served yet), and
  `/api/debug/health` and `/api/debug/jobs`, which neither the page nor a served test calls. Twelve cleanup and RSVP tests wait on the body fetch
  when a message opens (#31). `just test-rust-all` says what passes.
- **The Rust server's background work** · high value · effort M · [#28](https://github.com/lanterno/mailmind/issues/28) — `mailmind-server` runs the change
  queue's and the outbox's workers, the snooze, sync, reconcile and draft-push rounds, and IDLE's workers, one for
  each account, which follow the accounts as they change (`mm-server/src/workers.rs`, `mm_mailbox::realtime`);
  `mm-served` drives the rounds one pass at a time through its `work` line. Still to come: `warm_tick` (a per-account `warm_folders`), backfilling bodies,
  `forget`, loading 1Password credentials before the first sync, and the jobs of areas not yet ported (assess, draft
  ahead, the brief, the review, Tidy), which wait on those areas (#6). Expiry runs on a lane of its own. Each joins `Workers::start` as a `mm_mailbox::workers::Queue`
  or a timed lane.
- **Every change tells the page, the rest of it** · medium value · effort S · [#30](https://github.com/lanterno/mailmind/issues/30) — The Mailbox's,
  Attention's and Curation's changes reach the event stream under the Python app's names (`mm-server/src/heard.rs`),
  and `/api/stats` keeps its counts until one says they moved. The outbox's Sent, Failed and DraftCopyRemoved
  are heard too, and the logins hear CredentialsRejected and an account's edit or removal (`AccountsHeard`). Still silent:
  `folder-synced` at the end of a folder's first scan, which waits on that scan (#31).
- **The sync's remaining parts** · medium value · effort M · [#31](https://github.com/lanterno/mailmind/issues/31) — A folder that opens is answered
  from what is stored, and no first scan or catch-up starts (`mm_mailbox::opening::StoredOnly`, where Python runs
  `folders.on_open`); a message that opens with no stored body says it isn't served yet (`opening::StoredBody`, where
  Python fetches it, `Ingest.fetch_body`, and prefetches the next). `mm_mailbox::ingest` leaves out a person's arrival filters (Curation's `arrival_flags`) and the
  invite a message carries kept with its body (`mm-calendar` reads it now). A move's folder catch-up
  (`Folders.touched`) and the 1Password preload aren't ported either.
- **One adapter for each port** · low value · effort S · [#32](https://github.com/lanterno/mailmind/issues/32) — The areas ported side by side left doubles: three
  readers of the rule book (`Database::rules_document`, `RulesDocument`, the Curation store's `RuleBook`), three ways of
  reading a POST's fields (`body::Fields`, `posted`, `schema`), two adapters naming the accounts (`wiring::KnownAccounts`,
  `bound::Names`), two tallies of a sender (`StoredTally`, `ContactMail::card`), two writers of the undo stack
  (`blocks::push_undo`, `mailbox_commands`), and three integer readers beside `mm_kernel::parsed::as_int` (`query::as_int`,
  `body::as_int`, `schema::number`). Keep one of each.
- **Where the Rust server still answers apart** · low value · effort S · [#33](https://github.com/lanterno/mailmind/issues/33) — Discovery reads a folder's part
  from its name only (`bound::NamedRoles`), not from what LIST said; a domain that isn't ASCII is lowered and punycoded with
  no nameprep; a failed sync or reconcile keeps Rust's class name (`Timeout`, `ConnectionRefused`) where Python kept its
  exception's, and its log line names no account; a snooze commits twice, its undo entry in a block of its own;
  a login reads its password from the Keychain each time where Python keeps it for the process, and a deleted account's
  sync status stays until a restart (`Syncing` has no `_forget_status`). Compose: an attached message/rfc822 is
  rewritten best-effort (about a third of 1,500 random cases differ from Python's bytes), encoded words a person typed
  are decoded only in UTF-8, ASCII or Latin-1, a few broken-MIME drafts reopen differently, and an original fetched to
  reply to isn't stored. Expiry: with no INTERNALDATE, a Date of `-0000` is read as UTC, where Python read it as local time.
- **The mail frame's browser tests on the Rust server** · medium value · effort M · [#10](https://github.com/lanterno/mailmind/issues/10) — They are served
  tests now, run against both servers, and take the sanitizer away through the protocol (`unsanitized on`). None passes
  on the Rust server yet, for three reasons, none of them the frame's: a body that isn't stored isn't fetched as its
  message opens (#31), and the reader calls `/api/upcoming` and `/api/ai/draft-ahead`, which answer 404 there (#6). With
  bodies stored ahead and those two 404s set aside, 39 of `test_mail_frame.py` and `test_mail_frame_scripts.py`'s 40 pass
  on it, every unsanitized case among them; the 40th opens a conversation whose bodies weren't stored. Waits on #6 and #31.
- **The core runs the migrations** · high value · effort M · [#35](https://github.com/lanterno/mailmind/issues/35) — `mailmind-server`
  starts only over a database at the version it reads (`mm_store::schema::VERSION`, 51) and leaves making and upgrading
  one to the Python app. Before the flip, the core takes the migrations over from 52, with the backup before an upgrade
  and the copy-first check `db check-upgrade` does.
- **The flip** · high value · effort M · [#11](https://github.com/lanterno/mailmind/issues/11) — Step 4: when the served suites pass against both servers and the owner has used
  the Rust server daily for two weeks, the Python server goes. Waits on #6, #10, #28, #30, #31 and #35. The two weeks
  begin once the page's routes (#6) and IDLE (#28) are on the Rust server. Then step 5 moves the page from routes to the core's
  views and commands, screen by screen, starting with the message list.

## The clients

- **Measure the desktop app on Windows and Linux** · high value · effort S · [#12](https://github.com/lanterno/mailmind/issues/12) — CI shows only that their lists open and
  scroll (`just desktop smoke`). Measure launch, idle memory and frame times on the runners, add budgets for each to
  `budgets.toml`, and decide each platform's shell from them (see The desktop app).
- **Measure the Mac's Tauri build with the screen awake** · low value · effort S · [#13](https://github.com/lanterno/mailmind/issues/13) — `desktop.mac_first_rows_ms` and
  `desktop.mac_idle_memory_mb` were taken behind a locked screen, where macOS runs the app at about half speed.
- **Run the iPhone smoke check once** · medium value · effort S · [#14](https://github.com/lanterno/mailmind/issues/14) — `just apple smoke` scrolls the list in an iPhone
  simulator on CI's Mac, and has yet to complete there.
- **Real app identifiers** · medium value · effort S · [#15](https://github.com/lanterno/mailmind/issues/15) — `app.mailmind` is a placeholder (`shells/apple/project.yml`,
  `shells/android/app/build.gradle.kts`). The real ones need the domain and the entity the store listings will name (#27).
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
  itself, so two addresses that differ only there match one rule (the strict xfail in
  `tests/unit/curation/test_classifier_in_rust.py`). No Rust string holds one: the test goes with Python at the flip (#11),
  and then the question is only whether the core should compare such addresses as the bytes the server sent.
- **Simplify the search grammar after the flip** · low value · effort S · [#22](https://github.com/lanterno/mailmind/issues/22) — `mm_discovery` copies Python's quirks (its
  `fromisoformat`, a Unicode 16 case table) only so the two answer alike. Once Python goes (#11), keep strict `before:` and
  `after:` dates and Rust's own Unicode.

## The desktop app

- **Proton Mail goes with the desktop app** — decided on 2026-11-08. Proton offers IMAP only through its Bridge, which has
  to run on the same computer as the app, so only a desktop app can count on it. The `proton` account type, the
  `proton_bridge` credential and the loopback handling stay as they are until that app exists; add no new Bridge special cases.
- **A native Mac app; the web shell on Windows and Linux** — decided on 2026-11-14 ([#26](https://github.com/lanterno/mailmind/issues/26)).
  On the Mac the native list opens in 218 ms and idles at 28 MB, against 724 ms and 83.9 MB for the same page in Tauri,
  so the native Mac app ships after the Tauri build, once the iPad shell it reuses has the screens. Windows and Linux
  keep the web shell unless their measurements (#12) miss a budget, with a GTK4 shell in Rust as Linux's fallback.

## Open decisions

These are carried forward from the original phase plan because they need a call, not an
implementation.

- **How aggressively to prefetch folders** · [#23](https://github.com/lanterno/mailmind/issues/23) — on-demand sync bounds disk and first-sync cost, but
  unified-inbox-across-accounts and global search both want broader coverage.
- **Where the data directory lives by default** · [#24](https://github.com/lanterno/mailmind/issues/24) — `MAILMIND_HOME` names one, and unset it is the repo
  folder, where it has always been. Left for later on 2026-11-03.
- **Default remote-image policy** · [#25](https://github.com/lanterno/mailmind/issues/25) — block-by-default with per-message "Load images" (current, and the
  privacy-preserving choice) vs. allowing images for known/personal senders.
- **The client's licence, a legal entity, and who runs the push relay** · [#27](https://github.com/lanterno/mailmind/issues/27) — the architecture document's risks section
  recommends an answer for each. The push relay waits on the iPhone app; the other two on the first store listing.

## Next up

1. **Step 3: the Rust server on every route** (high) — the assistant and IDLE are under way; then the sync's remaining
   parts (#31), the body fetch first, and the migrations (#35) before the flip. See The move to the Rust core.
2. **The owner's two weeks on the Rust server** (high) — they begin once the page's routes (#6) and IDLE (#28) are served,
   and they end in the flip (#11). `MAILMIND_WORKERS=account` (#3) can soak in them, or now: both servers read it.
