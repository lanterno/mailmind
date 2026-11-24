import AppKit
import Foundation
import Observation
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
/// buttons run them (`Mailbox.fileOpen`, `file`, `askMove`, `star(on:)`, `setSeen`, the desk's `paneCommands` and
/// `commands()`), the single keys through the list's own `keyDown`, and a row's menu through its items. SwiftUI's
/// buttons and the menu bar themselves can't be pressed from a test.
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
                                              sentTo: [], sentToMore: 0,
                                              snippet: "No rush.", subject: "Whenever suits", uid: 90, when: "Mon")

    static func hitCopy(_ index: Int) -> MailCopy { MailCopy(API.FolderRow(hits[index])) }

    /// The server: the mailbox's routes, every verb's, the search's hits, the away message's conversation and any
    /// message's head.
    static func server(rows: [API.FolderRow] = rows, grouped: Bool = true) -> FakeServer {
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

    /// Every way a message reaches the reading pane, and a window of its own: one over a message the Inbox lists
    /// while the mail window has another open, and one over a message no list holds while the mail window has two
    /// rows marked.
    enum Way: String, CaseIterable {
        case listRow, conversation, upNext, search, feed, paperTrail
        case briefCanWait, briefNeedsYou, todayInvite, documentHub, gate, answer, paletteAnswer
        case notification, notificationAway, senderCard, senderCardAway, sheetOverAChosenRow
        case ownWindow, ownWindowAway
    }

    /// What a way opened: the message, whether the pane shows it as its whole conversation, whether the server has
    /// it starred and unread, what opens once it leaves (nil: it closes), and its row in the list on screen, where a
    /// row's menu can be opened on it. In a window of its own: that window, and what the mail window had open and
    /// chosen, which stays.
    struct Opened {
        let copy: MailCopy
        var whole = false
        var flagged = false
        var unread = false
        let next: MailCopy?
        var row: API.FolderRow?
        var window: MessageWindowVerbs?
        var mainOpen: MailCopy?
        var mainChosen: [MailCopy] = []
    }

    /// A message in a window of its own over `world`'s mailbox, its reader open on `head`, once it knows how the
    /// message stands, as the window reads it as it opens.
    func window(on copy: MailCopy, head: API.MessageOpened, in world: World) async -> MessageWindowVerbs {
        let reader = ReaderModel()
        reader.hold(copy, head)
        let window = MessageWindowVerbs(copy: copy, mailbox: world.mailbox, reader: reader, routes: world.server.wire)
        await window.reread()
        return window
    }

    func open(_ way: Way, in world: World) async throws -> Opened {
        let app = world.app, mailbox = world.mailbox, desk = world.desk, away = Self.away
        /// The reader opening the away message, as the core answers it, and the mailbox reading how it stands, as it
        /// does as it opens one from outside the list: the menus then say Unstar and Mark as Read.
        func reads() async {
            app.reader.hold(away, Self.head())
            await waiting(for: "how it stands") { Marks(mailbox.shownRows) == Marks([API.FolderRow(away, head: nil,
                                                                                               member: Self.awayMember)]) }
        }
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
            await reads()
            return Opened(copy: away, flagged: true, unread: true, next: nil)
        case .briefNeedsYou:
            // The brief's "needs you" card, whose message the list beside it holds.
            await inbox(app, tab: .needs)
            app.place = .desk(.brief)
            desk.open(MailCopy(Self.rows[0]))
            return Opened(copy: MailCopy(Self.rows[0]), next: nil)
        case .documentHub:
            app.place = .documents
            mailbox.open(away)
            await reads()
            return Opened(copy: away, flagged: true, unread: true, next: nil)
        case .gate:
            app.place = .desk(.screener(account: Fixture.ada))
            desk.open(away)
            await reads()
            return Opened(copy: away, flagged: true, unread: true, next: nil)
        case .answer:
            app.place = .desk(.ask)
            desk.open(away)
            await reads()
            return Opened(copy: away, flagged: true, unread: true, next: nil)
        case .paletteAnswer:
            desk.openCited(away)
            await reads()
            return Opened(copy: away, flagged: true, unread: true, next: nil)
        case .notification:
            mailbox.openInItsFolder(MailCopy(Self.rows[0]))
            await waiting(for: "the folder's rows") { app.list?.copy(at: 0) != nil }
            return Opened(copy: MailCopy(Self.rows[0]), next: MailCopy(Self.rows[1]), row: app.list?.copy(at: 0))
        case .notificationAway:
            mailbox.openInItsFolder(away)
            await reads()
            await waiting(for: "the folder's rows") { app.list?.copy(at: 0) != nil }
            return Opened(copy: away, flagged: true, unread: true, next: nil)
        case .senderCard:
            await inbox(app)
            mailbox.open(MailCopy(Self.rows[2]))
            return Opened(copy: MailCopy(Self.rows[2]), next: MailCopy(Self.rows[3]), row: app.list?.copy(at: 2))
        case .senderCardAway:
            await inbox(app)
            mailbox.open(away)
            await reads()
            return Opened(copy: away, flagged: true, unread: true, next: nil)
        case .sheetOverAChosenRow:
            // Your Week or Commitments, over the Inbox with a row chosen, opens another message.
            await inbox(app)
            mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
            desk.open(away)
            await reads()
            return Opened(copy: away, flagged: true, unread: true, next: nil)
        case .ownWindow:
            // A double-click on the conversation's row, with the first row open in the mail window: the window
            // shows its newest message, and acts on that alone.
            await inbox(app)
            mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
            let copy = MailCopy(Self.rows[1])
            let window = await window(on: copy, head: Self.head(copy, thread: "t2"), in: world)
            return Opened(copy: copy, next: nil, window: window, mainOpen: MailCopy(Self.rows[0]),
                          mainChosen: [MailCopy(Self.rows[0])])
        case .ownWindowAway:
            // A sender's older mail in a window of its own, with two rows marked in the mail window.
            await inbox(app)
            let marked = [try XCTUnwrap(app.list?.copy(at: 0)), try XCTUnwrap(app.list?.copy(at: 3))]
            mailbox.chose(marked)
            let window = await window(on: away, head: Self.head(), in: world)
            return Opened(copy: away, flagged: true, unread: true, next: nil, window: window,
                          mainChosen: marked.map(MailCopy.init))
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
        case windowArchive, windowDelete, windowJunk, windowMoveTo, windowUnread, windowStar, windowSnooze, windowLater

        enum Does: Equatable {
            case file(API.FilingVerb)
            case move
            /// Star, or Unstar where the server has it starred: the key toggles it, and the menus say which.
            case star
            case unread
            case snooze
            case later
        }

        var does: Does {
            switch self {
            case .paneFile, .menuArchive, .keyE, .paletteArchive, .rowArchive, .windowArchive: .file(.archive)
            case .paneDelete, .menuDelete, .keyHash, .keyD, .keyDelete, .paletteDelete, .rowDelete, .windowDelete:
                .file(.trash)
            case .menuJunk, .keyB, .paletteJunk, .rowJunk, .windowJunk: .file(.junk)
            case .menuMoveTo, .keyM, .paletteMoveTo, .rowMoveTo, .windowMoveTo: .move
            case .keyS, .menuStar, .paletteStar, .rowStar, .windowStar: .star
            case .menuUnread, .keyU, .paletteUnread, .rowUnread, .windowUnread: .unread
            case .paneSnooze, .keyH, .paletteSnooze, .rowSnooze, .windowSnooze: .snooze
            case .paneLater, .keyL, .paletteLater, .rowLater, .windowLater: .later
            }
        }

        /// A row's menu opens only on a row the list shows.
        var onARow: Bool { rawValue.hasPrefix("row") }
        /// The Message menu, and its keys, in a message's own window, which has no list, bar or palette.
        var inAWindow: Bool { rawValue.hasPrefix("window") }
    }

    /// Press `verb` on what `world` has open, as its surface does.
    func press(_ verb: Verb, in world: World, opened: Opened) async throws {
        let mailbox = world.mailbox, desk = world.desk
        if let window = opened.window { return try await press(verb, in: window, world: world) }
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
        case .paneFile: mailbox.fileOpen(.archive)
        case .paneDelete: mailbox.fileOpen(.trash)
        case .menuArchive: mailbox.file(.archive)
        case .menuDelete: mailbox.file(.trash)
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
        case .windowArchive, .windowDelete, .windowJunk, .windowMoveTo, .windowUnread, .windowStar, .windowSnooze,
             .windowLater:
            XCTFail("\(verb) is a message window's")
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

    /// Press `verb` in a message's own window, as its Message menu runs it: the mailbox's verbs as `MessageMenuVerbs`
    /// gives them for that window, and attention's (Snooze's first choice, Reply Later) on what `DeskMessageItems`
    /// gives them to there. The menu's keys are its items'.
    func press(_ verb: Verb, in window: MessageWindowVerbs, world: World) async throws {
        let menu = MessageMenuVerbs(window: window, mailbox: world.mailbox)
        XCTAssertTrue(menu.acting, "the Message menu offers \(verb)")
        let marks = Marks(menu.shown)
        let attending = try XCTUnwrap(DeskMessageItems.attending(window: window, desk: world.desk),
                                      "the Message menu offers Snooze and Reply Later")
        XCTAssertEqual(attending.copies, [window.copy], "on the window's message")
        XCTAssertNil(attending.stack)
        switch verb {
        case .windowArchive: menu.file(.archive)
        case .windowDelete: menu.file(.trash)
        case .windowJunk: menu.file(.junk)
        case .windowMoveTo: menu.askMove()
        case .windowUnread: menu.setSeen(marks.unread)
        case .windowStar: menu.star(!marks.unstars)
        case .windowSnooze: await attending.model.snooze(attending.copies, .preset(.tomorrow))
        case .windowLater: await attending.model.replyLater(attending.copies)
        default: XCTFail("\(verb) isn't in a message's window")
        }
        // Move To asks where, over the window, then moves to the account's first folder offered.
        if verb.does == .move {
            await waiting(for: "\(verb)'s sheet") { window.moving != nil }
            let ask = try XCTUnwrap(window.moving)
            XCTAssertEqual(ask, MoveAsk(account: window.copy.account, from: window.copy.folder))
            XCTAssertTrue(world.mailbox.moving == nil, "the mail window asks nothing")
            window.move(to: try XCTUnwrap(world.mailbox.destinations(ask).first))
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
        case .star:
            let body = try JSONDecoder().decode(API.FlagBody.self, from: try await asked(API.Post.Flag.path))
            XCTAssertEqual([body.folder, String(body.key), String(body.uid ?? -1)],
                           [copy.folder, String(copy.key), String(copy.uid)], said)
            XCTAssertEqual(body.flag, .flagged, said)
            XCTAssertEqual(body.on, !opened.flagged, said + ": as the server has it starred or not")
        case .unread:
            let body = try JSONDecoder().decode(API.SeenBody.self, from: try await asked(API.Post.Seen.path))
            // The Message menu says what it will do as the message stands: Mark as Read while it is unread.
            let seen = (verb == .menuUnread || verb == .windowUnread) && opened.unread
            XCTAssertEqual(body, API.SeenBody(folder: copy.folder, key: copy.key, seen: seen, uid: copy.uid), said)
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
        if let window = opened.window {
            // In a window of its own, only its message was acted on: the mail window keeps what it had open and
            // chosen. Once the message has left, its window closes, and the mail window says so with Undo.
            switch verb.does {
            case .file, .move, .snooze:
                await waiting(for: said + " closes the window") { window.left }
                XCTAssertFalse(MessageMenuVerbs(window: window, mailbox: nil).acting, said + ": nothing more to do")
                await waiting(for: said + " offers Undo") { mailbox.toast?.undo != nil }
                mailbox.undo()
                await waiting(for: said + " undone") { !server.asked(API.Post.Undo.path).isEmpty }
            default:
                await waiting(for: said + " says so") { mailbox.toast != nil }
                XCTAssertFalse(window.left, said + " keeps the window open")
            }
            XCTAssertEqual(app.selection.copy, opened.mainOpen, said + ": the mail window's message stays open")
            XCTAssertEqual(mailbox.marked, opened.mainChosen, said + ": and its rows chosen")
            XCTAssertEqual(server.asked.filter { Self.verbs.contains($0.path) }.count, 1, said + ": one request")
            return
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
            // Back, it opens again as it was opened: chosen in its row where the list or the lane holds it, else
            // from outside them.
            await waiting(for: said + " opens it again") { app.selection.copy == copy }
            XCTAssertEqual(mailbox.marked, opened.next == nil ? [] : [copy], said)
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
                    guard verb.inAWindow == way.rawValue.hasPrefix("ownWindow") else { continue }
                    let world = await world()
                    let opened = try await open(way, in: world)
                    if let window = opened.window {
                        XCTAssertEqual(window.copy, opened.copy, "\(way): the window shows it")
                    } else {
                        XCTAssertEqual(world.app.selection.copy, opened.copy, "\(way): the reader opens it, and Reply answers it")
                    }
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

    /// Mute takes the thread off the Inbox, so the list moves on as File does, one message or several, as the page's
    /// mute does; and a message opened from outside the list closes.
    func testMuteMovesOnAsFileDoes() async throws {
        let server = Self.server()
        server.on("POST", API.Post.Mute.path, API.OnThread(ok: true, threadId: "t1"))
        let world = await world(server)
        await inbox(world.app)
        world.mailbox.chose([try XCTUnwrap(world.app.list?.copy(at: 0))])
        let mute = try XCTUnwrap(world.desk.paneCommands.first { $0.id == "mute" }, "the bar's Mute")
        XCTAssertEqual(mute.label, "Mute thread")
        mute.run()
        await waiting(for: "Mute asks") { !world.server.asked(API.Post.Mute.path).isEmpty }
        await waiting(for: "the list moves on") { world.app.selection.copy == MailCopy(Self.rows[1]) }
        XCTAssertEqual(world.mailbox.marked, [MailCopy(Self.rows[1])], "chosen in its row")

        await world.desk.attention.mute([MailCopy(Self.rows[1]), MailCopy(Self.rows[2])])
        await waiting(for: "several move on past them") { world.app.selection.copy == MailCopy(Self.rows[3]) }

        world.mailbox.open(Self.away)
        await world.desk.attention.mute([try XCTUnwrap(world.app.selection.copy)])
        await waiting(for: "a message opened from outside closes") { world.app.selection.copy == nil }
    }

    /// A commitment's Done files its message to Archive from the Commitments sheet: the message open moves on as
    /// File moves it on, chosen in its row, and one open from outside the list closes. The reader once held it,
    /// saying it wasn't here any more, with its buttons.
    func testDoneOnACommitmentMovesTheMessageOpenOnAsFileDoes() async throws {
        let world = await world()
        await inbox(world.app)
        world.mailbox.chose([try XCTUnwrap(world.app.list?.copy(at: 0))])
        func item(_ copy: MailCopy) -> API.DigestItem {
            var item = DeskFixture.item(copy.key, from: "ada@example.test", name: "Ada Lovelace",
                                        subject: "Launch date", action: "Send the date", commitment: true)
            item.uid = copy.uid
            return item
        }
        await world.desk.brief.file(item(MailCopy(Self.rows[0])), .archive)
        XCTAssertEqual(world.server.asked(API.Post.Archive.path).count, 1)
        await waiting(for: "the list moves on") { world.app.selection.copy == MailCopy(Self.rows[1]) }
        XCTAssertEqual(world.mailbox.marked, [MailCopy(Self.rows[1])], "chosen in its row")

        world.mailbox.open(Self.away)
        await world.desk.brief.file(item(Self.away), .archive)
        await waiting(for: "a message opened from outside closes") { world.app.selection.copy == nil }
    }

    /// Your Week leaves out what was filed since it was read, as the server reads it, once the mail changes: a
    /// commitment's Done, or a command from anywhere (`commands-changed`). It once still listed and counted it.
    func testYourWeekIsReadAgainOnceMailIsFiled() async throws {
        final class Filed: @unchecked Sendable {
            var yes = false
        }
        for viaDone in [true, false] {
            let filed = Filed()
            let server = Self.server()
            server.on("GET", API.Get.Review.path) { _ in
                var review = DeskFixture.review
                if filed.yes {
                    review.needs = []
                    review.stats.needsYou = 0
                }
                return FakeServer.ok(API.ReviewAnswer.review(review))
            }
            server.on("POST", API.Post.Archive.path) { _ in
                filed.yes = true
                return FakeServer.ok(API.Filed(msg: "ok", ok: true, undoable: true))
            }
            let world = await world(server)
            await world.desk.review.open()
            XCTAssertEqual(world.desk.review.review?.needs.count, 1)
            if viaDone {
                let need = try XCTUnwrap(DeskFixture.review.needs.first)
                var item = DeskFixture.item(try XCTUnwrap(need.key), from: "grace@example.test", name: "Grace Hopper",
                                            subject: need.subject, action: need.action, commitment: true)
                item.uid = try XCTUnwrap(need.uid)
                await world.desk.brief.file(item, .archive)
            } else {
                filed.yes = true
                await world.desk.heard(CoreEvent(id: "1", name: "commands-changed", data: "{}"))
            }
            await waiting(for: "the week read again") { world.desk.review.review?.needs.isEmpty == true }
            XCTAssertEqual(world.desk.review.review?.stats.needsYou, 0, "nor counted")
            XCTAssertEqual(world.desk.review.review?.narrative, DeskFixture.review.narrative, "nothing written again")
            XCTAssertTrue(world.server.asked(API.Post.Review.path).allSatisfy { $0.method == "GET" }, "only read")
            XCTAssertTrue(world.server.asked(API.Post.ReviewRefresh.path).isEmpty)
        }
    }

    /// Snooze and Mute offer Undo on their toast, and so on Edit ▸ Undo as "Undo Snooze" and "Undo Mute": ⌘Z, sent
    /// to the window as the menu sends it, asks the server to undo once, as z and the toast's Undo do; and once z has
    /// taken it back, Edit ▸ Undo has nothing of it left to take back again.
    func testSnoozeAndMuteAreOnEditUndoAndZAndCommandZShareOneUndo() async throws {
        let server = Self.server()
        server.on("POST", API.Post.Mute.path, API.OnThread(ok: true, threadId: "t1"))
        let world = await world(server)
        await inbox(world.app)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300), styleMask: [.titled],
                              backing: .buffered, defer: true)
        let undo = try XCTUnwrap(window.undoManager)
        undo.groupsByEvent = false
        world.mailbox.undoManager = undo
        func undos() -> Int { world.server.asked(API.Post.Undo.path).count }
        func done(_ verb: () async -> Void) async {
            undo.beginUndoGrouping()
            await verb()
            undo.endUndoGrouping()
        }
        let commandZ = Selector(("undo:"))

        await done { await world.desk.attention.snooze([MailCopy(Self.rows[0])], .preset(.tomorrow)) }
        XCTAssertNotNil(world.mailbox.toast?.undo, "the toast offers Undo")
        XCTAssertEqual(undo.undoActionName, "Snooze", "Edit ▸ Undo Snooze")
        XCTAssertTrue(window.tryToPerform(commandZ, with: nil), "⌘Z")
        await waiting(for: "⌘Z undoes Snooze") { undos() == 1 }
        XCTAssertFalse(undo.canUndo)

        await done { await world.desk.attention.mute([MailCopy(Self.rows[1])]) }
        XCTAssertEqual(undo.undoActionName, "Mute", "Edit ▸ Undo Mute")
        world.mailbox.undo()  // z, or the toast's Undo
        await waiting(for: "z undoes Mute") { undos() == 2 }
        XCTAssertFalse(undo.canUndo, "z took Edit ▸ Undo's with it: ⌘Z has nothing more of it to take back")
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(undos(), 2, "Mute was undone once")

        await done { await world.desk.attention.snooze([MailCopy(Self.rows[2])], .preset(.tomorrow)) }
        XCTAssertTrue(window.tryToPerform(commandZ, with: nil))
        await waiting(for: "⌘Z undoes the second Snooze") { undos() == 3 }
        XCTAssertNil(world.mailbox.toast?.undo, "its toast no longer offers Undo")
    }

    /// Unsnooze in Snoozed, Unmute in Muted and Dismiss in Waiting take the message off the stack shown, so the list
    /// moves on as File does; and a message opened from outside the stack closes.
    func testUnsnoozeUnmuteAndDismissInTheirStacksMoveOnAsFileDoes() async throws {
        for stack in [VirtualFolder.snoozed, .muted, .waiting] {
            let server = Self.server()
            server.on("POST", API.Post.Unsnooze.path, API.Unsnoozed(count: 1, ok: true))
            server.on("POST", API.Post.Unmute.path, API.OnThread(ok: true, threadId: "t1"))
            server.on("POST", API.Post.FollowupsDismiss.path, API.OnThread(ok: true, threadId: "t1"))
            let world = await world(server)
            world.app.place = .virtual(stack, account: Fixture.ada)
            await waiting(for: "\(stack)'s rows") { world.app.list?.copy(at: 1) != nil }
            world.mailbox.chose([try XCTUnwrap(world.app.list?.copy(at: 0))])
            try await leave(stack, world)
            await waiting(for: "\(stack) moves on") { world.app.selection.copy == MailCopy(Self.rows[1]) }
            XCTAssertEqual(world.mailbox.marked, [MailCopy(Self.rows[1])], "\(stack): chosen in its row")

            // Opened from outside the stack's list (a sender's card), it closes to show the stack.
            world.mailbox.open(Self.away)
            try await leave(stack, world)
            await waiting(for: "\(stack) closes the message") { world.app.selection.copy == nil }
            XCTAssertEqual(world.app.place, .virtual(stack, account: Fixture.ada))
        }
    }

    /// The stack's verb that takes the message open off it, as the pane's bar and the snooze chooser run it.
    private func leave(_ stack: VirtualFolder, _ world: World) async throws {
        let copies = [try XCTUnwrap(world.app.selection.copy)]
        switch stack {
        case .snoozed:
            await world.desk.attention.unsnooze(copies)
        case .muted:
            let unmute = try XCTUnwrap(world.desk.paneCommands.first { $0.id == "mute" }, "the bar's Unmute")
            XCTAssertEqual(unmute.label, "Unmute thread")
            await world.desk.attention.unmute(copies)
        default:
            XCTAssertNotNil(world.desk.paneCommands.first { $0.id == "dismiss" }, "the bar's Dismiss")
            await world.desk.attention.dismissFollowup(copies)
        }
    }

    /// With rows marked and another message open beside them (as j, k or a click opens one), the pane's bar and Up
    /// next's buttons act on the message in the pane alone, as the page's reader does: the marks stay marked for the
    /// bulk bar, and the next message opens beside them. Undo opens it again beside them too. The list's keys still
    /// act on the marks.
    func testThePanesVerbsActOnTheMessageInThePaneNotTheMarks() async throws {
        enum Pane: String, CaseIterable { case file, delete, snooze, later }
        for pane in Pane.allCases {
            let world = await world()
            let app = world.app, mailbox = world.mailbox, desk = world.desk
            await inbox(app)
            let marks = [try XCTUnwrap(app.list?.copy(at: 0)), try XCTUnwrap(app.list?.copy(at: 4))]
            mailbox.chose(marks)
            let shown = try XCTUnwrap(app.list?.copy(at: 2))
            mailbox.openBeside(shown)
            XCTAssertTrue(mailbox.marking, "\(pane)")
            let copy = MailCopy(shown)
            // The bar's File and Delete, and Up next's File & next and Delete & next, which run the same.
            switch pane {
            case .file: mailbox.fileOpen(.archive)
            case .delete: mailbox.fileOpen(.trash)
            case .snooze:
                try XCTUnwrap(desk.paneCommands.first { $0.id == "snooze" }, "the bar's Snooze").run()
                guard case .snoozeUntil(let copies, _)? = desk.sheet else { return XCTFail("Snooze asks when") }
                XCTAssertEqual(copies, [copy], "Snooze asks for the message in the pane")
                await desk.attention.snooze(copies, .preset(.tomorrow))
            case .later:
                try XCTUnwrap(desk.paneCommands.first { $0.id == "later" }, "the bar's Later").run()
            }
            let path = switch pane {
            case .file: API.Post.Archive.path
            case .delete: API.Post.Trash.path
            case .snooze: API.Post.Snooze.path
            case .later: API.Post.Replylater.path
            }
            await waiting(for: "\(pane) asks") { !world.server.asked(path).isEmpty }
            let asked = world.server.asked.filter { Self.verbs.contains($0.path) }
            XCTAssertEqual(asked.count, 1, "\(pane): one request, for one message")
            let body = try JSONSerialization.jsonObject(with: try XCTUnwrap(asked.first).body) as? [String: Any]
            XCTAssertEqual(body?["key"] as? Int, copy.key, "\(pane) acts on the message in the pane")
            XCTAssertNil(body?["copies"], "\(pane) names no marks")
            await waiting(for: "\(pane) says so") { mailbox.toast != nil }
            XCTAssertEqual(mailbox.marked, marks.map(MailCopy.init), "\(pane): the marks stay marked")
            XCTAssertTrue(mailbox.marking, "\(pane): and are still the bulk bar's")
            guard pane != .later else {
                XCTAssertEqual(app.selection.copy, copy, "Later keeps it open")
                continue
            }
            await waiting(for: "\(pane) moves on beside the marks") { app.selection.copy == MailCopy(Self.rows[3]) }
            XCTAssertEqual(mailbox.marked, marks.map(MailCopy.init), "\(pane): moving on keeps the marks")
            guard pane != .snooze else { continue }
            await mailbox.undoNow()
            await waiting(for: "\(pane)'s Undo opens it again") { app.selection.copy == copy }
            XCTAssertEqual(mailbox.marked, marks.map(MailCopy.init), "\(pane): beside the marks, which stay")
        }

        // The list's keys still act on the marks, as one request for them all.
        let server = Self.server()
        server.on("POST", API.Post.BulkFile.path, API.RowsFiled(count: 2, failed: 0, filed: [], msg: "", ok: true,
                                                                partial: false, queued: 0, rowsFailed: 0,
                                                                undoable: true))
        let world = await world(server)
        await inbox(world.app)
        let marks = [try XCTUnwrap(world.app.list?.copy(at: 0)), try XCTUnwrap(world.app.list?.copy(at: 4))]
        world.mailbox.chose(marks)
        world.mailbox.openBeside(try XCTUnwrap(world.app.list?.copy(at: 2)))
        key("#", world.mailbox)
        await waiting(for: "# asks") { !server.asked(API.Post.BulkFile.path).isEmpty }
        let body = try JSONDecoder().decode(API.BulkFileBody.self,
                                            from: try XCTUnwrap(server.asked(API.Post.BulkFile.path).first).body)
        XCTAssertEqual(Set(body.rows?.map(\.key) ?? []), Set(marks.map(\.key)), "# deletes the marks")
        XCTAssertTrue(server.asked(API.Post.Trash.path).isEmpty, "and not the message in the pane")
    }

    /// The pane over a message no list holds, filed already or in Trash, takes it back to its account's Inbox.
    func testMoveToInboxAndRestoreTakeTheMessageOpenBack() async throws {
        for folder in ["Archive", "Trash"] {
            let world = await world()
            let copy = MailCopy(key: 91, folder: folder, uid: 91, account: Fixture.ada)
            world.app.place = .desk(.brief)
            world.desk.open(copy)
            // The bar's Move to Inbox and Restore.
            world.mailbox.restoreOpen()
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

    // MARK: The empty pane

    /// With several rows marked the pane says how many, as the bulk bar counts them, and what that bar does with
    /// them; in Trash, that it restores them. With none it asks for a message to read.
    func testTheEmptyPaneSaysHowManyAreMarkedAndWhatTheBulkBarDoes() async throws {
        let world = await world()
        await inbox(world.app)
        XCTAssertEqual(PaneEmptyWords.of(world.app).title, "Pick a message to read")
        let rows = try [0, 2, 3, 4].map { try XCTUnwrap(world.app.list?.copy(at: $0)) }
        world.mailbox.chose(rows)
        XCTAssertNil(world.app.selection.copy, "no message open")
        let words = PaneEmptyWords.of(world.app)
        XCTAssertEqual(words.title, "4 selected")
        XCTAssertEqual(words.line,
                       "File, delete or snooze them from the bar over the list, or press Esc to clear the selection.")
        XCTAssertFalse(words.everything)

        let trashed = Self.rows.map { row in
            var row = row
            row.folder = "Trash"
            return row
        }
        let trash = await self.world(Self.server(rows: trashed))
        trash.app.place = .folder(account: Fixture.ada, folder: "Trash")
        await waiting(for: "Trash's rows") { trash.app.list?.copy(at: 2) != nil }
        trash.mailbox.chose(try [0, 2].map { try XCTUnwrap(trash.app.list?.copy(at: $0)) })
        XCTAssertEqual(PaneEmptyWords.of(trash.app),
                       PaneEmptyWords(title: "2 selected", line: "Restore them from the bar over the list, or press Esc "
                                          + "to clear the selection.", everything: false))
    }

    /// Summaries landing fill the Inbox's Needs you after it was read empty: the empty pane, which said nothing needs
    /// you, is drawn again as the list fills and asks for a message to read, as the page's empty reader follows its
    /// list; and says nothing needs you again once the list empties.
    func testTheEmptyPaneFollowsNeedsYouAsItFillsAndEmpties() async throws {
        let needing = NeedsBox()
        let server = Self.server(), inbox = Self.rows
        server.on("GET", API.Get.Messages.path) { asked in
            let rows = inbox.map { row in
                var row = row
                row.needsYou = needing.on
                return row
            }
            let offset = Int(asked.query["offset"] ?? "0") ?? 0
            let items = Array(rows.dropFirst(offset))
            return FakeServer.ok(API.MessagesAnswer.folderListing(API.FolderListing(
                account: asked.query["account"], folder: asked.query["folder"] ?? "INBOX",
                grouped: asked.query["group"] != nil, hasMore: false, items: items, offset: offset, ok: true,
                total: rows.count)))
        }
        let world = await world(server)
        world.app.mailbox.tab = .needs
        world.app.place = .folder(account: Fixture.ada, folder: "INBOX")
        let list = try XCTUnwrap(world.app.list)
        await waiting(for: "the Inbox's first read") { list.firstRowsAt != nil }
        XCTAssertEqual(PaneEmptyWords.of(world.app).title, "Nothing needs you.")

        // As a view reads them: drawn again once the list changes.
        func redrawn(after change: () -> Void) async -> Bool {
            let told = NeedsBox()
            withObservationTracking { _ = PaneEmptyWords.of(world.app) } onChange: { told.on = true }
            change()
            await waiting(for: "the list read again") { told.on }
            return told.on
        }
        let filled = await redrawn {
            needing.on = true
            list.refresh()
        }
        XCTAssertTrue(filled, "the pane is drawn again as Needs you fills")
        XCTAssertEqual(PaneEmptyWords.of(world.app).title, "Pick a message to read")
        let emptied = await redrawn {
            needing.on = false
            list.refresh()
        }
        XCTAssertTrue(emptied)
        XCTAssertEqual(PaneEmptyWords.of(world.app).title, "Nothing needs you.")
    }

    /// The reply box shows R where r opens it: under the mail window's pane, whose list takes r, and in a message's
    /// own window, which takes the reader's single keys, while they are on.
    func testTheReplyBoxShowsROnlyWhereROpensIt() async {
        let world = await world()
        XCTAssertTrue(MessageReader.showsReplyKey(app: world.app, mailbox: world.mailbox), "the mail window's pane")
        XCTAssertTrue(MessageReader.showsReplyKey(app: nil, mailbox: world.mailbox), "a message's own window")
        let off = await self.world(Fixture.server(singleKeys: false))
        XCTAssertFalse(MessageReader.showsReplyKey(app: nil, mailbox: off.mailbox), "single keys off: no r")
    }

    // MARK: Undo

    /// The Inbox's rows, listed one at a time.
    static func listing(_ rows: [API.FolderRow]) -> FakeServer.Answer {
        { asked in
            FakeServer.ok(API.MessagesAnswer.folderListing(API.FolderListing(
                account: asked.query["account"], folder: asked.query["folder"] ?? "INBOX", grouped: false,
                hasMore: false, items: rows, offset: 0, ok: true, total: rows.count)))
        }
    }

    /// A move's Undo brings the copy back with a new uid: the message it moved the pane on from is found by its key
    /// in the folder it left, and chosen in its row.
    func testUndoOpensTheMessageBackByItsKeyInTheFolderItLeft() async throws {
        let server = Self.server()
        let world = await world(server)
        await inbox(world.app)
        world.mailbox.chose([try XCTUnwrap(world.app.list?.copy(at: 0))])
        await world.mailbox.fileNow(.archive)
        XCTAssertEqual(world.app.selection.copy, MailCopy(Self.rows[1]), "it moved on")
        var back = Self.rows
        back[0].uid = 101
        server.on("GET", API.Get.Messages.path, Self.listing(back))
        await world.mailbox.undoNow()
        let again = MailCopy(key: 1, folder: "INBOX", uid: 101, account: Fixture.ada)
        await waiting(for: "it opens again") { world.app.selection.copy == again }
        XCTAssertEqual(world.mailbox.marked, [again], "chosen in its row")
    }

    /// Undo opens nothing again when the last thing that can be undone didn't move the pane on, nor when a person
    /// chose another row before the list came back, nor after a verb that can't be undone.
    func testUndoOpensNothingAgainThatItDidntMoveThePaneOnFrom() async throws {
        let world = await world()
        await inbox(world.app)
        world.mailbox.chose([try XCTUnwrap(world.app.list?.copy(at: 0))])
        await world.mailbox.fileNow(.archive)
        // Another row filed, which wasn't open: its Undo is the one next.
        await world.mailbox.fileNow(.archive, rows: [try XCTUnwrap(world.app.list?.copy(at: 3))])
        await world.mailbox.undoNow()
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(world.app.selection.copy, MailCopy(Self.rows[1]), "the pane stays where it moved on to")

        world.mailbox.chose([try XCTUnwrap(world.app.list?.copy(at: 1))])
        await world.mailbox.fileNow(.trash)
        XCTAssertEqual(world.app.selection.copy, MailCopy(Self.rows[2]))
        await world.mailbox.undoNow()
        let chosen = try XCTUnwrap(world.app.list?.copy(at: 4))
        world.mailbox.chose([chosen])
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(world.app.selection.copy, MailCopy(chosen), "a row chosen since the Undo stays")

        let server = Self.server()
        server.on("POST", API.Post.Archive.path, API.Filed(msg: "ok", ok: true, undoable: false))
        let other = await self.world(server)
        await inbox(other.app)
        other.mailbox.chose([try XCTUnwrap(other.app.list?.copy(at: 0))])
        await other.mailbox.fileNow(.archive)
        XCTAssertNil(other.mailbox.toast?.undo, "no Undo offered")
        await other.mailbox.undoNow()
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(other.app.selection.copy, MailCopy(Self.rows[1]), "nothing it can't undo opens again")
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

    /// A message opened from outside the list reads as the server has it whether a verb comes before the reader has
    /// opened it or after: before, the verb waits for the reader's head, which names its conversation. Starred and
    /// unread there, s unstars it and the pane's dwell knows it unread. File names it by its copy alone, and never
    /// waits.
    func testAVerbBeforeOrAfterTheReaderOpensTheMessageReadsItAsItStands() async throws {
        for before in [true, false] {
            let said = before ? "before the reader opens it" : "after the reader opened it"
            let world = await world()
            world.app.place = .desk(.brief)
            world.desk.open(Self.away)
            let (body, comes) = AsyncStream<Void>.makeStream()
            world.app.reader.hold(Self.away, until: {
                for await _ in body {}
                return Self.head()
            })
            if !before { comes.finish() }
            key("s", world.mailbox)
            let unread = Task { await world.mailbox.opensUnread(Self.away) }
            if before {
                XCTAssertFalse(Marks(world.mailbox.shownRows).unstars, said + ": nothing is known of it yet")
                try await Task.sleep(for: .milliseconds(50))
                XCTAssertTrue(world.server.asked(API.Post.Flag.path).isEmpty, said + ": s waits for the reader")
                comes.finish()
            }
            await waiting(for: said + ": s stars or unstars") { !world.server.asked(API.Post.Flag.path).isEmpty }
            let flag = try JSONDecoder().decode(API.FlagBody.self,
                                                from: try XCTUnwrap(world.server.asked(API.Post.Flag.path).first).body)
            XCTAssertEqual(flag.on, false, said + ": starred at the server, s unstars it")
            let known = await unread.value
            XCTAssertTrue(known, said + ": the dwell marks it read")
        }

        // File, pressed while the reader is still opening it, asks at once.
        let world = await world()
        world.app.place = .desk(.brief)
        world.desk.open(Self.away)
        let (body, comes) = AsyncStream<Void>.makeStream()
        world.app.reader.hold(Self.away, until: {
            for await _ in body {}
            return Self.head()
        })
        world.mailbox.file(.archive)
        await waiting(for: "File asks without waiting") { !world.server.asked(API.Post.Archive.path).isEmpty }
        comes.finish()
    }

    /// The menus' words follow the server as soon as the reader has opened a message from outside the list, before
    /// any verb asks.
    func testTheMenusSayHowAMessageOpenedFromOutsideStandsOnceTheReaderOpensIt() async throws {
        let world = await world()
        world.app.place = .desk(.brief)
        world.desk.open(Self.away)
        world.app.reader.hold(Self.away, Self.head())
        await waiting(for: "the Message menu says Unstar") { Marks(world.mailbox.shownRows).unstars }
        XCTAssertTrue(Marks(world.mailbox.shownRows).unread, "and Mark as Read")
    }

    // MARK: Going elsewhere

    /// A server whose folders each hold mail of their own: the Inbox its rows, Sent and Drafts one message each, and
    /// Snoozed none.
    static func ownFolders() -> FakeServer {
        let server = Self.server()
        let inbox = Self.rows
        let sent = [Fixture.row(60, folder: "Sent", subject: "Sent on")]
        let drafts = [Fixture.row(70, folder: "Drafts", subject: "Not yet")]
        server.on("GET", API.Get.Messages.path) { asked in
            let items: [API.FolderRow] = switch asked.query["folder"] {
            case "INBOX": inbox
            case "Sent": sent
            case "Drafts": drafts
            default: []
            }
            return FakeServer.ok(API.MessagesAnswer.folderListing(API.FolderListing(
                account: asked.query["account"], folder: asked.query["folder"] ?? "INBOX", grouped: false,
                hasMore: false, items: items, offset: 0, ok: true, total: items.count)))
        }
        return server
    }

    /// Going to a place whose list doesn't hold the message open closes it once the list has come, so File and Delete
    /// have nothing to act on: Sent doesn't show the Inbox's message, an empty Snoozed shows none, the Inbox doesn't
    /// keep a draft, a search or the Feed doesn't keep mail it didn't find, and the Document Hub holds no mail.
    func testGoingToAPlaceWhoseListDoesntHoldTheMessageOpenClosesIt() async throws {
        let inbox = Place.folder(account: Fixture.ada, folder: "INBOX")
        let drafts = Place.folder(account: Fixture.ada, folder: "Drafts")
        let goings: [(from: Place, to: Place)] = [
            (inbox, .folder(account: Fixture.ada, folder: "Sent")), (inbox, .virtual(.snoozed, account: Fixture.ada)),
            (drafts, inbox), (inbox, .search(Search(q: "paid", account: Fixture.ada))),
            (inbox, .search(Search(q: "category:newsletter", lane: .feed))), (inbox, .documents),
        ]
        for (from, to) in goings {
            let said = "\(from) → \(to)"
            let world = await world(Self.ownFolders())
            world.app.place = from
            await waiting(for: said + ": the rows") { world.app.list?.copy(at: 0) != nil }
            world.mailbox.chose([try XCTUnwrap(world.app.list?.copy(at: 0))])
            XCTAssertNotNil(world.app.selection.copy, said)
            world.app.place = to
            await waiting(for: said + " closes the message") { world.app.selection.copy == nil }
            XCTAssertTrue(world.mailbox.chosenRows.isEmpty, said)
            XCTAssertFalse(world.mailbox.hasTarget, said + ": File and Delete have nothing to act on")
            XCTAssertEqual(world.app.place, to, said)
        }
    }

    /// A list that holds the message open keeps it open, and so does an answer, which stands in the list's place
    /// with the pane reading beside it. A message opened as the window goes to its folder (a notification's) stays,
    /// whether that folder's list holds it or not.
    func testGoingToAPlaceWhoseListHoldsTheMessageOpenKeepsIt() async throws {
        for to in [Place.allInboxes, .desk(.ask)] {
            let world = await world(Self.ownFolders())
            await inbox(world.app)
            let row = try XCTUnwrap(world.app.list?.copy(at: 0))
            world.mailbox.chose([row])
            world.app.place = to
            if to == .allInboxes {
                await waiting(for: "every Inbox's rows") { world.app.list?.copy(at: 0) != nil }
            }
            try await Task.sleep(for: .milliseconds(20))
            XCTAssertEqual(world.app.selection.copy, MailCopy(row), "\(to) keeps it open")
        }

        let world = await world(Self.ownFolders())
        world.app.place = .folder(account: Fixture.ada, folder: "Sent")
        await waiting(for: "Sent's rows") { world.app.list?.copy(at: 0) != nil }
        world.mailbox.chose([try XCTUnwrap(world.app.list?.copy(at: 0))])
        world.mailbox.openInItsFolder(Self.away)
        await waiting(for: "the Inbox's rows") { world.app.list?.copy(at: 0) != nil }
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertEqual(world.app.selection.copy, Self.away, "a notification's message stays open in its folder")
    }

    /// A message's own window closes once its message is sent away from any window: the mail window's Message menu,
    /// its keys, the pane's bar, a snooze. Undo opens it again in a window of its own, found by its key in the folder
    /// it left, as the mail window's Undo opens its pane's message again. A verb on another message keeps it open.
    func testAMessagesWindowClosesWhenItsMessageIsSentAwayFromAnyWindowAndUndoOpensIt() async throws {
        enum From: String, CaseIterable { case menu, key, pane, snooze, ownMenu }
        for from in From.allCases {
            let server = Self.server()
            let world = await world(server)
            let mailbox = world.mailbox
            await inbox(world.app)
            let row = try XCTUnwrap(world.app.list?.copy(at: 2))
            let copy = MailCopy(row)
            // A double-click: the row chosen, and its message in a window of its own.
            mailbox.chose([row])
            let window = await window(on: copy, head: Self.head(copy, thread: nil), in: world)
            var opened: [MailCopy] = []
            mailbox.opensWindow = { opened.append($0) }
            mailbox.file(.archive, rows: [try XCTUnwrap(world.app.list?.copy(at: 4))])
            await waiting(for: "\(from): another message filed") { !server.asked(API.Post.Archive.path).isEmpty }
            try await Task.sleep(for: .milliseconds(20))
            XCTAssertFalse(window.left, "\(from): a verb on another message keeps the window open")

            switch from {
            case .menu: mailbox.file(.trash)
            case .key: key("#", mailbox)
            case .pane: mailbox.fileOpen(.trash)
            case .snooze: await world.desk.attention.snooze([copy], .preset(.tomorrow))
            case .ownMenu: MessageMenuVerbs(window: window, mailbox: mailbox).file(.trash)
            }
            await waiting(for: "\(from) closes the window") { window.left }
            await waiting(for: "\(from) offers Undo") { mailbox.toast?.undo != nil }
            // Back with a new uid: found by its key in the folder it left.
            var back = Self.rows
            back[2].uid = 103
            server.on("GET", API.Get.Messages.path, Self.listing(back))
            await mailbox.undoNow()
            await waiting(for: "\(from)'s Undo opens the window again") { !opened.isEmpty }
            XCTAssertEqual(opened, [MailCopy(key: copy.key, folder: "INBOX", uid: 103, account: copy.account)],
                           "\(from)")
        }

        // Nothing undoable closed it: the next Undo opens no window.
        let world = await world()
        await inbox(world.app)
        let copy = MailCopy(try XCTUnwrap(world.app.list?.copy(at: 2)))
        let window = await window(on: copy, head: Self.head(copy, thread: nil), in: world)
        var opened: [MailCopy] = []
        world.mailbox.opensWindow = { opened.append($0) }
        await window.mailbox.fileNow(.trash, rows: window.shown)
        XCTAssertTrue(window.left)
        await world.mailbox.fileNow(.archive, rows: [try XCTUnwrap(world.app.list?.copy(at: 4))])
        await world.mailbox.undoNow()
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(opened, [], "the Undo is the later verb's, which closed no window")
    }

    /// At the gate, "Read it whole" and then Delete in the pane: the gate reads its queue again, so the card goes with
    /// its sender's last message; with mail of theirs left, the card reads their next message in its place. A message
    /// the gate doesn't show reads nothing again.
    func testTheGatesCardGoesWithItsMessageWhenItIsSentAway() async throws {
        for left in [false, true] {
            let server = Self.server()
            let shop = API.ScreenerItem(count: left ? 2 : 1, domain: "example.test", fromAddr: "shop@example.test",
                                        fromName: "Shop", when: "Nov 2")
            let other = API.ScreenerItem(count: 1, domain: "example.test", fromAddr: "cy@example.test",
                                         fromName: "Cy Young", when: "Mon")
            // Once the letter has gone: the sender is gone from the gate, or their other message is their latest.
            server.on("GET", API.Get.Screener.path) { [server] _ in
                let gone = !server.asked(API.Post.Trash.path).isEmpty
                let items = gone && !left ? [other] : [shop, other]
                return FakeServer.ok(try! JSONEncoder().encode(API.ScreenerQueue(account: Fixture.ada, items: items,
                                                                                 ok: true, total: items.count)))
            }
            let hits = Self.hits
            server.on("GET", API.Get.Search.path) { [server] _ in
                let gone = !server.asked(API.Post.Trash.path).isEmpty
                return FakeServer.ok(try! JSONEncoder().encode(API.SearchAnswer.searchPage(API.SearchPage(
                    generated: "", hasMore: false, items: [gone ? hits[1] : hits[0]], offset: 0, ok: true,
                    query: "", scope: "account"))))
            }
            let world = await world(server)
            let gate = world.desk.screener(for: Fixture.ada)
            world.app.place = .desk(.screener(account: Fixture.ada))
            await gate.load()
            await gate.readLetter("shop@example.test")
            XCTAssertEqual(gate.visitor?.fromAddr, "shop@example.test")
            let letter = try XCTUnwrap(gate.letters["shop@example.test"] ?? nil)
            await world.mailbox.fileNow(.archive, rows: [API.FolderRow(Fixture.hit(77, subject: "Elsewhere"))])
            try await Task.sleep(for: .milliseconds(20))
            XCTAssertEqual(server.asked(API.Get.Search.path).count, 1, "a message the gate doesn't show reads nothing")

            // Read it whole, then the pane's Delete.
            world.desk.open(.named(key: letter.key, folder: letter.folder, uid: letter.uid, account: letter.account))
            await world.mailbox.fileNow(.trash, rows: await world.mailbox.paneTargets(standing: false))
            if left {
                await waiting(for: "the card reads the sender's next message") {
                    (gate.letters["shop@example.test"] ?? nil)?.key == Self.hits[1].key
                }
                XCTAssertEqual(gate.visitor?.fromAddr, "shop@example.test")
            } else {
                await waiting(for: "the card goes with the sender") { gate.visitor?.fromAddr == "cy@example.test" }
                XCTAssertEqual(gate.rows.map(\.fromAddr), ["cy@example.test"])
            }
        }
    }

    /// A notification's message is chosen in its row once its folder's rows come, as a click on it would choose it,
    /// so the list shows which it is. One the list doesn't hold stays open with no row chosen, and a message opened
    /// before the rows come stays open.
    func testANotificationsMessageIsChosenInItsRowOnceTheListHoldsIt() async throws {
        let world = await world()
        let copy = MailCopy(Self.rows[2])
        world.mailbox.openInItsFolder(copy)
        XCTAssertEqual(world.app.selection.copy, copy, "it opens at once")
        await waiting(for: "its row chosen") { world.mailbox.marked == [copy] }
        XCTAssertEqual(world.app.selection.copy, copy)
        XCTAssertFalse(world.mailbox.marking, "chosen, not marked")

        world.mailbox.openInItsFolder(Self.away)
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertEqual(world.app.selection.copy, Self.away)
        XCTAssertEqual(world.mailbox.marked, [], "no row is chosen for a message the list doesn't hold")

        let other = await self.world()
        other.mailbox.openInItsFolder(copy)
        let first = MailCopy(Self.rows[0])
        other.mailbox.open(first)
        await waiting(for: "the Inbox's rows") { other.app.list?.copy(at: 0) != nil }
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertEqual(other.app.selection.copy, first, "a message opened since stays open")
        XCTAssertNotEqual(other.mailbox.marked, [copy])
    }

    // MARK: The reader's row

    /// A message opened from outside the list reads by the row the server has for it: its folder's listing says
    /// when it came, whether it needs you and its summary, and its conversation how it stands now, read and starred.
    func testAMessageOpenedFromOutsideTheListShowsItsTimeChipsAndSummary() async throws {
        let filed = MailCopy(key: 95, folder: "Archive", uid: 95, account: Fixture.ada)
        let listed = Fixture.row(95, folder: "Archive", seen: true, needs: true, action: "Pay by Friday",
                                 summary: "The invoice for October.", subject: "Invoice", when: "2h ago")
        let server = Self.server()
        let inbox = Self.rows
        server.on("GET", API.Get.Messages.path) { asked in
            let items = asked.query["folder"] == "Archive" ? [listed] : inbox
            return FakeServer.ok(API.MessagesAnswer.folderListing(API.FolderListing(
                account: asked.query["account"], folder: asked.query["folder"] ?? "INBOX", grouped: false,
                hasMore: false, items: items, offset: 0, ok: true, total: items.count)))
        }
        var member = Self.awayMember
        (member.key, member.uid, member.folder) = (95, 95, "Archive")
        server.on("GET", API.Get.Thread.path, API.Thread(account: Fixture.ada, count: 1, items: [member], ok: true,
                                                         replyKey: 95, threadId: "t95"))
        let world = await world(server)
        world.app.place = .desk(.brief)
        world.mailbox.open(filed)
        world.app.reader.hold(filed, Self.head(filed, thread: "t95"))
        await waiting(for: "its listing") { world.mailbox.shownRow(of: filed)?.summary.isEmpty == false }
        let row = try XCTUnwrap(world.mailbox.shownRow(of: filed))
        XCTAssertEqual(row.summary, "The invoice for October.")
        XCTAssertEqual(row.action, "Pay by Friday")
        XCTAssertTrue(row.needsYou, "the Needs you chip")
        XCTAssertEqual(row.when, "2h ago", "when it came, as the list says it")
        XCTAssertFalse(row.seen, "unread, as its conversation says now")
        XCTAssertTrue(row.flagged, "and starred")
        XCTAssertFalse(row.isConversation(grouped: true), "it stands for itself")

        // Read again after mail changed, it keeps what its listing said, its marks as the server has them now.
        member.seen = true
        server.on("GET", API.Get.Thread.path, API.Thread(account: Fixture.ada, count: 1, items: [member], ok: true,
                                                         replyKey: 95, threadId: "t95"))
        world.mailbox.rereadOutside()
        await waiting(for: "read again") { world.mailbox.shownRow(of: filed)?.seen == true }
        XCTAssertEqual(world.mailbox.shownRow(of: filed)?.summary, "The invoice for October.")
        XCTAssertEqual(server.asked(API.Get.MessageRow.path).filter { $0.query["folder"] == "Archive" }.count, 1,
                       "its row is read once as it opens")
    }

    /// The document names the account and the folder as the mail window's rail does, in a message's own window
    /// too: "Ada" and "In Archive", not the address's "ada" and a folder of the person's own.
    func testAMessagesOwnWindowNamesItsAccountAndFolderAsTheMailWindowDoes() async throws {
        let world = await world()
        let filed = MailCopy(key: 95, folder: "Archive", uid: 95, account: Fixture.ada)
        let place = DocumentPlace.of(app: nil, mailbox: world.mailbox, copy: filed, needsYou: false, replied: false)
        XCTAssertEqual(place.me, "Ada")
        XCTAssertEqual(place.chips.map(\.text), ["Ada", "In Archive"])
        XCTAssertEqual(DocumentPlace.of(app: world.app, copy: filed, needsYou: false, replied: false).chips.map(\.text),
                       ["Ada", "In Archive"], "as the mail window's pane says them")
    }

    /// Where no row says when a message came, its header's date is said as the list says a time.
    func testAHeadersDateIsSaidAsTheListSaysATime() throws {
        let header = "Sat, 14 Nov 2026 10:24:01 +0000"
        let sent = try XCTUnwrap(MessageWhen.date(header))
        let day: TimeInterval = 86_400
        let times: [(after: TimeInterval, said: String)] = [
            (0, "just now"), (2.5 * 3600, "2h ago"), (26 * 3600, "yesterday"), (3 * day, "3d ago"), (10 * day, "1w ago"),
            (40 * day, "Nov 14"),
        ]
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = TimeZone(identifier: "UTC")!
        for (after, said) in times {
            XCTAssertEqual(MessageWhen.said(header, now: sent.addingTimeInterval(after), calendar: utc), said, "\(after)")
        }
        XCTAssertEqual(MessageWhen.said("2 Nov 2026 09:00 +0100 (CET)", now: sent.addingTimeInterval(86_400 * 60)),
                       "Nov 02", "no weekday, no seconds, a comment after the zone")
        XCTAssertNotNil(MessageWhen.date("Mon, 2 Nov 2026 09:00:00 GMT"), "a zone by its name")
        XCTAssertEqual(MessageWhen.said("sometime"), "sometime", "a date that won't read is said as written")
    }

    /// The words count the person's calendar days, as the day heads over the list's rows do and the core's words for a
    /// row do: twenty minutes either side of midnight is yesterday's where midnight fell between, and twenty minutes
    /// ago where it didn't.
    func testAHeadersDateCountsThePersonsCalendarDaysAsTheDayHeadsDo() throws {
        let header = "Thu, 19 Nov 2026 23:50:00 +0000"
        let sent = try XCTUnwrap(MessageWhen.date(header))
        func calendar(_ zone: String) -> Calendar {
            var calendar = Calendar(identifier: .gregorian)
            calendar.timeZone = TimeZone(identifier: zone)!
            return calendar
        }
        let later = sent.addingTimeInterval(20 * 60)
        XCTAssertEqual(MessageWhen.said(header, now: later, calendar: calendar("UTC")), "yesterday", "midnight between")
        XCTAssertEqual(MessageWhen.said(header, now: later, calendar: calendar("Europe/London")), "yesterday")
        XCTAssertEqual(MessageWhen.said(header, now: later, calendar: calendar("Europe/Paris")), "20m ago",
                       "both just past midnight there")
        XCTAssertEqual(MessageWhen.said(header, now: later, calendar: calendar("Pacific/Auckland")), "20m ago",
                       "both in the afternoon of the 20th there")
        XCTAssertEqual(MessageWhen.said(header, now: later, calendar: calendar("America/Los_Angeles")), "20m ago",
                       "both in the afternoon of the 19th there")
        XCTAssertEqual(MessageWhen.said(header, now: sent.addingTimeInterval(60), calendar: calendar("UTC")), "just now")
        XCTAssertEqual(MessageWhen.said(header, now: sent.addingTimeInterval(100), calendar: calendar("Asia/Kolkata")),
                       "1m ago", "past a minute and a half")
        XCTAssertEqual(MessageWhen.said(header, now: sent.addingTimeInterval(2 * 86_400 + 600), calendar: calendar("UTC")),
                       "3d ago", "two days and ten minutes, three calendar days")
        XCTAssertEqual(MessageWhen.said("Sat, 21 Nov 2026 09:00:00 +0000", now: sent, calendar: calendar("UTC")),
                       "Nov 21", "a later day is its date")
    }

    // MARK: Read for its moment

    /// After its moment on an unread message the reader marks it read (`ReaderPane.dwell`, a view's task, asks this),
    /// and the window says so at once, before the list is read again: its row is read in the list, and the Message
    /// menu offers Mark as Unread. Then the list and the counts are read again, as after a verb's mark.
    func testAMessageMarkedReadForItsMomentShowsReadAtOnce() async throws {
        var unread = Self.rows
        unread[0].seen = false
        let server = Self.server(rows: unread)
        let world = await world(server)
        await inbox(world.app)
        let row = try XCTUnwrap(world.app.list?.copy(at: 0))
        world.mailbox.chose([row])
        XCTAssertTrue(Marks(world.mailbox.shownRows).unread, "the Message menu offers Mark as Read")
        let lists = server.asked(API.Get.Messages.path).count, counts = server.asked(API.Get.Folders.path).count

        await world.mailbox.markOpenedRead(MailCopy(row), profile: "full")
        XCTAssertEqual(world.app.list?.copy(at: 0)?.seen, true, "its row is read in the list at once")
        XCTAssertFalse(Marks(world.mailbox.shownRows).unread, "the Message menu offers Mark as Unread")
        let marked = try XCTUnwrap(server.asked(API.Post.Seen.path).first).body
        XCTAssertEqual(try JSONDecoder().decode(API.SeenBody.self, from: marked),
                       API.SeenBody(folder: "INBOX", key: 1, seen: true, uid: 1))
        await waiting(for: "the list and the counts read again") {
            server.asked(API.Get.Messages.path).count > lists && server.asked(API.Get.Folders.path).count > counts
        }
    }

    /// Read only, or before the digest has said how MailMind runs, opening marks nothing, and the row stays unread.
    func testAMessageReadForItsMomentStaysUnreadWhereOpeningMarksNothing() async throws {
        var unread = Self.rows
        unread[0].seen = false
        for profile in [nil, "read-only"] as [String?] {
            let server = Self.server(rows: unread)
            let world = await world(server)
            await inbox(world.app)
            let row = try XCTUnwrap(world.app.list?.copy(at: 0))
            world.mailbox.chose([row])
            await world.mailbox.markOpenedRead(MailCopy(row), profile: profile)
            XCTAssertTrue(server.asked(API.Post.Seen.path).isEmpty, profile ?? "not known yet")
            XCTAssertEqual(world.app.list?.copy(at: 0)?.seen, false, profile ?? "not known yet")
        }
    }

    /// A message opened from outside the list reads as read at once too, and a conversation's member marked as it
    /// opens reads the list and the counts again, the conversation's row being the server's to say.
    func testAMessageOpenedFromOutsideOrAConversationsMemberShowsReadOnceMarked() async throws {
        let world = await world()
        _ = try await open(.briefCanWait, in: world)
        XCTAssertTrue(Marks(world.mailbox.shownRows).unread)
        await world.mailbox.markOpenedRead(Self.away, profile: "full")
        XCTAssertFalse(Marks(world.mailbox.shownRows).unread, "the Message menu offers Mark as Unread at once")

        let server = Self.server()
        let other = await self.world(server)
        await inbox(other.app)
        let row = try XCTUnwrap(other.app.list?.copy(at: 1))
        other.mailbox.chose([row])
        let lists = server.asked(API.Get.Messages.path).count
        let conversation = ConversationModel(routes: server.wire, account: Fixture.work, id: "t2",
                                             profile: { "full" }, marked: { other.mailbox.markedRead($0) })
        await conversation.markRead(API.ThreadMessage(answered: false, flagged: false, folder: "INBOX",
                                                      hasAttach: false, isJunk: false, key: 2, seen: false,
                                                      sentTo: [], sentToMore: 0,
                                                      snippet: "", subject: "Re: The engine's notes", uid: 2,
                                                      when: ""))
        XCTAssertFalse(server.asked(API.Post.Seen.path).isEmpty)
        await waiting(for: "the list read again") { server.asked(API.Get.Messages.path).count > lists }
    }

    /// A message whose head names no conversation is read as its folder lists it: unread and starred there, so s
    /// unstars it and the dwell marks it read. One its folder doesn't list either reads as read and not starred.
    func testAMessageWithNoConversationIsReadAsItsFolderListsIt() async throws {
        let receipt = MailCopy(key: 93, folder: "Receipts", uid: 93, account: Fixture.ada)
        let server = Self.server()
        let inbox = Self.rows, listed = Fixture.row(93, folder: "Receipts", seen: false, flagged: true, count: 2)
        server.on("GET", API.Get.Messages.path) { asked in
            let items = asked.query["folder"] == "Receipts" ? inbox + [listed] : inbox
            return FakeServer.ok(API.MessagesAnswer.folderListing(API.FolderListing(
                account: asked.query["account"], folder: asked.query["folder"] ?? "INBOX", grouped: false,
                hasMore: false, items: items, offset: 0, ok: true, total: items.count)))
        }
        let world = await world(server)
        world.app.place = .documents
        world.mailbox.open(receipt)
        world.app.reader.hold(receipt, Self.head(receipt, thread: nil))
        let row = await world.mailbox.openRow(receipt)
        XCTAssertTrue(row.flagged)
        XCTAssertFalse(row.seen)
        XCTAssertFalse(row.isConversation(grouped: true), "it stands for itself, as the pane shows it")
        let unread = await world.mailbox.opensUnread(receipt)
        XCTAssertTrue(unread, "the dwell marks it read")
        let asked = try XCTUnwrap(server.asked(API.Get.MessageRow.path).last)
        XCTAssertEqual([asked.query["account"], asked.query["folder"], asked.query["uid"]],
                       [Fixture.ada, "Receipts", "93"], "its row, by its folder and UID")
        await world.mailbox.starNow()
        let flag = try JSONDecoder().decode(API.FlagBody.self,
                                            from: try XCTUnwrap(server.asked(API.Post.Flag.path).first).body)
        XCTAssertEqual([flag.key, flag.uid], [93, 93])
        XCTAssertEqual(flag.on, false, "starred in its folder, s unstars it")

        let gone = MailCopy(key: 94, folder: "Receipts", uid: 94, account: Fixture.ada)
        let alone = await world.mailbox.standing(of: gone, head: Self.head(gone, thread: nil))
        XCTAssertTrue(alone.seen)
        XCTAssertFalse(alone.flagged)
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

/// A flag the fake server reads on its own task while the test sets it, and that a change seen sets.
private final class NeedsBox: @unchecked Sendable {
    private let lock = NSLock()
    private var held = false

    var on: Bool {
        get { lock.withLock { held } }
        set { lock.withLock { held = newValue } }
    }
}
