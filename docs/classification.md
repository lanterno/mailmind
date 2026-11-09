# Classification

`curation.domain.classifier` assigns one of seven buckets (`SPAM`, `BLOCKED`, `MKT`, `NOTIF`,
`RECEIPT`, `PROTECTED`, `PERSONAL`); `JUNK_BUCKETS` decides what ingest drops. It's a pure function of a
message's headers and the rule book's lists, and `bootstrap.curation.classify` calls it with the rules as they stand.
`curation.domain.rulebook` is a learned per-sender/domain keep/block list on top, persisted to
`mailmind_rules.json` — that file is personal data and stays gitignored. The same file
holds expiry rules (`expire_hours`: sender/domain → hours), enforced by
`Expiry.account` from the CLI and by the server's `expire` job hourly (not read-only).
`rules.remove` deliberately leaves expiry alone; clear it with `set_expiry(token, 0)`.
The file also holds a person's filters (`curation.domain.filters`): a search that stars or marks read. `Ingest` runs them only on
mail never seen before, on an incremental sync of the Inbox, and the server hears through the command path. A filter takes the search's
grammar and its index (`discovery.Conditions`), and refuses what mail arriving can't answer yet (`body:`, `has:attachment`, `category:`, dates).
`curation.adapters.rules_file` (`bootstrap.curation.rules`, the file's one reader) writes it whole (a temp file,
owner-only, renamed over it), and a change reads, changes and saves as one step, under a file lock (`.rules.lock`), so the command line and the server can't lose each other's. A read parses it again only when it changed; one that won't parse leaves the last
rules read well, so a bad edit never stops a sync, and the next save moves it to `backups/` first. The classifier
reads through `rules.sets()`.

A rule change classifies again the stored mail it covers (`ingest.reclassify`, called by whatever calls
`rules.add` or `rules.remove`): each row a rule decides now or decided before. A row no rule had a say in
stays as ingest judged it, since not all it read then is kept. An edit of the file by hand reaches only new mail.

Ingest keeps a message's unsubscribe headers (`messages.list_unsub_raw` and `list_unsub_method`, through
`mime.list_unsub`), `none` included. `Unsubscribing.resolve` answers for a sender from the newest message
that can say: those columns, a stored body, or for older mail the two headers read off the server, within
`UNSUB_READ_WAIT` on the account's gate. `sender_unsub` (migration 34) keeps the answer past the sender's mail,
so a second try after a cleanup's sweep still knows the way.

The preview's `confirm_token` (`perimeter.tokens`) is `<time>.<hmac>`, over the purpose, the sender, the way out it showed
and where that goes (`unsubscribe.preview_said`), under the process's secret. It stands for `TOKEN_LIFE`, ten minutes. A cleanup with one out of date
or naming another way out sends nothing and answers `no_token`, and its block and sweep stand.
