# Classification

`curation.domain.classifier` assigns one of seven buckets (`SPAM`, `BLOCKED`, `MKT`, `NOTIF`,
`RECEIPT`, `PROTECTED`, `PERSONAL`); `JUNK_BUCKETS` decides what ingest drops. It's a pure function of a
message's headers and the rule book's lists, and `bootstrap.curation.classify` calls it with the rules as they stand.
The Rust core computes it (`mm_curation::classifier`, through `mailmind_leaves`), as every client will; the Python
module keeps the bucket names and builds the core's copy of the rule sets once each time the rules change
(`rulebook.as_sets`), so classifying a message costs under a microsecond however many rules there are.
A person's reply or forward in a list's thread (a subject that opens `Re:`, `Fwd:` or a kin of them, from an address
`looks_human` says is a person's) is `PERSONAL`, read before the list headers make it marketing: a group's, a club's or
a school's list puts its headers on every reply. The list's own mail, a role's address (`news@`) and a no-reply one stay
marketing.
`curation.domain.rulebook` is a learned per-sender/domain keep/block list on top, kept in the database, which is encrypted
([At rest](security.md#at-rest)), as the `rules` document (`platform.db.documents`, migration 48). Its body is the JSON
the rule book's old file held; nothing reads or writes a file of it any more. The same
document holds expiry rules (`expire_hours`: sender/domain → hours), enforced by
`Expiry.account` from the CLI and by the server's `expire` job hourly (not read-only).
`rules.remove` deliberately leaves expiry alone; clear it with `set_expiry(token, 0)`.
It also holds a person's filters (`curation.domain.filters`): a search that stars or marks read. `Ingest` runs them only on
mail never seen before, on an incremental sync of the Inbox, and the server hears through the command path. A filter takes the search's
grammar and its index (`discovery.Conditions`), and refuses what mail arriving can't answer yet (`body:`, `has:attachment`, `category:`, dates).
`curation.adapters.rules_document` (`bootstrap.curation.rules`, the document's one reader and writer) writes it whole, a version on,
and a change reads, changes and writes it in one write transaction, which takes the database's write lock before it reads, so the
command line and the server, or two threads of either, can't lose each other's: the second waits, then reads what the first wrote.
A read asks the document's version and parses it again only when that moved. The classifier reads through `rules.sets()`. The
`rules` commands bring the database to the newest schema first, as a sync does.

A rule change classifies again the stored mail it covers (`ingest.reclassify`, called by whatever calls
`rules.add` or `rules.remove`): each row a rule decides now or decided before. A row no rule had a say in
stays as ingest judged it, since not all it read then is kept. A document written another way, as an import is, reaches only new mail.

Ingest keeps a message's unsubscribe headers (`messages.list_unsub_raw` and `list_unsub_method`, through
`mime.list_unsub`), `none` included. `Unsubscribing.resolve` answers for a sender from the newest message
that can say: those columns, a stored body, or for older mail the two headers read off the server, within
`UNSUB_READ_WAIT` on the account's gate. `sender_unsub` (migration 34) keeps the answer past the sender's mail,
so a second try after a cleanup's sweep still knows the way.

An account's sweep (`Sweeps.sweep`, `mm_curation::sweeping`) reads the Inbox off the server, flags and all, and classifies
it as it is now. Junk that is starred (`\Flagged`) or unread (no `\Seen`, or flags the server didn't say) stays, and
the answer counts it (`held_back`). It takes the newest `SWEEP_MAX` (500) of the rest by UID and says when that left
some (`capped`). A dry run's plan lists what goes (`going`): each junk bucket that has any, in the classifier's order,
with its count, the `LISTED` (5) senders with the most of it and its newest five subjects. A real one audits, moves
and verifies as before, then puts a way back for each message that went under one undo group: the request's
`undo_group`, or a `sweep:` one of its own, which the answer names (null when nothing went). Each child is a
`sweep_back` from the Trash to the Inbox, an account's put back together on one session and found again by its
Message-ID, so one with none goes but can't come back (`no_undo`). The Trash is the one the server lists, found as the
sweep's session opens (`session.find_trash`, the core's `LiveSession::trash`): the mail goes there and its way back
names it; a server whose folders can't be listed moves nothing. A sweep the server stops part way reads the Inbox
again on a fresh session: what left it is `moved`, has its rows forgotten and a way back, and a sweep that moved nothing
leaves the undo stack as it was ([Writes and Undo](writes-and-undo.md)).

The Curate card's numbers come from the stored plan (`plan_stored`, `mm_curation::sweep_plan::StoredPlans`), with no
server asked: `junk` and `keep` as the classifier judges the stored Inbox now, and `sweepable`, the junk a sweep would
move, stored as read (`seen`) and not starred (`flagged`). The card's **Sweep** button offers `sweepable` and isn't
there when it is none; Undo is offered after a sweep only when it moved something.

The preview's `confirm_token` (`perimeter.tokens`) is `<time>.<hmac>`, over the purpose, the sender, the way out it showed
and where that goes (`unsubscribe.preview_said`), under the process's secret. It stands for `TOKEN_LIFE`, ten minutes. A cleanup with one out of date
or naming another way out sends nothing and answers `no_token`, and its block and sweep stand.
