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
- **Keep the sweep's stored preview true to marks changed elsewhere** · low value · effort S ·
  [#65](https://github.com/lanterno/mailmind/issues/65) — Once a first scan has read the Inbox whole, Sweep's preview
  and the Curate card count from the stored read and star marks (`ScannedInbox`, `StoredPlans`), which a sync reads
  again only for the newest 500 stored messages (`sync_plan::FLAG_REFRESH`) where the server has no CONDSTORE. Older
  mail read or starred in another client is counted on the wrong side; Confirm reads the server and moves only what is
  still read and unstarred there, so what it moves can differ from what the preview listed.

## Speed and the connection layer

- **Let the sync, the older-mail read, IDLE and the folder rail use the kept connection** · medium value · effort M ·
  [#54](https://github.com/lanterno/mailmind/issues/54) — Each account's kept connection (`mm_mailbox::kept`) serves
  only the jobs that hold its gate. The background sync, a folder's read of older mail, IDLE and the folder rail take
  no gate, so each still logs in for itself, and together they are most of an account's logins, which Proton Bridge's
  low cap feels most. Let them borrow the kept connection when it is free, or keep one of their own.
- **Read a smaller opening of older mail in a first scan** · medium value · effort S ·
  [#53](https://github.com/lanterno/mailmind/issues/53) — A first scan reads every message's first 64 KiB
  (`mm_mailbox::ingest::HEAD_BYTES`) for its preview, mail past 30 days too, which nothing summarizes unasked and whose
  preview needs far less. Read a smaller opening there, and leave the rest to the backfill or to opening it.
- **Set no backfill message aside when the login fails** · low value · effort S ·
  [#58](https://github.com/lanterno/mailmind/issues/58) — A backfill batch whose try ends unreachable sets each of its
  messages aside and sends each alone from then on (`body_backfill::connection_lost`), even when the login or the
  connection failed before any message was asked for. Rest the account then, and leave its messages as they were.
- **Enable CONDSTORE only for a job that needs it** · low value · effort S ·
  [#55](https://github.com/lanterno/mailmind/issues/55) — Every kept connection sends `ENABLE CONDSTORE` as it opens,
  where the server offers it (`mm_mailbox::kept`), whether or not a job lent it reads by MODSEQ.
- **Unselect a kept connection's folder between jobs** · low value · effort S ·
  [#57](https://github.com/lanterno/mailmind/issues/57) — A job lent the kept connection may not use the last job's
  folder, but nothing sends `UNSELECT`, so the server keeps it selected. Send it where offered (RFC 3691), never `CLOSE`.
- **Cut the round trips the IMAP counts found** · medium value · effort S ·
  [#69](https://github.com/lanterno/mailmind/issues/69) — `budgets.toml`'s `[counts]` show a move searching the target
  for the UID its `COPYUID` already gave, a reply listing every folder before its `APPEND` to Sent, and a kept
  connection selecting the Inbox it already has. Lower each limit as its count comes down.
- **A new database's key refused once on CI's Linux** · low value · effort S ·
  [#70](https://github.com/lanterno/mailmind/issues/70) — `Store::open` on a new file failed with SQLCipher's catch-all
  keying error once, with four other tests keying databases beside it. Find what can fail when several connections are
  keyed at once, since the app opens several as it starts.
- **Log the kept connections out on Ctrl-C or SIGTERM** · low value · effort S ·
  [#56](https://github.com/lanterno/mailmind/issues/56) — `mailmind-server` logs them out as it stops serving, but has
  no signal handler, so a Ctrl-C or a SIGTERM ends it without the LOGOUTs. The Mac app's `Host::close` sends them.
- **Let the last jobs that wait for every account's gate take only their own** · medium value · effort S · [#3](https://github.com/lanterno/mailmind/issues/3) —
  A gate for each account is the Rust server's default (`environment::gate_per_account`), but two jobs still pass
  `EVERY_ACCOUNT`, which waits for every gate: a draft save whose old copy is in another account (`mm_compose::drafting`;
  it needs just the two accounts' gates, which the gates can't take yet), and an undo entry that names no account, as a
  snooze's does (`mm_mailbox::undo`), though undoing a snooze asks no server anything. #3 also asks that
  `MAILMIND_WORKERS` go once the two weeks on the Rust server (Next up) have soaked a gate for each account.

## The move to the Rust core

Step 3 of the "MailMind client architecture" document is done ([docs/clients.md](docs/clients.md)): the Rust server
(`core/crates/mm-server`) answers every route the Python app does, the debug routes last, and every served test passes
on it against the same scripts, as `tests/support/rust_ledger.txt` and `just test-rust` hold. Until the flip, the
Python app gets fixes only.

- **One adapter for each port** · low value · effort S · [#32](https://github.com/lanterno/mailmind/issues/32) — The areas ported side by side left doubles: three
  readers of the rule book (`Database::rules_document`, `RulesDocument`, the Curation store's `RuleBook`), three ways of
  reading a POST's fields (`body::Fields`, `posted`, `schema`), two adapters naming the accounts (`wiring::KnownAccounts`,
  `bound::Names`), two tallies of a sender (`StoredTally`, `ContactMail::card`), two writers of the undo stack
  (`blocks::push_undo`, `mailbox_commands`), and three integer readers beside `mm_kernel::parsed::as_int` (`query::as_int`,
  `body::as_int`, `schema::number`). Keep one of each.
- **Where the Rust server still answers apart** · low value · effort S · [#33](https://github.com/lanterno/mailmind/issues/33) — Discovery reads a folder's part
  from its name only (`bound::NamedRoles`), not from what LIST said; a domain that isn't ASCII is lowered and punycoded with
  no nameprep; a failed sync or reconcile keeps Rust's class name (`Timeout`, `ConnectionRefused`) where Python kept its
  exception's, and its log line names no account, as a job's `last_error` does in `/api/debug/jobs`, which lists the
  jobs in another order; a snooze commits twice, its undo entry in a block of its own;
  a login reads its password from the Keychain each time where Python keeps it for the process. Compose: an attached message/rfc822 is
  rewritten best-effort (about a third of 1,500 random cases differ from Python's bytes), encoded words a person typed
  are decoded only in UTF-8, ASCII or Latin-1, a few broken-MIME drafts reopen differently, and an original fetched to
  reply to isn't stored. Expiry: with no INTERNALDATE, a Date of `-0000` is read as UTC, where Python read it as local time. Two Rust servers
  starting at once on a fresh machine can each make a database key, the second's replacing the first's in the Keychain,
  where Python's second finds the first's (`database_version::made`). AI (#49): a Claude call that runs out of time says
  the SDK's longer words on Python ("Request timed out or interrupted…") where Rust says "Request timed out."; a batch's
  results, at a gateway whose URL has a path, are asked where `results_url` names them on Python and under the URL's
  path on Rust; and a count of tokens given as a digit string past 2^63−1, or as `true` on the Chat Completions wire,
  reads as the most or 1 on Python and 0 on Rust.
- **The rarer broken MIME parameters** · low value · effort M · [#48](https://github.com/lanterno/mailmind/issues/48) — A scan of the `name` and
  `filename` parameters found broken mail the core reads apart from Python, which the property tests rarely draw: a stray
  quote in an earlier parameter, a value or name starting with a tspecial, `*` or `\`, an apostrophe in an unquoted value,
  an unclosed comment in the type, and three two-byte boundary cases. Decide each by the RFCs and mail clients, and mend
  the core where Python's reading is the sound one.
- **Two mailbox browser tests that fail now and then on the Rust server** · low value · effort S · [#50](https://github.com/lanterno/mailmind/issues/50) — Under
  `just test-rust`'s four browsers, archiving that opens the next message sometimes logs a 404, and `g` then a letter
  sometimes hasn't changed the folder within 5 s; each passes alone. Find the request that 404s and what the folder
  switch waits on, and have the page or the test wait on what it means, not on the clock.
- **Python browser tests that fail now and then only on CI's served run** · low value · effort S · [#66](https://github.com/lanterno/mailmind/issues/66) —
  Two autosave tests in `test_compose.py` count saves against the clock, `test_docs.py`'s server stops before its test,
  `test_suggestions.py`'s renumber logs a 404, and three more (`test_details.py`, `test_guide.py`, the Hub's axe check)
  look before the server has settled what it shows; all pass on a Mac. Have the page or the test wait on what it means.
- **Backfill several messages a FETCH on the Python server** · low value · effort S ·
  [#59](https://github.com/lanterno/mailmind/issues/59) — The Rust server's backfill asks for a folder's dozen in one
  FETCH; the Python app's (`Syncing.backfill_round`) fetches one message at a time. Worth it only if the flip (#11)
  waits long; otherwise it closes with the flip.
- **The flip** · high value · effort M · [#11](https://github.com/lanterno/mailmind/issues/11) — Step 4: when the served suites pass against both servers and the owner has used
  the Rust server daily for two weeks (`just run-rust`), the Python server goes. Step 3 is done, so it waits only on
  those two weeks; `/api/debug/health`, `/api/debug/jobs` and `/api/debug/ai` say how the jobs, the queues and AI
  spending stand meanwhile. Then step 5 moves the page from routes to the core's views and commands, screen by screen,
  starting with the message list.

## The clients

- **Measure the desktop app on Windows and Linux** · high value · effort S · [#12](https://github.com/lanterno/mailmind/issues/12) — CI shows only that their lists open and
  scroll (`just desktop smoke`). Measure launch, idle memory and frame times on the runners, add budgets for each to
  `budgets.toml`, and decide each platform's shell from them (see The desktop app).
- **Run the iPhone smoke check once** · medium value · effort S · [#14](https://github.com/lanterno/mailmind/issues/14) — `just apple smoke` scrolls the list in an iPhone
  simulator on CI's Mac, and has yet to complete there.
- **Real app identifiers** · medium value · effort S · [#15](https://github.com/lanterno/mailmind/issues/15) — `app.mailmind` is a placeholder on Android
  (`shells/android/app/build.gradle.kts`, the Kotlin package in `core/crates/mm-ffi/uniffi.toml`); the Apple app is
  `io.github.lanterno.mailmind`. Android's needs the domain and the entity the store listings will name (#27).
- **Sign in with Google and Microsoft** · high value · effort L · [#47](https://github.com/lanterno/mailmind/issues/47) — Sign in in the browser, as Spark
  does, in place of an App Password: OAuth for an installed app, the token in the Keychain, XOAUTH2 in `mm-imap` and
  `mm-smtp`. Microsoft can come first; Google waits on the owner: a Cloud project, restricted-scope verification for
  `https://mail.google.com/` (and perhaps the Gmail API in place of IMAP), and possibly a yearly security assessment.
- **A removed account's mail stays in the backups** · medium value · effort S ·
  [#60](https://github.com/lanterno/mailmind/issues/60) — Removing an account can delete its mail from the database,
  but `backups/` keeps it: the copy before each upgrade until newer ones replace it (the newest three are kept), and a
  copy Start Fresh moved aside until it is deleted, since nothing prunes those. Take it out of them too, or say in
  Settings that they keep it, and until when.
- **Warn of every draft not yet on its server when an account is removed** · low value · effort S ·
  [#62](https://github.com/lanterno/mailmind/issues/62) — The Mac app's Remove dialog counts unsent mail from the outbox
  tray (`Unsent.read`), which lists a draft only once its push is overdue (`WAITING_AFTER`), so a draft saved in the
  last minute or so, an autosave's push not yet due (`PUSH_EVERY`), goes with the account's mail unwarned.
- **Say what a partial Undo leaves for a full run** · low value · effort S ·
  [#61](https://github.com/lanterno/mailmind/issues/61) — In read only, an Undo of a group that only partly goes back
  answers 200 with `reversed` and `failed` (`mm_mailbox::undo`'s `reverse_entry`, `mailmind.platform.undo`), not the
  read-only 409, and the clients then suggest pressing z again, which read only refuses every time. Say how many wait
  for a full run, and have the page and the Mac app say so.
- **Let Start Fresh get past a Keychain item that isn't a key** · low value · effort S ·
  [#63](https://github.com/lanterno/mailmind/issues/63) — A start whose Keychain item for the database's key isn't one
  offers Start Fresh (`database_version`'s `NOT_A_KEY`), which moves the database aside but leaves the item, so the next
  start reads the same item and refuses the same way.
- **Tell launch failures apart by a kind, not by words** · low value · effort S ·
  [#64](https://github.com/lanterno/mailmind/issues/64) — The Mac app's `LaunchFailure` offers Start Fresh and Try Again
  by looking for phrases in the core's English sentence (`startsFresh`, `triesAgain`), so a change of wording changes
  its buttons. Have the host's start failure carry a kind through `mm-ffi`.
- **Tauri's accepted advisories** · medium value · effort S · [#16](https://github.com/lanterno/mailmind/issues/16) — `shells/desktop/src-tauri/osv-scanner.toml` ignores seven
  until 2027-02-12. Upgrade past them, or look at each again, before then.

## Showing and sorting mail

- **Where the reader still differs from a browser** · low value · effort M · [#37](https://github.com/lanterno/mailmind/issues/37) — `mm_content::sanitize`
  shows what a browser shows of the elements it drops, but stays short of it where that was safer: an `<object>` of an
  image type or with file: or ftp: data, HTML inside an svg or math integration point (`foreignObject`, `desc`, `title`,
  `annotation-xml`), `<applet>`'s content, a closed `<xml>`'s text, and a nested form a browser leaves open
  (`<form>a<div>b<form>c</form>d</div>e</form>f` shows `f`, Chromium `ef`). It shows more than a browser of `<video>` and
  `<audio>` content and of `<noembed>` and `<noframes>` text; an ancestor's end tag never ends a dropped element
  (`<div><svg></div>after` loses `after`); `</br>` isn't a line break; and an unwrapped element loses its `class` and
  `id`. A snippet still leaves out `<noscript>` text the reader now shows (`mime/fields.rs`).
- **Reclassify mail sorted under the old patterns** · medium value · effort S · [#20](https://github.com/lanterno/mailmind/issues/20) — the core's classifier
  (`mm_curation::classifier`) no longer matches its patterns inside longer words, but mail sorted before keeps its bucket:
  only rows a rule covers are sorted again.
- **A lone surrogate in a sender's name** · low value · effort S · [#21](https://github.com/lanterno/mailmind/issues/21) — the core reads one as U+FFFD where Python read it as
  itself, so two addresses that differ only there match one rule (the strict xfail in
  `tests/unit/curation/test_classifier_in_rust.py`). No Rust string holds one: the test goes with Python at the flip (#11),
  and then the question is only whether the core should compare such addresses as the bytes the server sent.
- **Say when each snooze preset wakes** · low value · effort S · [#45](https://github.com/lanterno/mailmind/issues/45) — Meadow's snooze menu shows
  each preset's time beneath it. Only the server and the core work those times out (`wake_time`), and no route returns
  them, so the Mac app's chooser and the page's `SnoozeMenu` only name the presets: a route, or a field on one, that
  says each preset's time from the same function.
- **Simplify the search grammar after the flip** · low value · effort S · [#22](https://github.com/lanterno/mailmind/issues/22) — `mm_discovery` copies Python's quirks (its
  `fromisoformat`, a Unicode 16 case table) only so the two answer alike. Once Python goes (#11), keep strict `before:` and
  `after:` dates and Rust's own Unicode.

## The desktop app

- **Proton Mail is the Mac app's, through Bridge** — decided on 2026-11-08. Proton offers IMAP only through its Bridge,
  which has to run on the same computer as the app, so only a desktop app can count on it: the Mac app reaches Proton
  through Bridge, with the `proton` account type, the `proton_bridge` credential and the loopback handling.
- **A native Mac app now; the web shell on Windows and Linux** — decided on 2026-11-14 ([#26](https://github.com/lanterno/mailmind/issues/26)),
  and that day the owner chose to build the native Mac app now, in place of shipping the Tauri build first. On the Mac
  the native list opens in 218 ms and idles at 28 MB, against 724 ms and 83.9 MB for the same page in Tauri. It runs
  the core in its own process and reaches it through the routes the page calls, answered in-process, so every use case
  is the one the served suites hold; step 5's views and commands can replace them screen by screen later. Its
  identifier is `io.github.lanterno.mailmind`; it is signed ad hoc until there is a Developer ID (#42), and updates
  through Sparkle from the repo's GitHub Releases. Windows and Linux keep the web shell unless their measurements (#12)
  miss a budget, with a GTK4 shell in Rust as Linux's fallback.
- **Developer ID signing and notarization** · medium value · effort S · [#42](https://github.com/lanterno/mailmind/issues/42) — Waits on the
  owner's paid Apple Developer account.
- **Enter App Password on the Sync card for a provider on its own domain** · low value · effort S · [#67](https://github.com/lanterno/mailmind/issues/67) —
  The card finds the provider by the address's domain, the core's test words by the IMAP host first: an iCloud or
  Yahoo account on a domain of its own gets Change Password… while its test asks for an app password.

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

1. **The owner's two weeks on the Rust server** (high) — step 3 is done: every route the Python app answers is served
   there, the debug routes too, and a message whose body isn't stored opens. `just run-rust` starts them, in place of
   `just run`, and so does the Mac app, which runs only the Rust core; they end in the flip (#11), which waits on
   nothing else. A gate for each account, the Rust server's default, soaks in them too.
2. **Developer ID signing** (medium) — the Mac app signed and notarized
   ([#42](https://github.com/lanterno/mailmind/issues/42)), which waits on the owner's Apple account.
