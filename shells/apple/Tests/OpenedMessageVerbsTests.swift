import AppKit
import Foundation
import XCTest

/// The desk's and the composer's routes over a test's wire, so one server sees every verb, whichever area sends it.
extension Wire: RouteCaller, Routes {
    func document<R: GetRoute>(_ route: R.Type, _ query: KeyValuePairs<String, String?>) async throws -> NotJSON
        where R.Answer == NotJSON
    {
        let answer = await send("GET", R.path + Core.query(query), Data())
        guard (200..<300).contains(answer.status) else { throw Problem(answer) }
        return NotJSON(data: answer.body, contentType: answer.contentType)
    }
}

/// Every verb on the message open, from every way a message reaches the reading pane: each acts on that message at the
/// server, by the route the page sends it to, with its folder, key and uid, and what was open moves on in the list or
/// the lane it came from, or closes to show where it was opened from. A message opened from the brief once had every
/// verb but Snooze and Later do nothing at all, as only the list's rows were looked in.
///
/// Each surface is pressed through the function it calls: the pane's bar, the Message menu and the palette as their
/// buttons run them (`Mailbox.file`, `askMove`, `star(on:)`, `setSeen`, the desk's `paneCommands` and `commands()`),
/// the single keys through the list's own `keyDown`, and a row's menu through its items. SwiftUI's buttons and the
/// menu bar themselves can't be pressed from a test.
@MainActor
final class OpenedMessageVerbsTests: XCTestCase {
    // MARK: The mail

    /// The Inbox: a message, a conversation of three, and three more.
    static let rows = [
        Fixture.row(1, needs: true, subject: "Launch date"),
        Fixture.row(2, account: Fixture.work, count: 3, subject: "Re: The engine's notes"),
        Fixture.row(3), Fixture.row(4), Fixture.row(5),
    ]
    /// What a search, the Feed and the Paper Trail find.
    static let hits = [Fixture.hit(50, subject: "Paid $20.00"), Fixture.hit(51, subject: "Paid $5.00"),
                       Fixture.hit(52, subject: "Paid $9.00")]
    /// A message no list on screen holds: the brief's "can wait", an answer's mail, a file's message, a sender's
    /// older mail.
    nonisolated static let away = MailCopy(key: 90, folder: "INBOX", uid: 90, account: Fixture.ada)
    /// Its head as the reader opened it, naming its conversation.
    nonisolated static func head(_ copy: MailCopy = away, thread: String? = "t90") -> API.MessageOpened {
        API.MessageOpened(account: copy.account, attachments: [], blockedCssUrls: 0, blockedImages: 0, cc: "", date: "Mon",
                          folder: copy.folder, fromAddr: "cy@example.test", fromName: "Cy Young", hasHtml: false,
                          key: copy.key, ok: true, replyTo: "", subject: "Whenever suits", text: "No rush.",
                          threadId: thread, to: Fixture.ada, uid: copy.uid)
    }

    /// As its conversation says it stands: unread, and starred.
    nonisolated static let awayMember = API.ThreadMessage(answered: false, flagged: true, folder: "INBOX", fromAddr: "cy@example.test",
                                              fromName: "Cy Young", hasAttach: false, isJunk: false, key: 90, seen: false,
                                              snippet: "No rush.", subject: "Whenever suits", uid: 90, when: "Mon")

    static func hitCopy(_ index: Int) -> MailCopy { MailCopy(API.FolderRow(hits[index])) }

