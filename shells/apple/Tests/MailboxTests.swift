import Foundation
import SwiftUI
import XCTest

/// The mailbox's words, places and rows say what the page says of them.
final class MailboxWordsTests: XCTestCase {
    func testFilingIsToldInThePagesWords() {
        XCTAssertEqual(Words.filed("Archived", count: 1, failed: 0, queued: false), "Archived")
        XCTAssertEqual(Words.filed("Archived", count: 3, failed: 0, queued: false), "Archived 3 messages")
        XCTAssertEqual(Words.filed("Moved", count: 2, failed: 1, queued: false, to: "Archive"),
                       "Moved 2 of 3 to Archive — the rest stayed")
        XCTAssertEqual(Words.filed("Trashed", count: 1, failed: 0, queued: true), "Trashed · once the server is back")
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

    func testAPersonIsCalledByTheirFirstNameAndAnOrganisationWhole() {
        let people = [("Lin Mori", "lin@example.test"), ("Renée Lefèvre", "renee@atelier.example.test"),
                      ("Ivo Brandt", "ivo@harbor.example.test"), ("Grace Hopper", "grace.hopper@example.test"),
                      ("Ren Takeda", "rtakeda@example.test"), ("Ren Takeda", "takeda.r@example.test")]
        for (name, address) in people {
            XCTAssertEqual(Words.calling(name, address: address), String(name.split(separator: " ")[0]), name)
        }
        let organisations = [("Parcel Service", "tracking@parcels.example.test"),
                             ("Prize Desk", "winner@prizes.example.test"), ("Café Lumière", "hello@cafe.example.test"),
                             ("Old Mailer", "legacy@mailer.example.test"), ("Spindle Deals", "deals@spindle.example.test"),
                             ("The Weekly Loom", "news@loom.example.test"), ("Thimble & Co", "orders@thimble.example.test"),
                             ("Build Bot", "noreply@ci.example.test"), ("Example Bank", "alerts@bank.example.test"),
                             ("Riverside School", "school@example.test"), ("Northwind Traders", "sales@example.test"),
                             ("Takeda, Ren", "ren@example.test"), ("Guild Gazette", "gazette@example.test"),
                             ("Guild Gazette", "gazette@guild.example.test")]
        for (name, address) in organisations {
            XCTAssertEqual(Words.calling(name, address: address), name, "\(name) is said whole")
        }
        // The last name alone in the address is no sign of a person, as gazette@ for "Guild Gazette" isn't: said whole.
        XCTAssertEqual(Words.calling("Ren Takeda", address: "takeda@example.test"), "Ren Takeda")
        XCTAssertEqual(Words.calling("Ren Takeda"), "Ren", "with no address, a person's name by its shape")
        XCTAssertEqual(Words.calling("Parcel Service"), "Parcel Service")
        XCTAssertEqual(Words.calling("Lin"), "Lin")
        XCTAssertNil(Words.calling("ren@example.test", address: "ren@example.test"), "an address is no name")
        XCTAssertNil(Words.calling("  ", address: nil))

        // Where the window says it short.
        let parcel = ("Parcel Service", "tracking@parcels.example.test")
        XCTAssertEqual(SummaryCard.foot(parcel.0, address: parcel.1),
                       "Not Parcel Service’s words — the message is the source.")
        XCTAssertEqual(ReplyBoxWords(name: parcel.0, address: parcel.1, following: false).placeholder,
                       "Reply to Parcel Service…")
        XCTAssertEqual(ComposerForm.heading(.reply, to: [Recipient("Parcel Service <tracking@parcels.example.test>")]),
                       "Reply to Parcel Service")
        XCTAssertEqual(ContactCard.heading(API.ContactCard(accounts: [], addr: parcel.1, count: 1, known: false,
                                                           name: parcel.0, ok: true, recent: [], replied: 0)),
                       "About Parcel Service")
        XCTAssertEqual(SummaryCard.foot("Lin Mori", address: "lin@example.test"),
                       "Not Lin’s words — the message is the source.")
        let gazette = ("Guild Gazette", "gazette@example.test")
        XCTAssertEqual(ReplyBoxWords(name: gazette.0, address: gazette.1, following: false).placeholder,
                       "Reply to Guild Gazette…")
        XCTAssertEqual(ComposerForm.heading(.reply, to: [Recipient("Guild Gazette <gazette@example.test>")]),
                       "Reply to Guild Gazette")
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
        // As the rail names it (the servers' view, "Paper trail"), not a second way.
        XCTAssertEqual(Place.search(Search(q: "x", lane: .papertrail)).title(folders: []), "Paper trail")
        XCTAssertEqual(Place.documents.title(folders: []), "Documents")
    }

    /// The paper trail with no receipts says none yet; once its last receipt is filed or deleted, it had some, so it
    /// says what the page's emptied places say. Feed says it is clear either way.
    @MainActor
    func testThePaperTrailEmptiedSaysNothingIsLeftNotNoneYet() async {
        XCTAssertEqual(Words.emptyLane(.papertrail, emptied: false), "No receipts or invoices yet.")
        XCTAssertEqual(Words.emptyLane(.papertrail, emptied: true), "Nothing left here.")
        XCTAssertEqual(Words.emptyLane(.feed, emptied: true), "Your Feed is clear — no newsletters waiting.")

        func answer(_ server: FakeServer, _ hits: [API.SearchHit]) {
            server.on("GET", API.Get.Search.path, API.SearchAnswer.searchPage(API.SearchPage(
                generated: "", hasMore: false, items: hits, offset: 0, ok: true, query: "", scope: "all")))
        }
        let server = Fixture.server()
        answer(server, [])
        let none = LaneModel(search: Search(q: "category:receipt", lane: .papertrail), scope: .all, wire: server.wire)
        none.refresh()
        await until { none.loaded }
        XCTAssertFalse(none.emptied, "never had any: none yet")

        answer(server, [Fixture.hit(41, subject: "Your receipt")])
        let lane = LaneModel(search: Search(q: "category:receipt", lane: .papertrail), scope: .all, wire: server.wire)
        lane.refresh()
        await until { !lane.hits.isEmpty }
        XCTAssertFalse(lane.emptied)
        answer(server, [])
        lane.refresh()
        await until { lane.hits.isEmpty }
        XCTAssertTrue(lane.emptied, "its last receipt went")
        answer(server, [Fixture.hit(42, subject: "Another receipt")])
        lane.refresh()
        await until { !lane.hits.isEmpty }
        XCTAssertFalse(lane.emptied)
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
                                sentTo: [], sentToMore: 0,
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

    func testACopyIsInTrashByItsFoldersRoleOnItsOwnAccount() async {
        let app = await opened(Fixture.server())
        let mailbox = app.mailbox
        XCTAssertTrue(mailbox.inTrash(MailCopy(key: 1, folder: "Trash", uid: 1, account: Fixture.ada)))
        XCTAssertTrue(mailbox.inTrash(MailCopy(key: 1, folder: "[Gmail]/Trash", uid: 1, account: Fixture.work)))
        XCTAssertFalse(mailbox.inTrash(MailCopy(key: 1, folder: "INBOX", uid: 1, account: Fixture.ada)))
        XCTAssertFalse(mailbox.inTrash(MailCopy(key: 1, folder: "Trash", uid: 1, account: Fixture.work)),
                       "a name is only Trash where its account says so")
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

    /// The gate and the paper trail are pages across the window only while no message is open: arriving at one closes
    /// the message left open in the place before, which would otherwise keep it in the list's room beside that message.
    /// A place with a list of its own beside the pane, a lane among them, leaves the message open as before.
    func testAPageAcrossTheWindowClosesTheMessageLeftOpenWhereAListKeepsIt() async throws {
        let app = await opened(Fixture.server(grouped: false))
        let mailbox = app.mailbox
        let inbox = Place.folder(account: Fixture.ada, folder: "INBOX")
        func openTheFirst() async throws {
            app.place = inbox
            await until { app.list?.copy(at: 0) != nil }
            mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
            XCTAssertNotNil(app.selection.copy)
        }
        for page in [Place.desk(.screener(account: Fixture.ada)),
                     .search(Search(q: "category:receipt", lane: .papertrail))] {
            try await openTheFirst()
            app.place = page
            XCTAssertNil(app.selection.copy, "\(page) spans the window")
        }
        try await openTheFirst()
        let open = app.selection.copy
        app.place = .search(Search(q: "category:newsletter", lane: .feed))
        XCTAssertEqual(app.selection.copy, open, "a lane's list stands beside the pane: the message stays")
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

    /// A link in the run of a sentence (the empty Inbox tab's "in Everything.") takes no more room than its words, so
    /// no space stands before the full stop after it; one on a line of its own keeps room at its sides.
    func testALinkInASentenceKeepsNoRoomAtItsSides() {
        func width(_ view: some View) -> CGFloat { NSHostingView(rootView: view.meadowLight(.day)).fittingSize.width }
        let words = width(Text("Everything").font(.meadow(.ui, 12.5, .bold)).underline())
        let inline = width(Button("Everything") {}.buttonStyle(MeadowLinkStyle(inline: true)))
        let alone = width(Button("Everything") {}.buttonStyle(MeadowLinkStyle()))
        XCTAssertEqual(inline, words, accuracy: 0.5)
        XCTAssertEqual(alone, words + 8, accuracy: 0.5)
    }

    func testTheBulkBarsRestoreAndSnoozeTakeEveryRowChosenHeldOrNot() async throws {
        let rows = (1...450).map { Fixture.row($0, folder: "Trash") }
        let server = Fixture.server(rows: rows, grouped: false)
        server.on("POST", API.Post.BulkMove.path) { asked in
            let body = try! JSONDecoder().decode(API.BulkMoveBody.self, from: asked.body)
            return FakeServer.ok(API.RowsFiled(count: body.rows?.count ?? 0, failed: 0, filed: [], msg: "", ok: true,
                                               partial: false, queued: 0, rowsFailed: 0, undoable: true))
        }
        let app = await opened(server, place: .folder(account: Fixture.ada, folder: "Trash"))
        let mailbox = app.mailbox
        // Select All in a long list: the rows held, and the positions whose pages the list has let go.
        mailbox.chose((0..<MailListModel.pageSize).compactMap { app.list?.copy(at: $0) },
                      unheld: IndexSet(MailListModel.pageSize..<450))
        XCTAssertEqual(mailbox.chosenRows.count + mailbox.unheld.count, 450, "the bulk bar says 450 selected")
        var snoozed: [API.FolderRow] = []
        mailbox.snoozes = { snoozed = $0 }
        mailbox.snoozeChosen()
        await until { !snoozed.isEmpty }
        XCTAssertEqual(Set(snoozed.map(\.key)), Set(1...450))
        mailbox.restore()
        func moved() throws -> [API.BulkRow] {
            try server.asked(API.Post.BulkMove.path)
                .flatMap { try JSONDecoder().decode(API.BulkMoveBody.self, from: $0.body).rows ?? [] }
        }
        await until { (try? moved().count) == 450 }
        XCTAssertEqual(Set(try moved().map(\.key)), Set(1...450))
        XCTAssertEqual(Set(try server.asked(API.Post.BulkMove.path)
            .map { try JSONDecoder().decode(API.BulkMoveBody.self, from: $0.body).to }), ["INBOX"])
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

    /// A cleanup undone from the toast floods the Inbox again: Curate, open, reads its senders again once the Undo
    /// lands, rather than go on saying no sender is flooding you until it is opened again. Closed, it reads nothing:
    /// it reads them as it opens.
    func testCurateOpenReadsItsSendersAgainOnceAnUndoLands() async throws {
        let server = Fixture.server()
        server.on("GET", API.Get.CleanupSenders.path,
                  API.NoisySenders(generated: "2026-11-15T09:00:00Z", ok: true, senders: []))
        server.on("POST", API.Post.Undo.path, API.Undone(label: "Clean Up", ok: true, remaining: 0, reversed: 31))
        let app = await opened(server)
        let desk = Desk()
        desk.use(app, routes: server.wire)
        desk.sheet = .curate
        await desk.curate.load()
        XCTAssertEqual(desk.curate.flood?.senders.count, 0, "no sender is flooding you, the cleanup done")

        server.on("GET", API.Get.CleanupSenders.path, DeskFixture.flood)
        await app.mailbox.undoNow()
        await until { desk.curate.flood?.senders.count == DeskFixture.flood.senders.count }

        desk.sheet = nil
        let reads = server.asked(API.Get.CleanupSenders.path).count
        await app.mailbox.undoNow()
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(server.asked(API.Get.CleanupSenders.path).count, reads, "Curate closed reads nothing")
        withExtendedLifetime(desk) {}
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
                          sentTo: [], sentToMore: 0,
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

    func testAWindowThatClosesLetsGoOfItsHooksAndTheNextJoinsAfresh() async {
        let (_, mailbox) = await mailbox()
        var heard: [String] = []
        func hang(_ window: String) {
            for area in ["desk", "compose", "status", "meadow"] {
                mailbox.join(area) { mailbox in
                    mailbox.keys[Character(String(area.first!))] = { heard.append(window) }
                    mailbox.more.append { _ in [MailCommand(id: area, group: .commands, label: area) {}] }
                    mailbox.rowMenu.append { _ in [] }
                    mailbox.asks = { _ in heard.append(window) }
                    mailbox.plans = { _ in }
                    mailbox.composeTo = { _ in }
                    mailbox.snoozes = { _ in }
                    mailbox.opensBrief = { _ in }
                    mailbox.translates = { _ in nil }
                    mailbox.keepsLiteral = { _ in }
                    mailbox.translating = { nil }
                    mailbox.stopsTranslating = {}
                }
            }
        }
        hang("first")
        let round = mailbox.joinRound
        mailbox.leave()
        XCTAssertEqual(mailbox.joinRound, round + 1, "a window still open hears it, and joins again")
        XCTAssertTrue(mailbox.keys.isEmpty && mailbox.more.isEmpty && mailbox.rowMenu.isEmpty)
        XCTAssertTrue(mailbox.asks == nil && mailbox.plans == nil && mailbox.composeTo == nil && mailbox.snoozes == nil)
        XCTAssertTrue(mailbox.opensBrief == nil && mailbox.translates == nil && mailbox.keepsLiteral == nil)
        XCTAssertTrue(mailbox.translating == nil && mailbox.stopsTranslating == nil)
        XCTAssertFalse(mailbox.commands().contains { $0.id == "desk" }, "the closed window's commands are gone")

        // The window opened again from the Dock: its areas join, each once, and theirs are the hooks.
        hang("second")
        hang("second")
        mailbox.keys["d"]?()
        mailbox.asks?("when is the dentist")
        XCTAssertEqual(heard, ["second", "second"])
        XCTAssertEqual(mailbox.commands().filter { $0.id == "desk" }.count, 1)
        XCTAssertEqual(mailbox.rowMenu.count, 4)
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
        XCTAssertEqual(mailbox.title, "At the gate")
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

/// An account added or removed reads the window again at once: the rail shows it, and its Inbox says it is being read
/// for the first time, without waiting for the end of its first sync.
@MainActor
final class MailboxAccountsTests: XCTestCase {
    /// The server over `accounts`, each Inbox empty.
    private func server(accounts: [String], syncing: Bool = false) -> FakeServer {
        let server = Fixture.server(rows: [])
        changeTo(server, accounts: accounts, syncing: syncing)
        return server
    }

    /// The server now names `accounts`, and every Inbox says whether one is on its first read.
    private func changeTo(_ server: FakeServer, accounts: [String], syncing: Bool = false) {
        server.on("GET", API.Get.Digest.path, Fixture.digest(accounts: accounts))
        server.on("GET", API.Get.Inboxes.path, API.InboxesListing(folder: "INBOX", grouped: true, hasMore: false,
                                                                  items: [], offset: 0, ok: true,
                                                                  syncing: syncing ? true : nil, total: 0))
    }

    private func opened(_ server: FakeServer) async -> MailApp {
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        app.openList()
        await until { app.list?.firstRowsAt != nil }
        return app
    }

    /// An account removed leaves every other account's colour as it was, and one added after takes the colour left
    /// free; the places outlive the window, kept in the Mac's preferences.
    func testAnAccountRemovedLeavesTheOthersColoursAsTheyWere() async throws {
        let suite = "AccountPlaces-\(UUID().uuidString)"
        let kept = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { kept.removePersistentDomain(forName: suite) }
        let (ada, bea, cy, dee) = ("ada@example.test", "bea@example.test", "cy@example.test", "dee@example.test")
        func read(_ accounts: [String]) async -> Mailbox {
            let server = Fixture.server()
            server.on("GET", API.Get.Digest.path, Fixture.digest(accounts: accounts))
            let mailbox = Mailbox()
            mailbox.placesKept = kept
            mailbox.wire = server.wire
            await mailbox.readDigest()
            return mailbox
        }
        let three = await read([ada, bea, cy])
        XCTAssertEqual([ada, bea, cy].map(three.colourPlace), [0, 1, 2], "first seen, in the accounts' order")
        let two = await read([ada, cy])
        XCTAssertEqual([ada, cy].map(two.colourPlace), [0, 2], "Cy keeps its colour once Bea is removed")
        let added = await read([ada, cy, dee])
        XCTAssertEqual([ada, cy, dee].map(added.colourPlace), [0, 2, 1], "an account added takes the colour left free")
        let look = RowLook(inks: RowInks.of(.day, light: .day, highContrast: false), measure: RowMeasure(.comfortable),
                           accounts: added.accounts, places: added.places)
        XCTAssertEqual(look.colour(of: cy), look.inks.account(2), "the rows colour it so")
        XCTAssertEqual(AccountPlaces.place(of: cy, among: [ada, cy, dee], in: kept), 2, "so do Settings and the desk")
    }

    /// The core keeps each account's place, so the page and the Mac colour an account the same: the digest's places
    /// stand over any this Mac kept, and are kept here for what reads them before the digest comes.
    func testTheCoresPlacesStandOverTheOnesThisMacKept() async throws {
        let suite = "AccountPlaces-\(UUID().uuidString)"
        let kept = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { kept.removePersistentDomain(forName: suite) }
        let (ada, bea, cy) = ("ada@example.test", "bea@example.test", "cy@example.test")
        kept.set([ada: 0, bea: 1, cy: 2], forKey: AccountPlaces.key)
        let server = Fixture.server()
        server.on("GET", API.Get.Digest.path, Fixture.digest(accounts: [ada, bea, cy], colours: [ada: 4, bea: 0, cy: 2]))
        let mailbox = Mailbox()
        mailbox.placesKept = kept
        mailbox.wire = server.wire
        await mailbox.readDigest()
        XCTAssertEqual([ada, bea, cy].map(mailbox.colourPlace), [4, 0, 2], "the core's, not the order or this Mac's")
        XCTAssertEqual(AccountPlaces.place(of: ada, among: [ada, bea, cy], in: kept), 4, "kept for Settings and the desk")
        XCTAssertEqual(AccountPlaces.placed([ada, bea, cy], kept: [ada: 0, bea: 1, cy: 2], core: [ada: 1]),
                       [ada: 1, bea: 0, cy: 2], "one the core names takes its place; a place kept here it holds goes free")
    }

    /// Every account's Inbox unread, for the brief's line: counted only once each account's Inbox is read, never a
    /// part of it.
    func testTheInboxesUnreadIsCountedOnceEveryAccountsInboxIsRead() async {
        let mailbox = Mailbox()
        mailbox.wire = Fixture.server().wire
        XCTAssertNil(mailbox.inboxesUnread, "no account read yet")
        await mailbox.readDigest()
        XCTAssertNil(mailbox.inboxesUnread, "the accounts, not yet their folders")
        await mailbox.readCounts()
        XCTAssertEqual(mailbox.inboxesUnread, 5, "Ada's 4 and Work's 1; Junk's 2 aren't the Inbox's")

        let server = Fixture.server()
        server.on("GET", API.Get.Folders.path) { asked in
            let account = asked.query["account"] ?? ""
            let folders = account == Fixture.work ? [] : Fixture.adaFolders
            return FakeServer.ok(API.Folders(account: account, folders: folders, muted: 0, ok: true, replylater: 0,
                                             snoozed: 0, waiting: 0))
        }
        let partial = Mailbox()
        partial.wire = server.wire
        await partial.refresh()
        XCTAssertNil(partial.inboxesUnread, "an account with no Inbox listed yet leaves the count unknown")
    }

    func testAnAccountAddedShowsInTheRailAndItsInboxSaysItIsReadForTheFirstTime() async {
        let server = server(accounts: [])
        let app = await opened(server)
        XCTAssertEqual(app.mailbox.accounts, [])
        XCTAssertFalse(app.mailbox.facts.syncing)

        changeTo(server, accounts: [Fixture.ada], syncing: true)
        await app.accountsChanged().value
        XCTAssertEqual(app.mailbox.accounts, [Fixture.ada], "the rail shows it at once")
        XCTAssertEqual(app.mailbox.folders(of: Fixture.ada).count, Fixture.adaFolders.count)
        await until { app.mailbox.facts.syncing }
        XCTAssertTrue(app.mailbox.facts.syncing, "the list says its Inbox is being read for the first time")
        XCTAssertEqual(app.place, .allInboxes)
    }

    func testAnAccountRemovedLeavesTheRailAndAPlaceOfItsGivesWayToEveryInbox() async {
        let server = server(accounts: [Fixture.ada, Fixture.work])
        let app = await opened(server)
        app.place = .folder(account: Fixture.work, folder: "INBOX")
        changeTo(server, accounts: [Fixture.ada])
        await app.accountsChanged().value
        XCTAssertEqual(app.mailbox.accounts, [Fixture.ada])
        XCTAssertEqual(app.place, .allInboxes, "its folder isn't there to show")
    }

    /// The brief names no account, but the list beside it can be the removed account's Inbox: that goes too, rather
    /// than the list reading an account that isn't there again and again.
    func testTheBriefBesideARemovedAccountsInboxGivesWayToEveryInbox() async {
        let server = server(accounts: [Fixture.ada, Fixture.work])
        let app = await opened(server)
        app.place = .folder(account: Fixture.work, folder: "INBOX")
        await until { app.list?.firstRowsAt != nil }
        app.place = .desk(.brief)
        XCTAssertEqual(app.listed, .folder(account: Fixture.work, folder: "INBOX"), "the brief keeps its Inbox listed")
        changeTo(server, accounts: [Fixture.ada])
        let before = server.asked.count
        await app.accountsChanged().value
        await until { app.list?.firstRowsAt != nil }
        XCTAssertEqual(app.place, .allInboxes)
        XCTAssertEqual(app.listed, .allInboxes, "every account's Inbox is listed in its place")
        let since = server.asked.dropFirst(before)
        XCTAssertFalse(since.contains { $0.path == API.Get.Messages.path && $0.query["account"] == Fixture.work },
                       "the removed account's Inbox isn't read again")
    }

    /// A removed account's Screener, open across the window, gives way to every account's Inbox: the Screener names
    /// its account apart from the place's own.
    func testARemovedAccountsScreenerGivesWayToEveryInbox() async {
        let server = server(accounts: [Fixture.ada, Fixture.work])
        let app = await opened(server)
        app.place = .desk(.screener(account: Fixture.work))
        changeTo(server, accounts: [Fixture.ada])
        await app.accountsChanged().value
        XCTAssertEqual(app.place, .allInboxes)
    }

    /// A new account's folders are read live from its server, which can take a while: the sheet that added it isn't
    /// held on them, and the rail names the account and the list says its Inbox is being read before they come.
    func testAnAccountAddedShowsBeforeItsFoldersAreReadFromItsServer() async {
        let server = server(accounts: [])
        let app = await opened(server)
        let folders = HeldRoute()
        app.mailbox.wire = Wire { method, target, body in
            if target.hasPrefix(API.Get.Folders.path + "?") { await folders.waitTillLetGo() }
            return await server.wire.send(method, target, body)
        }
        changeTo(server, accounts: [Fixture.ada], syncing: true)
        let reading = app.accountsChanged()
        await until { app.mailbox.facts.syncing }
        XCTAssertEqual(app.mailbox.accounts, [Fixture.ada], "the rail names it")
        XCTAssertTrue(app.mailbox.folders(of: Fixture.ada).isEmpty, "its folders haven't come yet")
        await folders.letGo()
        await reading.value
        XCTAssertEqual(app.mailbox.folders(of: Fixture.ada).count, Fixture.adaFolders.count, "then its folders come")
    }

    /// An account the rail doesn't list yet syncs: who the accounts are is read first, so its folders are read with the
    /// rest rather than after the next round.
    func testAnEventOfAnAccountTheRailDoesntListReadsTheAccountsBeforeTheirCounts() async {
        let server = server(accounts: [Fixture.ada])
        let app = await opened(server)
        XCTAssertTrue(app.mailbox.folders(of: Fixture.work).isEmpty)
        changeTo(server, accounts: [Fixture.ada, Fixture.work])
        let before = server.asked.count
        app.mailbox.heard(CoreEvent(id: "1", name: "sync-done",
                                    data: #"{"account": "work@example.test", "kept": 3, "junk": 0}"#))
        await until { !app.mailbox.folders(of: Fixture.work).isEmpty }
        XCTAssertEqual(app.mailbox.accounts, [Fixture.ada, Fixture.work])
        let since = server.asked.dropFirst(before).map(\.path)
        let digest = since.firstIndex(of: API.Get.Digest.path), folders = since.firstIndex(of: API.Get.Folders.path)
        XCTAssertNotNil(digest)
        XCTAssertLessThan(digest ?? .max, folders ?? .max, "the accounts first, then their folders")
    }

    /// An event of an account the rail lists, after which the digest names another: its folders are read too.
    func testAnAccountTheDigestNamesAnewHasItsFoldersRead() async {
        let server = server(accounts: [Fixture.ada])
        let app = await opened(server)
        changeTo(server, accounts: [Fixture.ada, Fixture.work])
        app.mailbox.heard(CoreEvent(id: "1", name: "new-mail", data: #"{"account": "ada@example.test", "count": 1}"#))
        await until { !app.mailbox.folders(of: Fixture.work).isEmpty }
        XCTAssertEqual(app.mailbox.folders(of: Fixture.work).count, Fixture.workFolders.count)
    }

    /// A first sync ends, and the older-mail lane then says folder-synced at each step, for minutes, with no mail
    /// changing that a person sees: the rail reads the digest again while the steps go on, not once they stop, and
    /// the folders the digest names come with it.
    func testTheRailFillsInAfterAFirstSyncWhileTheOlderMailLaneGoesOn() async {
        let server = server(accounts: [Fixture.ada])
        let app = await opened(server)
        changeTo(server, accounts: [Fixture.ada, Fixture.work])
        app.mailbox.heard(CoreEvent(id: "1", name: "sync-done",
                                    data: #"{"account": "ada@example.test", "kept": 40, "junk": 0}"#))
        let started = ContinuousClock.now
        var step = 1
        while app.mailbox.folders(of: Fixture.work).isEmpty {
            guard ContinuousClock.now - started < .seconds(3) else {
                return XCTFail("the rail waited out \(step) steps of the lane")
            }
            step += 1
            app.mailbox.heard(CoreEvent(id: String(step), name: "folder-synced",
                                        data: #"{"account": "ada@example.test", "folder": "INBOX", "ok": true, "new": 50}"#))
            try? await Task.sleep(for: .milliseconds(100))
        }
        XCTAssertEqual(app.mailbox.accounts, [Fixture.ada, Fixture.work])
        XCTAssertEqual(app.mailbox.folders(of: Fixture.work).count, Fixture.workFolders.count)
    }
}

/// Summaries land with no mail changing (`summarized`): the Inbox's Needs you tab, the rail and the brief's line over
/// the list read again and count the same mail, as the page's do; and a change of tab reads the brief's line with the
/// list, so neither says what the other no longer does.
@MainActor
final class MailboxSummariesTests: XCTestCase {
    private static let landed = CoreEvent(id: "1", name: "summarized", data: #"{"count": 2}"#)

    /// Every account's Inbox before the round: two messages, neither summarized, so nothing needs you yet.
    private func server() -> FakeServer {
        let server = Fixture.server(rows: [Fixture.row(1, seen: false), Fixture.row(2)])
        server.on("GET", API.Get.Digest.path, DeskFixture.digest(items: []))
        return server
    }

    /// The round landed: the first message needs you, in the Inbox's list and in the digest.
    private func summarize(_ server: FakeServer) {
        let rows = [Fixture.row(1, seen: false, needs: true, action: "Reply by Friday", summary: "Asks about Friday."),
                    Fixture.row(2, summary: "For your information.")]
        server.on("GET", API.Get.Inboxes.path) { asked in
            FakeServer.ok(API.InboxesListing(folder: "INBOX", grouped: asked.query["group"] != nil, hasMore: false,
                                             items: rows, offset: 0, ok: true, total: rows.count))
        }
        server.on("GET", API.Get.Digest.path, DeskFixture.digest(items: [
            DeskFixture.item(1, from: "grace.hopper@example.test", name: "Grace Hopper", subject: "Message 1",
                             action: "Reply by Friday"),
            DeskFixture.item(2, from: "grace.hopper@example.test", name: "Grace Hopper", subject: "Message 2",
                             needs: false, importance: 2),
        ]))
    }

    /// The window on every account's Inbox, at its Needs you tab, with the desk keeping the rail's tallies.
    private func opened(_ server: FakeServer) async -> (MailApp, Desk) {
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        let desk = Desk()
        desk.use(app, routes: server.wire)
        await desk.brief.keepTallied()?.value
        app.openList()
        await until { app.mailbox.facts.read == 2 }
        XCTAssertEqual(app.mailbox.tab, .needs)
        XCTAssertEqual(app.mailbox.facts.needs, 0)
        XCTAssertEqual(desk.brief.needs.count, 0)
        return (app, desk)
    }

    func testSummariesLandingReadTheTabTheRailAndTheBriefsLineAgainAndNotTheFolders() async {
        let server = server()
        let (app, desk) = await opened(server)
        let folders = server.asked(API.Get.Folders.path).count
        summarize(server)

        app.heard(Self.landed)
        await desk.heard(Self.landed)

        await until { app.mailbox.facts.needs == 1 && desk.brief.needs.count == 1 }
        XCTAssertEqual(app.list?.total, 1, "the Needs you tab lists it")
        XCTAssertEqual(desk.rail.needs(in: nil, grouped: app.mailbox.grouped), 1, "the rail counts it")
        XCTAssertEqual(BriefLine(needs: desk.brief.needs, waiting: 0, canWait: 0).tally,
                       "1 need you · 0 waiting · 0 can wait", "and so does the brief's line over the list")
        XCTAssertEqual(server.asked(API.Get.Folders.path).count, folders, "the folders count unread mail, which stays")
        withExtendedLifetime(desk) {}
    }

    /// The brief's line over one account's Inbox counts that account's mail alone, as the rail's Inbox does there: what
    /// needs you, waits and can wait in it. Over every Inbox it counts every account's.
    func testTheBriefsLineInOneAccountsInboxCountsThatAccountsMail() async throws {
        let server = server()
        server.on("GET", API.Get.Digest.path, DeskFixture.digest(items: [
            DeskFixture.item(101, from: "grace@example.test", name: "Grace Hopper", subject: "Figures"),
            DeskFixture.item(102, from: "lin@example.test", name: "Lin Mori", subject: "Plans"),
            DeskFixture.item(103, from: "otto@example.test", name: "Otto Brand", subject: "Invoice",
                             account: Fixture.work),
            DeskFixture.item(104, from: "news@example.test", name: "News", subject: "Weekly", needs: false,
                             importance: 1, safe: true, account: Fixture.work),
        ]))
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        let desk = Desk()
        desk.use(app, routes: server.wire)
        await desk.brief.keepTallied()?.value
        await until { desk.brief.needs.count == 3 }
        app.place = .allInboxes
        let every = try XCTUnwrap(MailboxTable.briefLine(desk.brief, mailbox: app.mailbox))
        XCTAssertEqual(every.tally, "3 need you · 3 waiting · 1 can wait", "every account's")
        app.place = .folder(account: Fixture.ada, folder: "INBOX")
        let ada = try XCTUnwrap(MailboxTable.briefLine(desk.brief, mailbox: app.mailbox))
        XCTAssertEqual(ada.tally, "2 need you · 0 waiting · 0 can wait", "Ada's alone, as the rail counts there")
        XCTAssertEqual(ada.gist, "Lin and Grace need you.", "and names who, in Ada's Inbox alone")
        XCTAssertEqual(desk.rail.needs(in: Fixture.ada, grouped: false), 2)
        app.place = .folder(account: Fixture.work, folder: "INBOX")
        let work = try XCTUnwrap(MailboxTable.briefLine(desk.brief, mailbox: app.mailbox))
        XCTAssertEqual(work.tally, "1 need you · 0 waiting · 1 can wait")
        app.place = .folder(account: Fixture.ada, folder: "Archive")
        XCTAssertNil(MailboxTable.briefLine(desk.brief, mailbox: app.mailbox), "no line over a folder")
        withExtendedLifetime(desk) {}
    }

    /// The round's word was missed (the stream reconnecting, say): Everything and back reads the brief's line with
    /// the list, rather than leave it saying nothing needs you over a list that says otherwise.
    func testAChangeOfTabReadsTheBriefsLineWithTheList() async {
        let server = server()
        let (app, desk) = await opened(server)
        summarize(server)
        let digests = server.asked(API.Get.Digest.path).count

        app.mailbox.tab = .all

        await until { desk.brief.needs.count == 1 }
        XCTAssertGreaterThan(server.asked(API.Get.Digest.path).count, digests)
        app.mailbox.tab = .needs
        await until { app.mailbox.facts.needs == 1 && app.list?.total == 1 }
        XCTAssertEqual(desk.brief.needs.count, app.mailbox.facts.needs)
        withExtendedLifetime(desk) {}
    }

    func testSummariesLandingReadTheListOnScreen() {
        XCTAssertTrue(MailApp.refreshesTheList(Self.landed))
    }
}

/// A long conversation archived from the Inbox: the server takes all twenty of its messages inside the request, its
/// moves reach the IMAP server there too, and no event follows. The rail's Needs you and the brief's line over the
/// list drop it once the request is answered, as the Needs you tab does, not when the next mail comes.
@MainActor
final class MailboxFilingTalliesTests: XCTestCase {
    func testArchivingALongConversationDropsItFromTheRailAndTheBriefsLineWithNoEvent() async throws {
        // The conversation's twenty messages each need you; two other messages do too.
        let thread = (201...220).map {
            DeskFixture.item($0, from: "otto@example.test", name: "Otto Brand", subject: "Re: Guild hall roof repair schedule",
                             thread: "t-roof")
        }
        let others = [DeskFixture.item(101, from: "grace@example.test", name: "Grace Hopper", subject: "Figures"),
                      DeskFixture.item(102, from: "lin@example.test", name: "Lin Mori", subject: "Plans")]
        let conversation = Fixture.row(220, count: 20, thread: "t-roof", needs: true, from: "Otto Brand",
                                       subject: "Re: Guild hall roof repair schedule")
        let server = Fixture.server(rows: [conversation, Fixture.row(101, needs: true), Fixture.row(102, needs: true)])
        server.on("GET", API.Get.Digest.path, DeskFixture.digest(items: thread + others))
        server.on("POST", API.Post.ThreadArchive.path) { _ in
            // Filed inside the request: the digest leaves them out from now on.
            server.on("GET", API.Get.Digest.path, DeskFixture.digest(items: others))
            return FakeServer.ok(API.ManyFiled(count: 20, failed: 0, filed: [], msg: "", ok: true, queued: 0,
                                               undoable: true))
        }
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        let desk = Desk()
        desk.use(app, routes: server.wire)
        await desk.brief.keepTallied()?.value
        app.openList()
        await until { app.list?.copy(at: 0) != nil }
        XCTAssertEqual(desk.rail.needs(in: nil, grouped: true), 3, "the conversation once, and the two others")
        XCTAssertEqual(desk.brief.needs.count, 3)
        let digests = server.asked(API.Get.Digest.path).count

        let row = try XCTUnwrap(app.list?.copy(at: 0))
        XCTAssertTrue(row.isConversation(grouped: true))
        let took = await app.mailbox.fileNow(.archive, rows: [row])
        XCTAssertTrue(took)

        // No event is heard: the request's answer reads the tallies again.
        await until { desk.rail.needs(in: nil, grouped: true) == 2 && desk.brief.needs.count == 2 }
        XCTAssertGreaterThan(server.asked(API.Get.Digest.path).count, digests, "the digest read again")
        XCTAssertEqual(BriefLine(needs: desk.brief.needs, waiting: 0, canWait: 0).tally,
                       "2 need you · 0 waiting · 0 can wait", "the brief's line over the list")
        withExtendedLifetime(desk) {}
    }
}

/// What a test's route waits on until the test lets it go.
private actor HeldRoute {
    private var free = false
    private var waiting: [CheckedContinuation<Void, Never>] = []

    func waitTillLetGo() async {
        guard !free else { return }
        await withCheckedContinuation { waiting.append($0) }
    }

    func letGo() {
        free = true
        waiting.forEach { $0.resume() }
        waiting = []
    }
}
