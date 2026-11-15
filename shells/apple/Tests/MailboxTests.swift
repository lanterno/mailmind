import Foundation
import XCTest

/// The mailbox's words, places and rows say what the page says of them.
final class MailboxWordsTests: XCTestCase {
    func testFilingIsToldInThePagesWords() {
        XCTAssertEqual(Words.filed("Archived", count: 1, failed: 0, queued: false, readOnly: false), "Archived")
        XCTAssertEqual(Words.filed("Archived", count: 3, failed: 0, queued: false, readOnly: false), "Archived 3 messages")
        XCTAssertEqual(Words.filed("Moved", count: 2, failed: 1, queued: false, readOnly: false, to: "Archive"),
                       "Moved 2 of 3 to Archive — the rest stayed")
        XCTAssertEqual(Words.filed("Trashed", count: 1, failed: 0, queued: true, readOnly: false), "Trashed · once the server is back")
        XCTAssertEqual(Words.filed("Trashed", count: 1, failed: 0, queued: true, readOnly: true), "Trashed · waits for a full run")
    }

    func testAListIsCountedAsItsHeadCountsIt() {
        XCTAssertEqual(Words.count(1, more: false, noun: "message"), "1 message")
        XCTAssertEqual(Words.count(1, more: true, noun: "result"), "1+ results")
        XCTAssertEqual(Words.count(0, more: false, noun: "conversation"), "0 conversations")
    }

    func testASenderAndAFileAreSaidShortly() {
        XCTAssertEqual(Words.sender(count: 12, replied: 3, firstAgo: "2y"), "12 emails · replied 3× · first 2y ago")
        XCTAssertEqual(Words.sender(count: 1, replied: 0, firstAgo: "3d"), "1 email · replied 0× · first 3d ago")
        XCTAssertEqual(Words.sender(count: 2, replied: 0, firstAgo: nil), "2 emails · replied 0×")
        XCTAssertEqual(Words.size(0), "")
        XCTAssertEqual(Words.size(512), "512 B")
        XCTAssertEqual(Words.size(2048), "2 KB")
        XCTAssertEqual(Words.size(3_670_016), "3.5 MB")
        XCTAssertEqual(Words.short("ada@example.test"), "ada")
    }

    func testAFolderIsNamedByItsRole() {
        XCTAssertEqual(Words.role(Fixture.folder("INBOX", role: "inbox")), "Inbox")
        XCTAssertEqual(Words.role(Fixture.folder("[Gmail]/All Mail", role: "all")), "All mail")
        XCTAssertEqual(Words.role(Fixture.folder("Receipts", role: "custom")), "Receipts")
    }

    func testAMonogramIsTheNamesCapitalsAndAHueTheAddressAlwaysGets() {
        XCTAssertEqual(Monogram.initials(name: "Library of Ash Hollow", address: nil), "LA")
        XCTAssertEqual(Monogram.initials(name: "grace hopper", address: nil), "GH")
        XCTAssertEqual(Monogram.initials(name: "Zoë Ångström", address: nil), "ZÅ")
        XCTAssertEqual(Monogram.initials(name: "ada@example.test", address: "ada@example.test"), "A")
        XCTAssertEqual(Monogram.initials(name: nil, address: "7up@example.test"), "7")
        XCTAssertEqual(Monogram.hue("ada@example.test"), Monogram.hue("ada@example.test"))
        XCTAssert((0..<360).contains(Monogram.hue("ada@example.test")))
    }
}

final class MailboxPlacesTests: XCTestCase {
    func testAPlaceSaysItsAccountAndWhetherItIsAnInbox() {
        XCTAssertTrue(Place.allInboxes.isInbox)
        XCTAssertTrue(Place.folder(account: Fixture.ada, folder: "INBOX").isInbox)
        XCTAssertFalse(Place.folder(account: Fixture.ada, folder: "Archive").isInbox)
        XCTAssertFalse(Place.virtual(.snoozed, account: nil).isInbox)
        XCTAssertEqual(Place.virtual(.waiting, account: Fixture.ada).account, Fixture.ada)
        XCTAssertNil(Place.search(Search(q: "x")).account, "a search from no account is every account's")
        XCTAssertEqual(Place.search(Search(q: "x", account: Fixture.work)).account, Fixture.work)
        XCTAssertFalse(Place.folder(account: Fixture.ada, folder: "INBOX").showsAccount, "one account's folder needs no account")
        XCTAssertTrue(Place.allInboxes.showsAccount)
    }