    /// The server: the mailbox's routes, every verb's, the search's hits, the away message's conversation and any
    /// message's head.
    static func server(grouped: Bool = true) -> FakeServer {
        let server = Fixture.server(rows: rows, grouped: grouped)
        let filed = API.Filed(msg: "ok", ok: true, undoable: true)
        for path in [API.Post.Archive.path, API.Post.Trash.path, API.Post.Junk.path, API.Post.Move.path,
                     API.Post.Seen.path, API.Post.Flag.path] {
            server.on("POST", path, filed)
        }
        let many = API.ManyFiled(count: 3, failed: 0, filed: [], msg: "", ok: true, queued: 0, undoable: true)
        for path in [API.Post.ThreadArchive.path, API.Post.ThreadTrash.path, API.Post.ThreadMove.path] {
            server.on("POST", path, many)
        }
        server.on("POST", API.Post.Snooze.path, API.Snoozed(count: 1, ok: true, until: 1_800_000_000))
        server.on("POST", API.Post.Replylater.path, API.RepliedLater(ok: true, on: true, threadId: "t"))
        server.on("POST", API.Post.Undo.path, API.Undone(label: "Archive", ok: true, remaining: 0))
        server.on("GET", API.Get.Search.path, API.SearchAnswer.searchPage(API.SearchPage(
            generated: "", hasMore: false, items: hits, offset: 0, ok: true, query: "", scope: "all")))
        server.on("GET", API.Get.Thread.path) { asked in
            guard asked.query["thread_id"] == "t90", asked.query["account"] == Fixture.ada else {
                return FakeServer.refused(400, "missing account/thread")
            }
            return FakeServer.ok(API.Thread(account: Fixture.ada, count: 1, items: [awayMember], ok: true, replyKey: 90,
                                            threadId: "t90"))
        }
        server.on("GET", API.Get.MessageBody.path) { asked in
            let copy = MailCopy(key: Int(asked.query["key"] ?? "") ?? 0, folder: asked.query["folder"] ?? "",
                                uid: Int(asked.query["uid"] ?? "") ?? 0, account: Fixture.ada)
            return FakeServer.ok(API.MessageBodyAnswer.messageOpened(head(copy)))
        }
        return server
    }

    /// A window over `server`, every account's mail in it, Everything the Inbox's tab, and the desk joined.
    @MainActor
    struct World {
        let server: FakeServer
        let app: MailApp
        let desk: Desk
        var mailbox: Mailbox { app.mailbox }
    }

    func world(_ server: FakeServer = server()) async -> World {
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        let desk = Desk()
        desk.use(app, routes: server.wire)
        return World(server: server, app: app, desk: desk)
    }

    /// Ada's Inbox on screen, on `tab`, once its first rows have come.
    func inbox(_ app: MailApp, tab: InboxTab = .all) async {
        app.mailbox.tab = tab
        app.place = .folder(account: Fixture.ada, folder: "INBOX")
        await waiting(for: "the Inbox's rows") { app.list?.copy(at: 0) != nil }
    }

    // MARK: The ways a message is opened

    /// Every way a message reaches the reading pane.
    enum Way: String, CaseIterable {
        case listRow, conversation, upNext, search, feed, paperTrail
        case briefCanWait, briefNeedsYou, todayInvite, documentHub, gate, answer, paletteAnswer
        case notification, notificationAway, senderCard, senderCardAway, sheetOverAChosenRow
    }

    /// What a way opened: the message, whether the pane shows it as its whole conversation, whether the server has
    /// it starred, what opens once it leaves (nil: it closes), and its row in the list on screen, where a row's
    /// menu can be opened on it.
    struct Opened {
        let copy: MailCopy
        var whole = false
        var flagged = false
        let next: MailCopy?
        var row: API.FolderRow?
    }

