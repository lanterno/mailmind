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
`POST /api/rules` keeps or blocks only one address or one domain with a dot in it (`rulebook.names_a_sender`, and the
core's): a number pasted before an address, two addresses or a name in brackets would make a rule that never matches,
so it is refused in words the page and the Mac app show. A removal asks only `is_token`, so a rule kept before still goes.
It also holds a person's filters (`curation.domain.filters`): a search that stars or marks read. `Ingest` runs them only on
mail never seen before, on an incremental sync of the Inbox, and the server hears through the command path; a read-only process
runs none, since a mark kept here alone would be taken back by the same sync's read of the flags. A filter takes the search's
grammar and its index (`discovery.Conditions`), and refuses what mail arriving can't answer yet (`body:`, `has:attachment`, `category:`, dates).
`curation.adapters.rules_document` (`bootstrap.curation.rules`, the document's one reader and writer) writes it whole, a version on,
and a change reads, changes and writes it in one write transaction, which takes the database's write lock before it reads, so the
command line and the server, or two threads of either, can't lose each other's: the second waits, then reads what the first wrote.
A read asks the document's version and parses it again only when that moved. The classifier reads through `rules.sets()`. The
`rules` commands bring the database to the newest schema first, as a sync does.

A rule change classifies again the stored mail it covers (`ingest.reclassify`, called by whatever calls
`rules.add` or `rules.remove`): each row a rule decides now or decided before. A row no rule had a say in
stays as ingest judged it, since not all it read then is kept. A document written another way, as an import is, reaches only new mail.

A block, by Screen out at the Screener's gate or in Curate's rules, moves nothing: the sender's mail stays where it
is, read now as `BLOCKED`, as their mail arriving later will be, and a sweep files it as it files any junk. Junk asks
nothing of you (`kernel.attention.answers`, the core's `verdict::answers` and `listing::attention`): a row read as junk
needs you in no list, whatever the AI said of it before the block, so the Inbox's Needs you leaves it out as the brief
does, and both count the same. What the AI said stays, and needs you again once the rule goes. A sender's cleanup is the
way to block and file their mail at once, under one Undo.

Ingest keeps a message's unsubscribe headers (`messages.list_unsub_raw` and `list_unsub_method`, through
`mime.list_unsub`), `none` included. `Unsubscribing.resolve` answers for a sender from the newest message
that can say: those columns, a stored body, or for older mail the two headers read off the server, within
`UNSUB_READ_WAIT` on the account's gate. `sender_unsub` (migration 34) keeps the answer past the sender's mail,
so a second try after a cleanup's sweep still knows the way. The senders list names each sender's way out
(`unsub_method`: `one-click`, `mailto`, `http-page`, or none) from the newest of its stored mail whose columns say one,
before any preview, and a sender none of whose headers were read names none.

An account's sweep (`Sweeps.sweep`, `mm_curation::sweeping`) reads the Inbox off the server, flags and all, and classifies
it as it is now. Junk that is starred (`\Flagged`) or unread (no `\Seen`, or flags the server didn't say) stays, and
the answer counts it (`held_back`). It takes the newest `SWEEP_MAX` (500) of the rest by UID and says when that left
some (`capped`). A dry run's plan lists what goes (`going`): each junk bucket that has any, in the classifier's order,
with its count, the `LISTED` (5) senders with the most of it and its newest five subjects. Once the account's first
scan has read its Inbox whole (`sync_state.scan_state` `synced`), the dry run reads the stored Inbox instead
(`Sweeps.preview`, the core's `ScannedInbox`), so it answers at once on an Inbox of any size, asks no server and waits
for no gate: each row is read as a sweep reads one off the server (`sweep_plan.read_from_stored`), its List headers as
ingest saw them (`listed`: its bucket or its kept way out) and its flags as the last sync left them. Until then (a
first scan still reading older mail, `down-to:N`, or only warmed, `recent`) the dry run reads the server. Confirm always
reads the Inbox off the server, once, and moves what it says is still junk, still read and still unstarred: junk
starred or marked unread elsewhere since the last sync stays, and junk read elsewhere since may go too. Once the Inbox
counts as read whole, Confirm takes nothing older than the stored Inbox's oldest message (`sweep_plan.since_stored`,
the core's `sweeping::since_stored`), and with nothing stored it takes nothing: a sync that read only recent mail
(`mailmind sync`'s last N days, or a folder migration 24 took as read whole) leaves the Inbox counted whole without
storing what came before, so the preview never named it. `mailmind sweep` plans off the server and sweeps what that
plan read. It audits, moves and verifies as before, then puts a way back for each message that went under one undo
group: the request's `undo_group`, or a `sweep:` one of its own, which the answer names (null when nothing went). Each child is a
`sweep_back` from the Trash to the Inbox, an account's put back together on one session and found again by its
Message-ID, so one with none goes but can't come back (`no_undo`). The Trash is the one the server lists, found as the
sweep's session opens (`session.find_trash`, the core's `LiveSession::trash`): the mail goes there and its way back
names it; a server whose folders can't be listed moves nothing. A sweep the server stops part way reads the Inbox
again on a fresh session: what left it is `moved`, has its rows forgotten and a way back, and a sweep that moved nothing
leaves the undo stack as it was ([Writes and Undo](writes-and-undo.md)).

The Curate card's numbers come from the stored plan (`plan_stored`, `mm_curation::sweep_plan::StoredPlans`), with no
server asked: `junk` and `keep` as the classifier judges the stored Inbox now, and `sweepable`, the junk a sweep would
move, stored as read (`seen`) and not starred (`flagged`). The card's **Sweep** button offers `sweepable` and isn't
there when it is none; Undo is offered after a sweep only when it moved something, or stopped where the server couldn't
be asked again how much went (`moved` 0 with an `undo_group`): the words say so, and Undo puts back whatever did.

The preview's `confirm_token` (`perimeter.tokens`) is `<time>.<hmac>`, over the purpose, the sender, the way out it showed
and where that goes (`unsubscribe.preview_said`), under the process's secret. It stands for `TOKEN_LIFE`, ten minutes. A cleanup with one out of date
or naming another way out sends nothing and answers `no_token`, and its block and sweep stand.