    func testAPlaceIsTitledAsThePageTitlesIt() {
        XCTAssertEqual(Place.allInboxes.title(folders: []), "Inbox")
        XCTAssertEqual(Place.folder(account: Fixture.ada, folder: "Receipts").title(folders: Fixture.adaFolders), "Receipts")
        XCTAssertEqual(Place.virtual(.replyLater, account: nil).title(folders: []), "Reply later")
        XCTAssertEqual(Place.search(Search(q: "x", lane: .papertrail)).title(folders: []), "Paper Trail")
        XCTAssertEqual(Place.documents.title(folders: []), "Documents")
    }

    func testSnoozedIsListedEmptyAndTheOthersOnlyWithSomethingIn() {
        XCTAssertTrue(VirtualFolder.snoozed.shown(count: 0))
        XCTAssertFalse(VirtualFolder.muted.shown(count: 0))
        XCTAssertTrue(VirtualFolder.waiting.shown(count: 2))
        XCTAssertEqual(VirtualFolder.waiting.count(muted: 1, replylater: 2, snoozed: 3, waiting: 4), 4)
    }

    func testALabelAndAViewAreSearchesInThePagesGrammar() {
        XCTAssertEqual(labelQuery("Travel"), #"label:"Travel""#)
        XCTAssertEqual(labelQuery(#"Say "hi" \ there"#), #"label:"Say \"hi\" \\ there""#)
        let view = API.ListedView(builtin: true, dynamic: "last7", id: "__last7__", name: "Last 7 days", q: "__LAST7__")
        let now = ISO8601DateFormatter().date(from: "2026-11-15T12:00:00Z")!
        XCTAssertEqual(viewQuery(view, now: now), "after:2026-11-08")
        XCTAssertEqual(Lane(view: "__lane_feed__"), .feed)
        XCTAssertNil(Lane(view: "v1"))
    }

    func testATabKeepsItsRowsAndTheOneJustOpened() {
        let read = Fixture.row(1), unread = Fixture.row(2, seen: false), needs = Fixture.row(3, needs: true)
        XCTAssertTrue(InboxTab.unread.keeps(unread, opened: nil))
        XCTAssertFalse(InboxTab.unread.keeps(read, opened: nil))
        XCTAssertTrue(InboxTab.unread.keeps(read, opened: MailCopy(read)), "a message you just read stays until you move on")
        XCTAssertTrue(InboxTab.needs.keeps(needs, opened: nil))
        XCTAssertFalse(InboxTab.needs.keeps(read, opened: nil))
        XCTAssertTrue(InboxTab.all.keeps(read, opened: nil))
    }
}

final class MailboxRowsTests: XCTestCase {
    func testEveryListReadsAsTheWindowsRows() {
        let hit = API.SearchHit(account: Fixture.ada, action: "  Pay it ", commitmentWorthy: false, date: "2026-11-02",
                                flagged: true, folder: "INBOX", fromAddr: "shop@example.test", fromName: "Shop", hasAttach: true,
                                isJunk: false, key: 7, needsYou: true, replyWorthy: false, safeToFile: false, seen: false,
                                snippet: "Thanks.", subject: "Your receipt", threadId: "t7", uid: 70, when: "Nov 2")
        let row = API.FolderRow(hit)
        XCTAssertEqual(MailCopy(row), MailCopy(key: 7, folder: "INBOX", uid: 70, account: Fixture.ada))
        XCTAssertEqual(row.action, "Pay it")
        XCTAssertTrue(row.flagged && row.hasAttach && row.needsYou && !row.seen)
        XCTAssertEqual(row.gist, "Thanks.", "with no summary the snippet is the gist")
    }

    func testAConversationFilesWholeButADraftNever() {
        XCTAssertTrue(Fixture.row(1, count: 3).isConversation(grouped: true))
        XCTAssertFalse(Fixture.row(1, count: 3).isConversation(grouped: false))
        XCTAssertFalse(Fixture.row(1, count: 1).isConversation(grouped: true))
        XCTAssertFalse(Fixture.row(1, folder: "Drafts", count: 3).isConversation(grouped: true))
        XCTAssertEqual(Fixture.row(1, count: 3).conversationCount, 3)
        XCTAssertNil(Fixture.row(1).conversationCount)
    }

    func testJunkIsTaggedWithItsBucket() {
        XCTAssertEqual(Fixture.row(1, junk: true).junkTag, "promo")
        XCTAssertNil(Fixture.row(1).junkTag)
    }
}

/// The palette's match and the Paper Trail's money read as the page's do (lib/fuzzy.test.ts, discovery/model.test.ts).
final class MailboxDiscoveryTests: XCTestCase {
    func testTheLettersMatchInOrderPreferringARunAndAWordsStart() throws {
        XCTAssertNotNil(Fuzzy.score("arc", "Archive"))
        XCTAssertNil(Fuzzy.score("cra", "Archive"))
        XCTAssertEqual(Fuzzy.score("", "Archive"), 0)
        XCTAssertGreaterThan(try XCTUnwrap(Fuzzy.score("ad", "Ada / Inbox")), try XCTUnwrap(Fuzzy.score("ad", "Bread")))
        XCTAssertGreaterThan(try XCTUnwrap(Fuzzy.score("inb", "Ada / Inbox")), try XCTUnwrap(Fuzzy.score("inb", "Ada in my bag")))
        XCTAssertGreaterThan(try XCTUnwrap(Fuzzy.score("star", "Star")), try XCTUnwrap(Fuzzy.score("star", "Star this one")))
    }

    func testRankingKeepsTheOrderWithNoQueryAndDropsWhatDoesntMatch() {
        let entries = ["Mark junk", "Archive", "Undo last action"]
        XCTAssertEqual(Fuzzy.rank("", entries, label: { $0 }), entries)
        XCTAssertEqual(Fuzzy.rank("a", entries, label: { $0 }).first, "Archive")
        XCTAssertEqual(Fuzzy.rank("arch", entries, label: { $0 }), ["Archive"])
    }

    func testMoneyReadsEitherWayOfWritingIt() {
        let cases: [(String, String, Double)] = [
            ("$1,234.50", "$", 1234.5), ("€1.234,50", "€", 1234.5), ("£12,50", "£", 12.5), ("1,234 USD", "$", 1234),
            ("99.00 EGP", "E£", 99), ("45 chf", "CHF", 45),
        ]
        for (text, currency, value) in cases {
            let read = Ledger.parse(text)
            XCTAssertEqual(read.currency, currency, text)
            XCTAssertEqual(read.value, value, text)
        }
        XCTAssertEqual(Ledger.amount(subject: "Paid $12.00", snippet: "Tax $2.00, total $14.00"), Ledger.Amount(currency: "$", value: 14))
        XCTAssertNil(Ledger.amount(subject: "Your order 1042", snippet: "shipped today"))
        XCTAssertTrue(Ledger.isRefund("Your refund is on its way"))
        XCTAssertTrue(Ledger.isRefund("Credit note 22"))
        XCTAssertFalse(Ledger.isRefund("Refundable deposit"))
        XCTAssertEqual(Ledger.money("$", -3.5, locale: Locale(identifier: "en_US")), "−$3.50")
        XCTAssertEqual(Ledger.monthName("2026-03"), "March 2026")
        XCTAssertEqual(Ledger.monthName(""), "Undated")
    }

    func testTheLedgerGroupsByMonthWithASubtotalForEachCurrency() {
        let hits = [Fixture.hit(1, subject: "Paid $20.00"), Fixture.hit(2, subject: "Refund of $5.00"),
                    Fixture.hit(3, subject: "Paid €10.00"), Fixture.hit(4, subject: "No amount"),
                    Fixture.hit(5, subject: "Paid $1.50", date: "2026-10-30")]
        let months = Ledger.months(hits)
        XCTAssertEqual(months.map(\.yearMonth), ["2026-11", "2026-10"])
        XCTAssertEqual(months[0].lines.map(\.shown), [20, -5, 10, nil])
        XCTAssertEqual(months[0].totals.map(\.currency), ["$", "€"])
        XCTAssertEqual(months[0].totals.map(\.value), [15, 10])
        XCTAssertEqual(months[1].totals.map(\.value), [1.5])
        XCTAssertTrue(Ledger.months([]).isEmpty)
    }
}

/// The lists read 200 rows at a time as the page reads them, and an Inbox's tab or a search keeps what it has read.
final class MailboxListsTests: XCTestCase {
    private func sheets(_ rows: [API.FolderRow], counted: Bool = true) -> Gathered.Read {
        { offset, limit in
            let page = Array(rows.dropFirst(offset).prefix(limit))
            return Sheet(rows: page, hasMore: offset + page.count < rows.count,
                         facts: ListFacts(total: counted ? rows.count : nil))
        }
    }

    func testAPagedListCountsItsTabsOverItsFirstPage() async throws {
        let rows = (1...300).map { Fixture.row($0, seen: $0 > 10, needs: $0 <= 3) }
        let said = Said()
        let load = Lists.paged(sheets(rows)) { await said.keep($0) }
        let first = try await load(0, 200)
        XCTAssertEqual(first.total, 300)
        XCTAssertEqual(first.rows.count, 200)
        let facts = await said.last
        XCTAssertEqual(facts?.needs, 3)
        XCTAssertEqual(facts?.unread, 10)
        XCTAssertEqual(facts?.total, 300)
        _ = try await load(200, 200)
        let times = await said.count
        XCTAssertEqual(times, 1, "only the first page reports")
    }

    func testATabKeepsOnlyItsRowsUpToHowFarItWasAskedToRead() async throws {
        let rows = (1...500).map { Fixture.row($0, seen: $0 % 2 == 0) }
        let gathered = Gathered(read: sheets(rows), keep: { InboxTab.unread.keeps($0, opened: $1) })
        let first = try await gathered.page(0, 200)
        XCTAssertEqual(first.total, 100, "of the first 200 read, half are unread")
        XCTAssertTrue(first.rows.allSatisfy { !$0.seen })
        await gathered.readMore()
        let more = try await gathered.page(0, 200)
        XCTAssertEqual(more.total, 200)
    }

    func testARowReadTwiceAsTheListMovedIsHeldOnce() async throws {
        let row = Fixture.row(1)
        let gathered = Gathered(read: { offset, _ in
            Sheet(rows: offset == 0 ? [row, row] : [row], hasMore: offset == 0, facts: ListFacts())
        })
        await gathered.readMore()
        let page = try await gathered.page(0, 200)
        XCTAssertEqual(page.total, 1)
    }

    func testTheOneJustOpenedStaysInItsTab() async throws {
        let rows = [Fixture.row(1), Fixture.row(2, seen: false)]
        let opened = MailCopy(rows[0])
        let gathered = Gathered(read: sheets(rows), keep: { InboxTab.unread.keeps($0, opened: $1) }, opened: { opened })
        let page = try await gathered.page(0, 200)
        XCTAssertEqual(page.rows.map(\.key), [1, 2])
    }
}

private actor Said {
    private(set) var last: ListFacts?
    private(set) var count = 0

    func keep(_ facts: ListFacts) {
        last = facts
        count += 1
    }
}

/// The verbs go through the page's routes: one message or conversation by its own, several under one undo group in
/// as many requests as the server's limit asks, each told in the page's words, a refusal with the server's reason.
@MainActor
final class MailboxVerbsTests: XCTestCase {
    private func opened(_ server: FakeServer, place: Place = .folder(account: Fixture.ada, folder: "INBOX")) async -> MailApp {
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.place = place
        await until { app.list?.copy(at: 0) != nil }
        return app
    }

    private func filed(_ status: String? = nil, undoable: Bool = true) -> API.Filed {
        API.Filed(msg: "ok", ok: true, status: status, undoable: undoable)
    }

    func testOneMessageFilesByItsOwnRouteAndMovesOnToTheNext() async throws {
        let server = Fixture.server(grouped: false)
        server.on("POST", API.Post.Archive.path, filed())
        let app = await opened(server)
        let mailbox = app.mailbox
        mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
        XCTAssertEqual(app.selection.copy?.key, 1)
        await mailbox.fileNow(.archive)
        let body = try JSONDecoder().decode(API.MoveBody.self, from: try XCTUnwrap(server.asked(API.Post.Archive.path).first).body)
        XCTAssertEqual(body, API.MoveBody(folder: "INBOX", key: 1, uid: 1))
        XCTAssertEqual(mailbox.toast?.text, "Archived")
        XCTAssertEqual(mailbox.toast?.undo, "Archived", "the server said it can be undone")
        XCTAssertEqual(app.selection.copy?.key, 2, "the reader moves on to the next row")
    }

    func testAConversationFilesWhole() async throws {
        let server = Fixture.server()
        server.on("POST", API.Post.ThreadTrash.path, API.ManyFiled(count: 3, failed: 0, filed: [], msg: "", ok: true, queued: 0,
                                                                   undoable: true))
        let app = await opened(server)
        let conversation = try XCTUnwrap(app.list?.copy(at: 1))
        XCTAssertTrue(conversation.isConversation(grouped: true))
        await app.mailbox.fileNow(.trash, rows: [conversation])
        XCTAssertEqual(server.asked(API.Post.ThreadTrash.path).count, 1)
        XCTAssertTrue(server.asked(API.Post.Trash.path).isEmpty)
        XCTAssertEqual(app.mailbox.toast?.text, "Trashed 3 messages")
    }

    func testManyRowsFileInChunksUnderOneUndo() async throws {
        let rows = (1...1200).map { Fixture.row($0) }
        let server = Fixture.server(rows: rows, grouped: false)
        server.on("POST", API.Post.BulkFile.path) { asked in
            let body = try! JSONDecoder().decode(API.BulkFileBody.self, from: asked.body)
            return FakeServer.ok(API.RowsFiled(count: body.rows?.count ?? 0, failed: 0, filed: [], msg: "", ok: true, partial: false,
                                               queued: 0, rowsFailed: body.rows?.first?.key == 501 ? 2 : 0, undoable: true))
        }
        let app = await opened(server)
        await app.mailbox.fileNow(.archive, rows: rows)
        let bodies = try server.asked(API.Post.BulkFile.path).map { try JSONDecoder().decode(API.BulkFileBody.self, from: $0.body) }
        XCTAssertEqual(bodies.map { $0.rows?.count ?? 0 }, [API.Limits.bulkRows, API.Limits.bulkRows, 1200 - 2 * API.Limits.bulkRows],
                       "no request holds more rows than the server takes")
        XCTAssertEqual(Set(bodies.map(\.undoGroup)).count, 1, "one Undo reverses all of it")
        XCTAssertTrue(bodies.allSatisfy { $0.verb == .archive })
        XCTAssertEqual(app.mailbox.toast?.text, "Archived 1198, 2 failed")
    }

    func testARefusalSaysTheServersReason() async throws {
        let server = Fixture.server(grouped: false)
        server.on("POST", API.Post.Junk.path) { _ in FakeServer.refused(409, "That message moved on the server.") }
        let app = await opened(server)
        await app.mailbox.fileNow(.junk, rows: [try XCTUnwrap(app.list?.copy(at: 0))])
        XCTAssertEqual(app.mailbox.toast?.text, "Couldn’t mark junk")
        XCTAssertEqual(app.mailbox.toast?.detail, "That message moved on the server.")
        XCTAssertNil(app.mailbox.toast?.undo)
    }

    func testAMoveOffersOnlyTheAccountsOwnFoldersThatTakeFiling() async throws {
        let server = Fixture.server()
        let app = await opened(server)
        let names = app.mailbox.destinations(MoveAsk(account: Fixture.ada, from: "INBOX")).map(\.name)
        XCTAssertEqual(names, ["Sent", "Archive", "Junk", "Trash", "Receipts"], "not Drafts, which takes no filing, nor the Inbox")
        app.mailbox.chose([Fixture.row(1), Fixture.row(2, account: Fixture.work)])
        app.mailbox.askMove()
        await until { app.mailbox.toast != nil }
        XCTAssertEqual(app.mailbox.toast?.text, "Those are in different accounts — mark one account’s mail to move it")
        XCTAssertNil(app.mailbox.moving)
    }

    func testUndoSaysWhatWasUndoneOrThatThereWasNothing() async throws {
        let server = Fixture.server()
        var undone = API.Undone(label: "Archive", ok: true, remaining: 0)
        server.on("POST", API.Post.Undo.path, undone)
        let app = await opened(server)
        await app.mailbox.undoNow()
        XCTAssertEqual(app.mailbox.toast?.text, "Archive undone")
        undone.failed = 2
        server.on("POST", API.Post.Undo.path, undone)
        await app.mailbox.undoNow()
        XCTAssertEqual(app.mailbox.toast?.text, "2 couldn’t be restored — press ⌘Z to try again")
        server.on("POST", API.Post.Undo.path) { _ in FakeServer.refused(409, "Nothing to undo.", more: ["empty": true]) }
        await app.mailbox.undoNow()
        XCTAssertEqual(app.mailbox.toast?.text, "Nothing to undo")
    }

    func testEditUndoUndoesWhatTheMailboxDid() async throws {
        let server = Fixture.server(grouped: false)
        server.on("POST", API.Post.Archive.path, filed())
        server.on("POST", API.Post.Undo.path, API.Undone(label: "Archive", ok: true, remaining: 0))
        let app = await opened(server)
        let undo = UndoManager()
        undo.groupsByEvent = false
        app.mailbox.undoManager = undo
        undo.beginUndoGrouping()
        await app.mailbox.fileNow(.archive, rows: [try XCTUnwrap(app.list?.copy(at: 0))])
        undo.endUndoGrouping()
        XCTAssertEqual(undo.undoActionName, "Archive")
        app.mailbox.undo()
        await until { !server.asked(API.Post.Undo.path).isEmpty }
        XCTAssertFalse(undo.canUndo, "the undo went through Edit ▸ Undo")
    }

    func testReadAndStarGoByTheRowAndTheConversation() async throws {
        let server = Fixture.server()
        server.on("POST", API.Post.Seen.path, filed())
        server.on("POST", API.Post.Flag.path, filed())
        server.on("GET", API.Get.Thread.path, API.Thread(account: Fixture.work, count: 3, items: [
            threadMessage(21, seen: true), threadMessage(22, seen: false), threadMessage(23, seen: false),
        ], ok: true, threadId: "t2"))
        let app = await opened(server)
        let conversation = try XCTUnwrap(app.list?.copy(at: 1))
        await app.mailbox.setSeenNow(true, rows: [conversation])
        let seen = try server.asked(API.Post.Seen.path).map { try JSONDecoder().decode(API.SeenBody.self, from: $0.body) }
        XCTAssertEqual(seen.map(\.key), [22, 23], "marking a conversation read takes each member that isn't")
        XCTAssertEqual(app.mailbox.toast?.text, "Marked read")
    }

    func testTheReaderMovesOnPastTheRowsThatLeave() async throws {
        let server = Fixture.server(rows: (1...4).map { Fixture.row($0) }, grouped: false)
        let app = await opened(server)
        let list = try XCTUnwrap(app.list)
        let rows = (0..<4).compactMap { list.copy(at: $0) }
        app.mailbox.chose([rows[1]])
        XCTAssertEqual(app.mailbox.nextOpen(leaving: [rows[1], rows[2]]), .some(.some(MailCopy(rows[3]))),
                       "the next row is leaving too, so the one after it")
        XCTAssertEqual(app.mailbox.nextOpen(leaving: [rows[1], rows[2], rows[3]]), .some(.some(MailCopy(rows[0]))),
                       "nothing after stays, so the one before")
        XCTAssertEqual(app.mailbox.nextOpen(leaving: rows), .some(nil), "nothing is left to open")
        XCTAssertTrue(app.mailbox.nextOpen(leaving: [rows[0]]) == nil, "what is open stays")
    }

    private func threadMessage(_ key: Int, seen: Bool) -> API.ThreadMessage {
        API.ThreadMessage(answered: false, flagged: false, folder: "INBOX", hasAttach: false, isJunk: false, key: key, seen: seen,
                          snippet: "", subject: "Re: The engine's notes", uid: key, when: "")
    }
}

/// Ways about: a chord goes to a folder by its role, the search field reads what is typed as the page reads it.
@MainActor
final class MailboxGoingTests: XCTestCase {
    private func mailbox() async -> (MailApp, Mailbox) {
        let app = MailApp()
        app.mailbox.wire = Fixture.server().wire
        await app.mailbox.refresh()
        return (app, app.mailbox)
    }

    func testAChordGoesToTheFolderPlayingThatRole() async {
        let (app, mailbox) = await mailbox()
        app.place = .folder(account: Fixture.work, folder: "INBOX")
        mailbox.go(chord: "a")
        XCTAssertEqual(app.place, .folder(account: Fixture.work, folder: "[Gmail]/All Mail"), "Gmail archives into All Mail")
        mailbox.go(chord: "d")
        XCTAssertEqual(mailbox.toast?.text, "No drafts folder on this account")
        mailbox.go(chord: "z")
        XCTAssertEqual(app.place, .virtual(.snoozed, account: Fixture.work))
        app.place = .allInboxes
        mailbox.go(chord: "w")
        XCTAssertEqual(app.place, .virtual(.waiting, account: nil), "from All Inboxes, over every account")
    }

    func testTheSearchFieldReadsWhatIsTypedAsThePageReadsIt() async {
        let (app, mailbox) = await mailbox()
        app.place = .folder(account: Fixture.ada, folder: "INBOX")
        XCTAssertEqual(mailbox.submitted("  from:grace "), .search(Search(q: "from:grace", account: Fixture.ada)))
        XCTAssertEqual(mailbox.submitted("?what did Grace say"), .question("what did Grace say"))
        XCTAssertEqual(mailbox.submitted("!archive all receipts"), .command("archive all receipts"))
        XCTAssertEqual(mailbox.submitted("?"), .leave)
        XCTAssertEqual(mailbox.submitted(""), .leave)
        app.place = .search(Search(q: "budget", account: Fixture.work, thread: "t2"))
        XCTAssertEqual(mailbox.submitted("figures"), .search(Search(q: "figures", account: Fixture.work, thread: "t2")),
                       "a search held to a conversation stays held")
        app.place = .allInboxes
        XCTAssertEqual(mailbox.submitted("x"), .search(Search(q: "x")))
    }

    func testLeavingASearchGoesBackToItsInbox() async {
        let (app, mailbox) = await mailbox()
        app.place = .search(Search(q: "x", account: Fixture.ada))
        mailbox.leaveSearch()
        XCTAssertEqual(app.place, .folder(account: Fixture.ada, folder: "INBOX"))
        app.place = .search(Search(q: "x"))
        mailbox.leaveSearch()
        XCTAssertEqual(app.place, .allInboxes)
    }

    func testThePaletteListsEveryPlaceAndTheVerbsOnlyWithSomethingChosen() async {
        let (app, mailbox) = await mailbox()
        app.place = .allInboxes
        let ids = Set(mailbox.commands().map(\.id))
        XCTAssertTrue(ids.isSuperset(of: ["all-inboxes", "acct:\(Fixture.ada)", "fold:\(Fixture.work):[Gmail]/All Mail", "docs",
                                          "view:v1", "undo", "grouping"]))
        XCTAssertFalse(ids.contains("archive"))
        mailbox.chose([Fixture.row(1)])
        XCTAssertTrue(Set(mailbox.commands().map(\.id)).contains("archive"))
        mailbox.more.append { _ in [MailCommand(id: "snooze", group: .actions, label: "Snooze…") {}] }
        XCTAssertTrue(mailbox.commands().contains { $0.id == "snooze" }, "another area's command joins the palette")
    }

    func testThePalettesPlacesShowTheKeysThatGoThereFromHere() async {
        let (app, mailbox) = await mailbox()
        func hints() -> [String: String] {
            Dictionary(mailbox.commands().compactMap { command in command.hint.map { (command.id, $0) } },
                       uniquingKeysWith: { first, _ in first })
        }
        app.place = .allInboxes
        var shown = hints()
        XCTAssertEqual(shown["fold:\(Fixture.ada):INBOX"], "g i", "from every inbox, the first account's folders")
        XCTAssertEqual(shown["acct:\(Fixture.ada)"], "g i")
        XCTAssertEqual(shown["fold:\(Fixture.ada):Trash"], "g t")
        XCTAssertEqual(shown["across:\(VirtualFolder.waiting.rawValue)"], "g w", "and the stacks over every account")
        XCTAssertNil(shown["fold:\(Fixture.ada):\(VirtualFolder.waiting.rawValue)"])
        XCTAssertNil(shown["fold:\(Fixture.work):INBOX"], "another account's folder is no chord away")
        XCTAssertNil(shown["all-inboxes"])
        XCTAssertNil(shown["fold:\(Fixture.ada):Receipts"], "a folder of a person's own has no key")

        let work = Place.folder(account: Fixture.work, folder: "INBOX")
        app.place = work
        shown = hints()
        XCTAssertEqual(shown["fold:\(Fixture.work):[Gmail]/All Mail"], "g a", "Gmail archives into All Mail")
        XCTAssertEqual(shown["fold:\(Fixture.work):\(VirtualFolder.waiting.rawValue)"], "g w")
        XCTAssertNil(shown["across:\(VirtualFolder.waiting.rawValue)"])
        XCTAssertNil(shown["fold:\(Fixture.ada):INBOX"])
        // Each key shown goes where its command goes.
        for command in mailbox.commands() {
            guard let hint = command.hint, hint.hasPrefix("g "), let key = hint.last else { continue }
            command.run()
            let there = app.place
            app.place = work
            mailbox.go(chord: key)
            XCTAssertEqual(app.place, there, command.id)
            app.place = work
        }
    }

    func testThePaletteShowsNoChordWhileSingleKeysAreOff() async {
        let app = MailApp()
        app.mailbox.wire = Fixture.server(singleKeys: false).wire
        await app.mailbox.refresh()
        app.place = .allInboxes
        XCTAssertFalse(app.mailbox.commands().contains { $0.hint?.hasPrefix("g ") == true })
    }

    func testAnAreaHangsItsHooksOnce() async {
        let (_, mailbox) = await mailbox()
        var joins = 0
        for _ in 0..<3 {
            mailbox.join("desk") { mailbox in
                joins += 1
                mailbox.more.append { _ in [MailCommand(id: "brief", group: .commands, label: "Today’s brief") {}] }
            }
        }
        mailbox.join("compose") { _ in joins += 1 }
        XCTAssertEqual(joins, 2, "each area once, however often its view appears")
        XCTAssertEqual(mailbox.commands().filter { $0.id == "brief" }.count, 1)
    }

    func testASentenceSearchedForGivesWayToTheSearchItMeans() async {
        let (app, mailbox) = await mailbox()
        let sentence = "mail from grace about the figures"
        var asked: [String] = []
        mailbox.translates = { typed in
            asked.append(typed)
            return typed == sentence ? "from:grace figures" : nil
        }
        app.place = .folder(account: Fixture.ada, folder: "INBOX")
        mailbox.submit(sentence)
        XCTAssertEqual(app.place, .search(Search(q: sentence, account: Fixture.ada)), "as typed, until the meaning comes")
        await until { app.place?.search?.asked != nil }
        XCTAssertEqual(app.place, .search(Search(q: "from:grace figures", account: Fixture.ada, asked: sentence)))

        // A search held to one conversation stays as typed.
        app.place = .search(Search(q: "x", account: Fixture.work, thread: "t2"))
        mailbox.submit(sentence)
        XCTAssertEqual(app.place, .search(Search(q: sentence, account: Fixture.work, thread: "t2")))
        XCTAssertEqual(asked, [sentence])
    }

    func testLeavingASearchDropsATranslationStillOnItsWay() async {
        let (app, mailbox) = await mailbox()
        var stopped = 0
        mailbox.stopsTranslating = { stopped += 1 }
        app.place = .search(Search(q: "mail from grace about the figures", account: Fixture.ada))
        mailbox.leaveSearch()
        XCTAssertEqual(stopped, 1)
        XCTAssertEqual(app.place, .folder(account: Fixture.ada, folder: "INBOX"))
    }

    func testTheDesksScreensStandInTheListsPlaceUnderTheirOwnTitles() async {
        let (app, mailbox) = await mailbox()
        app.place = .desk(.brief)
        XCTAssertNil(app.list, "the desk's screens read what they show themselves")
        XCTAssertEqual(mailbox.title, "Today’s Brief")
        app.place = .desk(.screener(account: Fixture.ada))
        XCTAssertEqual(mailbox.title, "Screener")
    }

    func testTheSidebarReadsTheAccountsFoldersLabelsAndViews() async {
        let (_, mailbox) = await mailbox()
        XCTAssertEqual(mailbox.accounts, [Fixture.ada, Fixture.work])
        XCTAssertEqual(mailbox.name(of: Fixture.ada), "Ada")
        XCTAssertEqual(mailbox.name(of: Fixture.work), "work", "no alias: the address's local part")
        XCTAssertEqual(mailbox.folders(of: Fixture.ada).count, Fixture.adaFolders.count)
        XCTAssertEqual(mailbox.labels[Fixture.work]?.map(\.name), ["Projects", "Travel \"2026\""])
        XCTAssertEqual(mailbox.across?.waiting, 3)
        XCTAssertEqual(mailbox.views.map(\.id), ["__lane_feed__", "__lane_papertrail__", "v1"])
        XCTAssertTrue(mailbox.aiOn)
    }
}