    func open(_ way: Way, in world: World) async throws -> Opened {
        let app = world.app, mailbox = world.mailbox, desk = world.desk, away = Self.away
        /// The reader opening the away message, as the core answers it.
        func reads() { app.reader.hold(away, Self.head()) }
        switch way {
        case .listRow:
            await inbox(app)
            let row = try XCTUnwrap(app.list?.copy(at: 0))
            mailbox.chose([row])
            return Opened(copy: MailCopy(row), next: MailCopy(Self.rows[1]), row: row)
        case .conversation:
            await inbox(app)
            let row = try XCTUnwrap(app.list?.copy(at: 1))
            mailbox.chose([row])
            return Opened(copy: MailCopy(row), whole: true, next: MailCopy(Self.rows[2]), row: row)
        case .upNext:
            // Up next's row, and Keep & next, go on down the list.
            await inbox(app)
            mailbox.chose([try XCTUnwrap(app.list?.copy(at: 1))])
            mailbox.move(by: 1)
            let row = try XCTUnwrap(app.list?.copy(at: 2))
            return Opened(copy: MailCopy(row), next: MailCopy(Self.rows[3]), row: row)
        case .search:
            app.place = .search(Search(q: "paid", account: Fixture.ada))
            await waiting(for: "the search's rows") { app.list?.copy(at: 0) != nil }
            let row = try XCTUnwrap(app.list?.copy(at: 0))
            mailbox.chose([row])
            return Opened(copy: MailCopy(row), next: Self.hitCopy(1), row: row)
        case .feed, .paperTrail:
            // The river's row and the ledger's line choose their hit (`opening` in Hub.swift).
            app.place = .search(way == .feed ? Search(q: "category:newsletter", lane: .feed)
                                             : Search(q: "category:receipt", lane: .papertrail))
            await waiting(for: "the lane's hits") { mailbox.lane?.loaded == true }
            let row = API.FolderRow(try XCTUnwrap(mailbox.lane?.hits.first))
            mailbox.chose([row])
            return Opened(copy: MailCopy(row), next: Self.hitCopy(1))
        case .briefCanWait, .todayInvite:
            // The brief beside the Inbox on Needs you: its "can wait" card, and Today & next's invite, open mail
            // the list there doesn't hold (`BriefActions.open`).
            await inbox(app, tab: .needs)
            app.place = .desk(.brief)
            desk.open(way == .briefCanWait ? away : .named(key: away.key, folder: away.folder, uid: away.uid,
                                                            account: away.account))
            reads()
            return Opened(copy: away, flagged: true, next: nil)
        case .briefNeedsYou:
            // The brief's "needs you" card, whose message the list beside it holds.
            await inbox(app, tab: .needs)
            app.place = .desk(.brief)
            desk.open(MailCopy(Self.rows[0]))
            return Opened(copy: MailCopy(Self.rows[0]), next: nil)
        case .documentHub:
            app.place = .documents
            mailbox.open(away)
            reads()
            return Opened(copy: away, flagged: true, next: nil)
        case .gate:
            app.place = .desk(.screener(account: Fixture.ada))
            desk.open(away)
            reads()
            return Opened(copy: away, flagged: true, next: nil)
        case .answer:
            app.place = .desk(.ask)
            desk.open(away)
            reads()
            return Opened(copy: away, flagged: true, next: nil)
        case .paletteAnswer:
            desk.openCited(away)
            reads()
            return Opened(copy: away, flagged: true, next: nil)
        case .notification:
            mailbox.openInItsFolder(MailCopy(Self.rows[0]))
            await waiting(for: "the folder's rows") { app.list?.copy(at: 0) != nil }
            return Opened(copy: MailCopy(Self.rows[0]), next: MailCopy(Self.rows[1]), row: app.list?.copy(at: 0))
        case .notificationAway:
            mailbox.openInItsFolder(away)
            reads()
            await waiting(for: "the folder's rows") { app.list?.copy(at: 0) != nil }
            return Opened(copy: away, flagged: true, next: nil)
        case .senderCard:
            await inbox(app)
            mailbox.open(MailCopy(Self.rows[2]))
            return Opened(copy: MailCopy(Self.rows[2]), next: MailCopy(Self.rows[3]), row: app.list?.copy(at: 2))
        case .senderCardAway:
            await inbox(app)
            mailbox.open(away)
            reads()
            return Opened(copy: away, flagged: true, next: nil)
        case .sheetOverAChosenRow:
            // Your Week or Commitments, over the Inbox with a row chosen, opens another message.
            await inbox(app)
            mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
            desk.open(away)
            reads()
            return Opened(copy: away, flagged: true, next: nil)
        }
    }

    // MARK: The verbs

    /// Every verb, from every surface that offers one.
    enum Verb: String, CaseIterable {
        case paneFile, paneDelete, paneSnooze, paneLater
        case menuArchive, menuDelete, menuJunk, menuMoveTo, menuUnread, menuStar
        case keyE, keyHash, keyD, keyDelete, keyB, keyM, keyS, keyU, keyH, keyL
        case paletteArchive, paletteDelete, paletteJunk, paletteMoveTo, paletteStar, paletteUnread, paletteSnooze,
             paletteLater
        case rowArchive, rowDelete, rowJunk, rowMoveTo, rowUnread, rowStar, rowSnooze, rowLater

        enum Does: Equatable {
            case file(API.FilingVerb)
            case move
            /// Star, the key toggling it as the server has it.
            case star(toggles: Bool)
            case unread
            case snooze
            case later
        }

        var does: Does {
            switch self {
            case .paneFile, .menuArchive, .keyE, .paletteArchive, .rowArchive: .file(.archive)
            case .paneDelete, .menuDelete, .keyHash, .keyD, .keyDelete, .paletteDelete, .rowDelete: .file(.trash)
            case .menuJunk, .keyB, .paletteJunk, .rowJunk: .file(.junk)
            case .menuMoveTo, .keyM, .paletteMoveTo, .rowMoveTo: .move
            case .keyS: .star(toggles: true)
            case .menuStar, .paletteStar, .rowStar: .star(toggles: false)
            case .menuUnread, .keyU, .paletteUnread, .rowUnread: .unread
            case .paneSnooze, .keyH, .paletteSnooze, .rowSnooze: .snooze
            case .paneLater, .keyL, .paletteLater, .rowLater: .later
            }
        }

        /// A row's menu opens only on a row the list shows.
        var onARow: Bool { rawValue.hasPrefix("row") }
    }

    /// Press `verb` on what `world` has open, as its surface does.
    func press(_ verb: Verb, in world: World, opened: Opened) async throws {
        let mailbox = world.mailbox, desk = world.desk
        XCTAssertTrue(mailbox.hasTarget, "the pane's bar and the Message menu offer it")
        func palette(_ id: String) throws {
            try XCTUnwrap(mailbox.commands().first { $0.id == id }, "the palette offers \(id)").run()
        }
        func rowMenu(_ title: String) throws {
            let items = RowMenu.items(for: mailbox, clicked: opened.row)
            let item = try XCTUnwrap(items.first { $0.title == title }, "the row's menu offers \(title)")
            fire(item.submenu?.items.first ?? item)
        }
        switch verb {
        case .paneFile, .menuArchive: mailbox.file(.archive)
        case .paneDelete, .menuDelete: mailbox.file(.trash)
        case .menuJunk: mailbox.file(.junk)
        case .menuMoveTo: mailbox.askMove()
        case .menuUnread: mailbox.setSeen(Marks(mailbox.shownRows).unread)
        case .menuStar: mailbox.star(on: !Marks(mailbox.shownRows).unstars)
        case .paneSnooze: try XCTUnwrap(desk.paneCommands.first { $0.id == "snooze" }, "the bar's Snooze").run()
        case .paneLater: try XCTUnwrap(desk.paneCommands.first { $0.id == "later" }, "the bar's Later").run()
        case .keyE: key("e", mailbox)
        case .keyHash: key("#", mailbox)
        case .keyD: key("d", mailbox)
        case .keyDelete: key("\u{7f}", mailbox)
        case .keyB: key("b", mailbox)
        case .keyM: key("m", mailbox)
        case .keyS: key("s", mailbox)
        case .keyU: key("u", mailbox)
        case .keyH: key("h", mailbox)
        case .keyL: key("l", mailbox)
        case .paletteArchive: try palette("archive")
        case .paletteDelete: try palette("trash")
        case .paletteJunk: try palette("junk")
        case .paletteMoveTo: try palette("move-to")
        case .paletteStar: try palette("star")
        case .paletteUnread: try palette("unread")
        case .paletteSnooze: try palette("snooze")
        case .paletteLater: try palette("later")
        case .rowArchive: try rowMenu("Archive")
        case .rowDelete: try rowMenu("Delete")
        case .rowJunk: try rowMenu("Mark as Junk")
        case .rowMoveTo: try rowMenu("Move To")
        case .rowUnread: try rowMenu("Mark as Unread")
        case .rowStar: try rowMenu("Star")
        case .rowSnooze: try rowMenu("Snooze…")
        case .rowLater: try rowMenu("Reply later")
        }
        // Move To asks where (the sheet, or m), then moves to the account's first folder offered.
        if verb.does == .move, !verb.onARow {
            await waiting(for: "\(verb)'s sheet") { mailbox.moving != nil }
            let ask = try XCTUnwrap(mailbox.moving)
            XCTAssertEqual(ask.account, opened.copy.account, "\(verb) offers the message's own account's folders")
            mailbox.move(to: try XCTUnwrap(mailbox.destinations(ask).first))
        }
        // Snooze asks when (the chooser under the bar's button), then snoozes until tomorrow as its first choice does.
        if verb.does == .snooze {
            guard case .snoozeUntil(let copies, _)? = desk.sheet else { return XCTFail("\(verb) asks when") }
            XCTAssertEqual(copies, [opened.copy], "\(verb) asks for the message open")
            await desk.attention.snooze(copies, .preset(.tomorrow))
        }
    }

    /// A key pressed in the list, through its own `keyDown`.
    func key(_ typed: String, _ mailbox: Mailbox) {
        let table = MailboxTableView()
        table.mailbox = mailbox
        let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: typed == "#" ? .shift : [],
                                     timestamp: 0, windowNumber: 0, context: nil, characters: typed,
                                     charactersIgnoringModifiers: typed, isARepeat: false, keyCode: 0)
        table.keyDown(with: event!)
    }

    /// A menu item chosen.
    func fire(_ item: NSMenuItem) {
        guard let action = item.action else { return XCTFail("\(item.title) does nothing") }
        _ = (item.target as? NSObject)?.perform(action, with: item)
    }

    // MARK: What the server sees

    /// That `verb` reached the server for `opened`, and what happened after.
    func check(_ verb: Verb, _ way: Way, in world: World, opened: Opened) async throws {
        let server = world.server, app = world.app, mailbox = world.mailbox, copy = opened.copy
        let said = "\(way.rawValue) × \(verb.rawValue)"
        func asked(_ path: String) async throws -> Data {
            await waiting(for: said + " asks " + path) { !server.asked(path).isEmpty }
            return try XCTUnwrap(server.asked(path).first, said).body
        }
        switch verb.does {
        case .file(let kind):
            let path = switch kind {
            case .archive: opened.whole ? API.Post.ThreadArchive.path : API.Post.Archive.path
            case .trash: opened.whole ? API.Post.ThreadTrash.path : API.Post.Trash.path
            case .junk: API.Post.Junk.path
            }
            let body = try JSONDecoder().decode(API.MoveBody.self, from: try await asked(path))
            XCTAssertEqual(body, API.MoveBody(folder: copy.folder, key: copy.key, uid: copy.uid), said)
        case .move:
            let path = opened.whole ? API.Post.ThreadMove.path : API.Post.Move.path
            let body = try JSONDecoder().decode(API.MoveToBody.self, from: try await asked(path))
            XCTAssertEqual([body.folder, String(body.key), String(body.uid ?? -1)],
                           [copy.folder, String(copy.key), String(copy.uid)], said)
            XCTAssertNotEqual(body.to, copy.folder, said)
        case .star(let toggles):
            let body = try JSONDecoder().decode(API.FlagBody.self, from: try await asked(API.Post.Flag.path))
            XCTAssertEqual([body.folder, String(body.key), String(body.uid ?? -1)],
                           [copy.folder, String(copy.key), String(copy.uid)], said)
            XCTAssertEqual(body.flag, .flagged, said)
            if toggles { XCTAssertEqual(body.on, !opened.flagged, said + ": as the server has it starred or not") }
        case .unread:
            let body = try JSONDecoder().decode(API.SeenBody.self, from: try await asked(API.Post.Seen.path))
            XCTAssertEqual(body, API.SeenBody(folder: copy.folder, key: copy.key, seen: false, uid: copy.uid), said)
        case .snooze:
            let body = try JSONDecoder().decode(API.SnoozeBody.self, from: try await asked(API.Post.Snooze.path))
            XCTAssertEqual([body.folder, body.key.map(String.init), body.uid.map(String.init)],
                           [copy.folder, String(copy.key), String(copy.uid)], said)
        case .later:
            let body = try JSONDecoder().decode(API.ReplyLaterBody.self, from: try await asked(API.Post.Replylater.path))
            XCTAssertEqual([body.folder, body.key.map(String.init), body.uid.map(String.init)],
                           [copy.folder, String(copy.key), String(copy.uid)], said)
            XCTAssertEqual(body.on, true, said)
        }
        // Filed, moved or snoozed, it leaves: the list or the lane moves on, else the message closes to where it was
        // opened from. Undo is offered, and takes it back. Reply later keeps it where it is, and open.
        switch verb.does {
        case .file, .move, .snooze:
            await waiting(for: said + " says so") { mailbox.toast != nil }
            XCTAssertNotNil(mailbox.toast?.undo, said + " offers Undo")
            await waiting(for: said + " moves on to \(opened.next.map { String($0.key) } ?? "nothing")") {
                app.selection.copy == opened.next
            }
            if opened.next == nil { XCTAssertTrue(mailbox.chosenRows.isEmpty, said) }
            mailbox.undo()
            await waiting(for: said + " undone") { !server.asked(API.Post.Undo.path).isEmpty }
        default:
            await waiting(for: said + " says so") { mailbox.toast != nil }
            XCTAssertEqual(app.selection.copy, copy, said + " keeps it open")
        }
        // Nothing else was acted on: no other message, and no other verb.
        XCTAssertEqual(server.asked.filter { Self.verbs.contains($0.path) }.count, 1, said + ": one request")
    }

    /// The routes a verb changes mail by.
    static let verbs: Set<String> = [
        API.Post.Archive.path, API.Post.Trash.path, API.Post.Junk.path, API.Post.Move.path, API.Post.Seen.path,
        API.Post.Flag.path, API.Post.ThreadArchive.path, API.Post.ThreadTrash.path, API.Post.ThreadMove.path,
        API.Post.Snooze.path, API.Post.Replylater.path,
    ]

    // MARK: The matrix

    func testEveryVerbActsOnTheMessageOpenHoweverItWasOpened() async throws {
        for way in Way.allCases {
            for verb in Verb.allCases {
                // Each pair on a window of its own, and a pair that fails says so and lets the rest run.
                do {
                    let world = await world()
                    let opened = try await open(way, in: world)
                    XCTAssertEqual(world.app.selection.copy, opened.copy, "\(way): the reader opens it, and Reply answers it")
                    guard !verb.onARow || opened.row != nil else { continue }
                    try await press(verb, in: world, opened: opened)
                    try await check(verb, way, in: world, opened: opened)
                } catch {
                    XCTFail("\(way.rawValue) × \(verb.rawValue): \(error)")
                }
            }
        }
    }

    /// The message open from outside the list is closed once it leaves, so the place it was opened from shows again:
    /// the brief beside the Inbox, the gate across the window, the answer, the Document Hub.
    func testAMessageOpenedFromOutsideTheListClosesBackToWhereItWasOpened() async throws {
        let places: [(Way, Place)] = [(.briefCanWait, .desk(.brief)), (.gate, .desk(.screener(account: Fixture.ada))),
                                      (.answer, .desk(.ask)), (.documentHub, .documents)]
        for (way, place) in places {
            let world = await world()
            _ = try await open(way, in: world)
            await world.mailbox.fileNow(.trash)
            XCTAssertNil(world.app.selection.copy, "\(way)")
            XCTAssertEqual(world.app.place, place, "\(way): where it was opened from shows again")
        }
    }

    /// In Reply Later, Done takes the message off the list shown, so the list moves on as File does. Reply later
    /// anywhere else keeps the message where it is, and open (the table's `later` verbs).
    func testDoneInReplyLaterMovesOnAsFileDoes() async throws {
        let world = await world()
        world.app.place = .virtual(.replyLater, account: Fixture.ada)
        await waiting(for: "Reply Later's rows") { world.app.list?.copy(at: 1) != nil }
        world.mailbox.chose([try XCTUnwrap(world.app.list?.copy(at: 0))])
        let done = try XCTUnwrap(world.desk.paneCommands.first { $0.id == "later" }, "the bar's Later")
        XCTAssertEqual(done.label, "Reply later — Done")
        done.run()
        await waiting(for: "Done asks") { !world.server.asked(API.Post.Replylater.path).isEmpty }
        let body = try JSONDecoder().decode(API.ReplyLaterBody.self,
                                            from: try XCTUnwrap(world.server.asked(API.Post.Replylater.path).first).body)
        XCTAssertEqual(body.key, Self.rows[0].key)
        XCTAssertEqual(body.on, false, "Done takes it off Reply Later")
        await waiting(for: "the list moves on") { world.app.selection.copy == MailCopy(Self.rows[1]) }
    }

    /// The pane over a message no list holds, filed already or in Trash, takes it back to its account's Inbox.
    func testMoveToInboxAndRestoreTakeTheMessageOpenBack() async throws {
        for folder in ["Archive", "Trash"] {
            let world = await world()
            let copy = MailCopy(key: 91, folder: folder, uid: 91, account: Fixture.ada)
            world.app.place = .desk(.brief)
            world.desk.open(copy)
            // The bar's Move to Inbox and Restore.
            world.mailbox.restore(await world.mailbox.targets())
            await waiting(for: "\(folder) restores") { !world.server.asked(API.Post.Move.path).isEmpty }
            let body = try JSONDecoder().decode(API.MoveToBody.self,
                                                from: try XCTUnwrap(world.server.asked(API.Post.Move.path).first).body)
            XCTAssertEqual(body, API.MoveToBody(folder: folder, key: 91, to: "INBOX", uid: 91), folder)
        }
    }

    /// Reply, Reply All, Forward and the reply box answer the message the reader opened: the pane hands the composer
    /// the reader's copy, which the composer opens the original of.
    func testAReplyAnswersTheMessageOpenedFromOutsideTheList() async throws {
        let world = await world()
        _ = try await open(.briefCanWait, in: world)
        let composing = Composing()
        composing.use(world.server.wire, digest: Fixture.digest())
        let reply = try await replied(composing, try XCTUnwrap(world.app.selection.copy))
        reply.close(force: true)
        let asked = try XCTUnwrap(world.server.asked(API.Get.MessageBody.path).last)
        XCTAssertEqual(asked.query, ["key": "90", "folder": "INBOX", "uid": "90"])
    }

    // MARK: Nothing done silently

    /// A key with nothing chosen or open says so, where it once did nothing at all. The bar's buttons and the Message
    /// menu's are off then (`hasTarget`).
    func testAKeyWithNothingOpenSaysSo() async throws {
        let world = await world()
        await inbox(world.app)
        XCTAssertFalse(world.mailbox.hasTarget)
        for typed in ["e", "#", "\u{7f}", "b", "m", "s", "u"] {
            world.mailbox.dismissToast()
            key(typed, world.mailbox)
            await waiting(for: "\(typed) says why") { world.mailbox.toast != nil }
            XCTAssertEqual(world.mailbox.toast?.text, "Choose a message first", typed)
        }
        XCTAssertFalse(world.server.asked.contains { Self.verbs.contains($0.path) }, "nothing was acted on")
    }

    /// A move to the folder the message is in already says so, and asks nothing.
    func testAMoveToWhereTheMessageIsSaysSo() async throws {
        let world = await world()
        world.mailbox.open(MailCopy(key: 92, folder: "Receipts", uid: 92, account: Fixture.ada))
        await world.mailbox.moveNow(to: "Receipts", label: "Receipts")
        XCTAssertEqual(world.mailbox.toast?.text, "Already in Receipts")
        XCTAssertTrue(world.server.asked(API.Post.Move.path).isEmpty)
    }

    // MARK: As it stands now

    /// Star and Mark as Unread read the row as the list holds it now, not as it was when chosen: starred once, the
    /// next press unstars, and the menus say so.
    func testATogglesSecondPressReadsTheRowAsTheListNowHasIt() async throws {
        let server = Self.server()
        let world = await world(server)
        await inbox(world.app)
        let row = try XCTUnwrap(world.app.list?.copy(at: 0))
        world.mailbox.chose([row])
        XCTAssertFalse(Marks(world.mailbox.shownRows).unstars)
        await world.mailbox.starNow()
        var starred = Self.rows
        starred[0].flagged = true
        server.on("GET", API.Get.Messages.path, API.MessagesAnswer.folderListing(API.FolderListing(
            account: Fixture.ada, folder: "INBOX", grouped: true, hasMore: false, items: starred, offset: 0, ok: true,
            total: starred.count)))
        world.app.list?.refresh()
        await waiting(for: "the list reads the star") { world.app.list?.copy(at: 0)?.flagged == true }
        XCTAssertTrue(Marks(world.mailbox.shownRows).unstars, "the Message menu says Unstar")
        let rowMenu = RowMenu.items(for: world.mailbox, clicked: row).map(\.title)
        XCTAssertTrue(rowMenu.contains("Unstar"), "and so does the row's menu")
        await world.mailbox.starNow()
        let flags = try server.asked(API.Post.Flag.path).map { try JSONDecoder().decode(API.FlagBody.self, from: $0.body).on }
        XCTAssertEqual(flags, [true, false], "the second press unstars")
    }

    /// A message no list holds is read from the server as it stands: unread and starred, as its conversation says.
    func testAMessageNoListHoldsIsReadAsItsConversationSays() async throws {
        let world = await world()
        _ = try await open(.briefCanWait, in: world)
        let row = await world.mailbox.openRow(Self.away)
        XCTAssertEqual(MailCopy(row), Self.away)
        XCTAssertTrue(row.flagged)
        XCTAssertFalse(row.seen)
        XCTAssertFalse(row.isConversation(grouped: true), "it stands for itself, as the pane shows it")
        XCTAssertEqual(row.fromAddr, "cy@example.test")
        XCTAssertTrue(Marks(world.mailbox.shownRows).unstars, "the Message menu says Unstar once it is read")
        XCTAssertTrue(Marks(world.mailbox.shownRows).unread, "and Mark as Read")

        // One whose conversation isn't known is named by its copy alone, read and not starred.
        let alone = API.FolderRow(Self.away, head: Self.head(thread: nil), member: nil)
        XCTAssertTrue(alone.seen)
        XCTAssertFalse(alone.flagged)
        XCTAssertEqual(alone.subject, "Whenever suits")
    }

    /// A message opened from outside the list is marked read as it is read, as one from the list is: the reader's
    /// pane asks whether it is unread as its conversation says (`ReaderPane.dwell`, a view's task, asks this after its
    /// moment on the message). Marked, it is read again, and isn't marked twice.
    func testAMessageOpenedFromOutsideTheListIsKnownUnreadSoItIsMarkedRead() async throws {
        let world = await world()
        _ = try await open(.briefCanWait, in: world)
        let unread = await world.mailbox.opensUnread(Self.away)
        XCTAssertTrue(unread)

        var read = Self.awayMember
        read.seen = true
        world.server.on("GET", API.Get.Thread.path, API.Thread(account: Fixture.ada, count: 1, items: [read], ok: true,
                                                               replyKey: 90, threadId: "t90"))
        world.mailbox.rereadOutside()
        await waiting(for: "the message read again") { !Marks(world.mailbox.shownRows).unread }
        let unreadNow = await world.mailbox.opensUnread(Self.away)
        XCTAssertFalse(unreadNow, "read, it isn't marked again")

        // A row the list holds is as the list has it, and a message not open is never asked after.
        await inbox(world.app)
        let row = try XCTUnwrap(world.app.list?.copy(at: 0))
        world.mailbox.chose([row])
        let listed = await world.mailbox.opensUnread(MailCopy(row))
        XCTAssertEqual(listed, !row.seen)
        let notOpen = await world.mailbox.opensUnread(Self.away)
        XCTAssertFalse(notOpen)
    }
}

/// Wait on the main actor until `done`, or fail after five seconds saying what was waited for.
@MainActor
func waiting(for what: String, _ done: @MainActor () -> Bool) async {
    let started = ContinuousClock.now
    while !done() {
        guard ContinuousClock.now - started < .seconds(5) else {
            XCTFail("still waiting: \(what)")
            return
        }
        try? await Task.sleep(for: .milliseconds(2))
    }
}
