import AppKit
import SwiftUI
import WebKit
import XCTest

/// The list's keys as they come, real `NSEvent`s through the table's own `keyDown`, and what they do to the list,
/// the reader beside it and the window's keyboard.
@MainActor
final class ListKeysTests: XCTestCase {
    private var routes = DeskRoutes()
    /// The window's desk, kept for the test: the mailbox holds its hooks weakly.
    private var desk: Desk?
    /// The app, kept for the test: the table holds its mailbox weakly, and a test that names only the table would
    /// otherwise lose it, and the table's keys with it.
    private var app: MailApp?

    override func setUp() async throws {
        routes = DeskRoutes()
    }

    override func tearDown() async throws {
        desk = nil
        app = nil
    }

    /// The Inbox's first six rows in the list's table, the desk joined to its window.
    private func table(brief: Bool = false, server: FakeServer = Fixture.server(rows: Array(Fixture.inbox.prefix(6))))
        async throws -> (MailApp, MailboxTableController)
    {
        let app = MailApp()
        self.app = app
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        let desk = Desk()
        desk.use(app, routes: routes)
        self.desk = desk
        app.openList()
        let list = try XCTUnwrap(app.list)
        await until { list.copy(at: list.total - 1) != nil }
        let look = RowLook(inks: RowInks.of(.day, light: .day, highContrast: false), measure: RowMeasure(.comfortable),
                           accounts: app.mailbox.accounts)
        let controller = MailboxTableController(list: list, mailbox: app.mailbox, look: look, sections: true)
        controller.setBrief(brief ? BriefLine(needs: [], waiting: 0, canWait: 0) : nil, chosen: false)
        controller.scrollView.frame = NSRect(x: 0, y: 0, width: 420, height: 800)
        controller.table.reloadData()
        return (app, controller)
    }

    /// A window as wide as the list and a reader beside it.
    private func window() -> NSWindow {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 860, height: 800), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        return window
    }

    /// A key typed in the list, as AppKit hands it over: its characters, its key code, what was held.
    private func press(_ typed: String, _ table: NSView, code: UInt16 = 0, held: NSEvent.ModifierFlags = []) {
        let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: held, timestamp: 0,
                                     windowNumber: table.window?.windowNumber ?? 0, context: nil, characters: typed,
                                     charactersIgnoringModifiers: typed, isARepeat: false, keyCode: code)
        table.keyDown(with: try! XCTUnwrap(event))
    }

    // MARK: Keys the list doesn't use

    func testAKeyTheListDoesntUseKeepsThePlaceAndOpensNoBrief() async throws {
        let (app, controller) = try await table(brief: true)
        let table = controller.table
        XCTAssertFalse(table.allowsTypeSelect, "no row is chosen by the letter its sender starts with")
        table.selectRowIndexes(IndexSet(integer: 3), byExtendingSelection: false)
        let open = try XCTUnwrap(app.selection.copy)
        let place = app.place
        for (typed, held) in [("7", []), ("y", []), ("q", []), ("Q", .shift), (" ", []), ("~", .shift)]
            as [(String, NSEvent.ModifierFlags)]
        {
            press(typed, table, held: held)
            XCTAssertEqual(table.selectedRowIndexes, IndexSet(integer: 3), "\(typed) keeps the row chosen")
            XCTAssertEqual(app.selection.copy, open, "\(typed) keeps the message open")
            XCTAssertEqual(app.place, place, "\(typed) opens no brief")
        }
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(routes.sent.filter { $0.method == "POST" }.isEmpty, "nothing is written by AI on its own")
    }

    func testTheListsOwnKeysStillGoWhereTheyDid() async throws {
        let (app, controller) = try await table()
        let table = controller.table
        table.selectRowIndexes(IndexSet(integer: 1), byExtendingSelection: false)
        press("j", table)
        XCTAssertEqual(app.selection.copy?.key, 3, "j goes on to the next row")
        var focused = 0
        app.mailbox.keys["\r"] = { focused += 1 }
        press("\r", table, code: 36)
        XCTAssertEqual(focused, 1, "Return is the shell's, for focus reading")
        XCTAssertTrue(MailboxTableView.tablesOwn(NSDownArrowFunctionKey), "the arrows are the table's")
        XCTAssertTrue(MailboxTableView.tablesOwn(0x09), "and Tab, which leaves the list")
        XCTAssertFalse(MailboxTableView.tablesOwn(0x0D))
        XCTAssertFalse(MailboxTableView.tablesOwn(Int(("x" as Character).asciiValue!)))
    }

    /// Under a layout whose letters aren't Latin (Arabic, its characters on the US key codes, as macOS hands them
    /// over), each key does what it does under ABC: ت is j, ي is d, ث is e, and ل then س goes to Sent. With ⌘ held it
    /// is the menus', and a field gets the Arabic typed in it, from the list's table or from the window behind it.
    func testUnderANonLatinLayoutEachKeyIsTheOneItStandsOn() async throws {
        let close = try KeyLayouts.arabic()
        defer { close() }
        let server = Fixture.server(rows: Array(Fixture.inbox.prefix(6)))
        let (app, controller) = try await table(server: server)
        let table = controller.table
        // A message or its conversation, as the list shows them.
        func trashed() -> Int { server.asked(API.Post.Trash.path).count + server.asked(API.Post.ThreadTrash.path).count }
        func archived() -> Int {
            server.asked(API.Post.Archive.path).count + server.asked(API.Post.ThreadArchive.path).count
        }
        table.selectRowIndexes(IndexSet(integer: 1), byExtendingSelection: false)
        press("ت", table, code: 38)
        XCTAssertEqual(app.selection.copy?.key, 3, "ت is j: on to the next row")
        press("ن", table, code: 40)
        XCTAssertEqual(app.selection.copy?.key, 2, "ن is k: back up")
        press("ي", table, code: 2, held: .command)
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(trashed(), 0, "⌘ي is the menus', never d")
        press("ي", table, code: 2)
        await until { trashed() == 1 }
        press("ث", table, code: 14)
        await until { archived() == 1 }
        press("ل", table, code: 5)
        press("س", table, code: 1)
        XCTAssertEqual(app.place, .folder(account: Fixture.ada, folder: "Sent"), "ل then س is g s")

        // A field keeps the Arabic typed in it, and nothing is filed.
        let window = window()
        let host = NSHostingView(rootView: Color.clear.frame(width: 860, height: 800))
        window.contentView = host
        host.addSubview(controller.scrollView)
        UntakenKeys.Catcher.hang(on: window, for: app.mailbox)
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 100, height: 22))
        host.addSubview(field)
        XCTAssertTrue(window.makeFirstResponder(field))
        window.sendEvent(try KeyLayouts.event("ث", 14, in: window))
        window.sendEvent(try KeyLayouts.event("ي", 2, in: window))
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(field.currentEditor()?.string, "ثي", "the field gets what was typed")
        XCTAssertEqual(archived(), 1, "and nothing is filed")
        XCTAssertEqual(trashed(), 1)

        // From the window behind the list, a key nothing took goes on to the list as the key it stands on.
        XCTAssertTrue(window.makeFirstResponder(host))
        let before = app.selection.copy
        window.sendEvent(try KeyLayouts.event("ت", 38, in: window))
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertNotEqual(app.selection.copy, before, "ت moves the list, as j does")
        XCTAssertTrue(window.firstResponder === table, "and the list has the keyboard")
        window.contentView = nil
    }

    func testWhileADialogIsUpTheListsKeysWait() async throws {
        let (app, controller) = try await table()
        let table = controller.table
        table.selectRowIndexes(IndexSet(integer: 1), byExtendingSelection: false)
        let open = app.selection.copy
        app.mailbox.dialogUp = true
        press("j", table)
        press("e", table)
        XCTAssertEqual(app.selection.copy, open, "j doesn't move the list behind the scrim")
        XCTAssertNil(app.mailbox.toast, "and e files nothing")
    }

    // MARK: Space

    private final class Flipped: NSView {
        override var isFlipped: Bool { true }
    }

    /// The reader beside the list: a scroll view 300 points high over a message 2000 points long.
    @discardableResult
    private func reader(in window: NSWindow) -> NSScrollView {
        let scroll = NSScrollView(frame: NSRect(x: 440, y: 0, width: 400, height: 300))
        let document = Flipped(frame: NSRect(x: 0, y: 0, width: 400, height: 2000))
        document.addSubview(FrameWebView(frame: NSRect(x: 0, y: 0, width: 400, height: 2000),
                                         configuration: WKWebViewConfiguration()))
        scroll.documentView = document
        window.contentView?.addSubview(scroll)
        return scroll
    }

    func testSpacePagesTheMessageOpenBesideTheList() async throws {
        let (_, controller) = try await table()
        let window = window()
        window.contentView?.addSubview(controller.scrollView)
        let scroll = reader(in: window)
        let table = controller.table
        table.selectRowIndexes(IndexSet(integer: 1), byExtendingSelection: false)
        press(" ", table, code: 49)
        XCTAssertEqual(scroll.contentView.bounds.minY, 260, "a page down, less a little kept in sight")
        XCTAssertEqual(table.selectedRowIndexes, IndexSet(integer: 1), "and the list keeps its place")
        press(" ", table, code: 49, held: .shift)
        XCTAssertEqual(scroll.contentView.bounds.minY, 0, "⇧Space pages back up")
        press(" ", table, code: 49, held: .shift)
        XCTAssertEqual(scroll.contentView.bounds.minY, 0, "and goes no higher than the top")
        window.contentView = nil
    }

    // MARK: The keyboard as the list comes

    /// A window's content as SwiftUI's is: it takes the keyboard for a button clicked in it.
    private final class Host: NSView {
        override var acceptsFirstResponder: Bool { true }
    }

    func testAListComingIntoTheWindowTakesTheKeyboard() async throws {
        let (_, controller) = try await table()
        let window = window()
        window.contentView = Host()
        window.contentView?.addSubview(controller.scrollView)
        let table = controller.table
        XCTAssertTrue(window.initialFirstResponder === table, "the list, not the search, as the window opens")
        await until { window.firstResponder === table }

        // A tab clicked: SwiftUI holds the keyboard, and the list that comes in its place takes it.
        XCTAssertTrue(window.makeFirstResponder(window.contentView))
        table.takeKeyboardIfNowhere()
        XCTAssertTrue(window.firstResponder === window.contentView, "only a list just placed takes it from SwiftUI")
        table.takeKeyboardIfNowhere(placed: true)
        XCTAssertTrue(window.firstResponder === table)

        // A field has it: nothing takes it from there.
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 100, height: 22))
        window.contentView?.addSubview(field)
        XCTAssertTrue(window.makeFirstResponder(field))
        table.takeKeyboardIfNowhere(placed: true)
        XCTAssertFalse(window.firstResponder === table)
        window.contentView = nil
    }

    /// Placed under the first run's welcome, the list takes the keyboard once the first account's rows show, from
    /// where the window put it as the welcome went, and only then. The splitter keeps only a focus Tab gave it.
    func testAListPlacedUnderTheWelcomeTakesTheKeyboardOnceTheFirstRowsShow() async throws {
        let (_, controller) = try await table()
        let table = controller.table
        var welcome = true
        table.welcomeUp = { welcome }
        let window = window()
        window.contentView = Host()
        window.contentView?.addSubview(controller.scrollView)
        XCTAssertTrue(window.makeFirstResponder(window.contentView))
        table.takeKeyboardIfNowhere(placed: true)
        XCTAssertFalse(window.firstResponder === table, "not under the welcome")
        table.rowsShown()
        XCTAssertFalse(window.firstResponder === table, "nor while it stands")
        welcome = false
        table.rowsShown()
        XCTAssertTrue(window.firstResponder === table, "the first account's rows show: the list has the keyboard")
        XCTAssertTrue(window.makeFirstResponder(window.contentView))
        table.rowsShown()
        XCTAssertFalse(window.firstResponder === table, "once: rows that come later leave the keyboard where it is")
        window.contentView = nil

        let tab = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: 0,
                                   context: nil, characters: "\t", charactersIgnoringModifiers: "\t", isARepeat: false,
                                   keyCode: 48)
        let click = NSEvent.mouseEvent(with: .leftMouseDown, location: .zero, modifierFlags: [], timestamp: 0,
                                       windowNumber: 0, context: nil, eventNumber: 0, clickCount: 1, pressure: 1)
        XCTAssertTrue(MeadowSplitter.keepsFocus(after: tab), "Tab brings the keyboard to the splitter")
        XCTAssertFalse(MeadowSplitter.keepsFocus(after: click), "a click gives it back to the list")
        XCTAssertFalse(MeadowSplitter.keepsFocus(after: nil), "and so does the window opening")
    }

    /// A click on the window's SwiftUI (the brief's card, a lane) leaves the keyboard there, where no list key once
    /// worked: a single key nothing takes goes on to the list, which takes the keyboard with it; where no list is
    /// shown it runs as the list runs it, so z undoes. A field keeps every key typed in it.
    func testTheListsKeysWorkAfterAClickOnTheWindowsSwiftUI() async throws {
        let server = Fixture.server(rows: Array(Fixture.inbox.prefix(6)))
        let (app, controller) = try await table(server: server)
        let window = window()
        let host = NSHostingView(rootView: Color.clear.frame(width: 860, height: 800))
        window.contentView = host
        host.addSubview(controller.scrollView)
        let catcher = UntakenKeys.Catcher.hang(on: window, for: app.mailbox)
        XCTAssertTrue(UntakenKeys.Catcher.hang(on: window, for: app.mailbox) === catcher, "once a window")
        // The list took the keyboard as it came: a click on SwiftUI takes it from there.
        try await Task.sleep(for: .milliseconds(20))
        XCTAssertTrue(window.makeFirstResponder(host), "a click on SwiftUI: the keyboard is its")
        func type(_ key: String, _ code: UInt16, in window: NSWindow) async throws {
            window.sendEvent(try XCTUnwrap(NSEvent.keyEvent(
                with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: window.windowNumber,
                context: nil, characters: key, charactersIgnoringModifiers: key, isARepeat: false, keyCode: code)))
            for _ in 0..<10 { try await Task.sleep(for: .milliseconds(10)) }
        }
        try await type("j", 38, in: window)
        let first = MailCopy(try XCTUnwrap(app.list?.copy(at: 0)))
        XCTAssertEqual(app.selection.copy, first, "j goes on to the list, which opens its first row")
        XCTAssertTrue(window.firstResponder === controller.table, "and the list has the keyboard")
        try await type("j", 38, in: window)
        XCTAssertEqual(app.selection.copy, MailCopy(try XCTUnwrap(app.list?.copy(at: 1))))

        // A field: what is typed is its own.
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 100, height: 22))
        host.addSubview(field)
        XCTAssertTrue(window.makeFirstResponder(field))
        let open = app.selection.copy
        try await type("k", 40, in: window)
        XCTAssertEqual(app.selection.copy, open, "k typed in a field moves nothing")
        XCTAssertEqual(field.currentEditor()?.string, "k")
        window.contentView = nil

        // No list shown: z undoes, as the toast says.
        let bare = self.window()
        let lone = NSHostingView(rootView: Color.clear.frame(width: 860, height: 800))
        bare.contentView = lone
        UntakenKeys.Catcher.hang(on: bare, for: app.mailbox)
        XCTAssertTrue(bare.makeFirstResponder(lone))
        try await type("z", 6, in: bare)
        await until { !server.asked(API.Post.Undo.path).isEmpty }
        bare.contentView = nil
    }

    /// Return in the search field leaves the keyboard there, as the page's does, where J would be typed: the empty
    /// pane says ↓ then, and ↓ goes on to the list, which takes the keyboard and opens its first row.
    func testTheEmptyPaneSaysTheKeyThatStartsAtTheTopFromWhereTheKeyboardIs() async throws {
        let (app, controller) = try await table()
        let window = window()
        window.contentView = Host()
        window.contentView?.addSubview(controller.scrollView)
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 100, height: 22))
        window.contentView?.addSubview(field)
        XCTAssertTrue(window.makeFirstResponder(field))
        XCTAssertEqual(PaneEmptyWords.of(app).line, "Or press J to start at the top.")
        app.mailbox.inSearchField = true
        XCTAssertEqual(PaneEmptyWords.of(app).line, "Or press ↓ to start at the top.")
        app.mailbox.enterList(in: window)
        XCTAssertTrue(window.firstResponder === controller.table, "↓ hands the keyboard to the list")
        XCTAssertEqual(app.selection.copy, MailCopy(try XCTUnwrap(app.list?.copy(at: 0))), "and opens its first row")
        window.contentView = nil
    }

    /// Esc out of a search, with a message chosen there that stands far down its Inbox: back in the Inbox the message
    /// stays chosen, and its row is scrolled into view as the Inbox's rows come.
    func testLeavingASearchShowsTheRowOfTheMessageStillChosen() async throws {
        let rows = (1...60).map { Fixture.row($0) }
        let app = MailApp()
        self.app = app
        app.mailbox.wire = Fixture.server(rows: rows).wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.place = .search(Search(q: "budget"))
        let far = rows[50]
        app.mailbox.chose([far])
        app.mailbox.leaveSearch()
        XCTAssertEqual(app.place, .allInboxes)
        let list = try XCTUnwrap(app.list)
        let look = RowLook(inks: RowInks.of(.day, light: .day, highContrast: false), measure: RowMeasure(.comfortable),
                           accounts: app.mailbox.accounts)
        let controller = MailboxTableController(list: list, mailbox: app.mailbox, look: look, sections: true)
        defer { controller.stop() }
        controller.scrollView.frame = NSRect(x: 0, y: 0, width: 420, height: 400)
        controller.table.reloadData()
        await until { list.position(of: MailCopy(far)) != nil }

        XCTAssertEqual(app.selection.copy, MailCopy(far), "the message stays open")
        let row = controller.table.selectedRow
        XCTAssertGreaterThan(row, 20, "its row is chosen, far down")
        XCTAssertTrue(controller.table.visibleRect.intersects(controller.table.rect(ofRow: row)), "and in view")
    }

    /// Ask, then Esc: back to where the question was asked, as the page's Esc stops asking, whether Esc comes from the
    /// window (the answer has no list to take it), the search field, or the palette that asked. A message the answer
    /// cites, opened from the palette, keeps the answer beside it. The list placed again takes the keyboard.
    func testEscOnAnAnswerGoesBackToWhereTheQuestionWasAsked() async throws {
        let (app, controller) = try await table()
        let desk = try XCTUnwrap(self.desk)
        let inbox = app.place
        let cited = MailCopy(try XCTUnwrap(app.list?.copy(at: 0)))
        desk.goToAnswer()
        XCTAssertEqual(app.place, .desk(.ask))
        XCTAssertTrue(app.mailbox.leaveAsk())
        XCTAssertEqual(app.place, inbox, "Esc goes back to where the question was asked")
        XCTAssertFalse(app.mailbox.leaveAsk(), "and only from an answer")
        XCTAssertEqual(app.place, inbox)

        // From a folder, asked again on the answer, then the search field's Esc.
        let sent = Place.folder(account: Fixture.ada, folder: "Sent")
        app.place = sent
        desk.goToAnswer()
        desk.goToAnswer()
        app.mailbox.leaveSearch()
        XCTAssertEqual(app.place, sent, "back where the first question was asked")

        // The palette that asked goes back as it goes; one that came up over an answer, or that a cited message
        // was opened from, leaves the answer; and another dialog's going leaves it too.
        XCTAssertTrue(WindowOverlays.leavesAnswer(palette: true, overAnswer: false, place: .desk(.ask), open: nil))
        XCTAssertFalse(WindowOverlays.leavesAnswer(palette: true, overAnswer: true, place: .desk(.ask), open: nil))
        XCTAssertFalse(WindowOverlays.leavesAnswer(palette: true, overAnswer: false, place: .desk(.ask), open: cited))
        XCTAssertFalse(WindowOverlays.leavesAnswer(palette: false, overAnswer: false, place: .desk(.ask), open: nil))
        XCTAssertFalse(WindowOverlays.leavesAnswer(palette: true, overAnswer: false, place: inbox, open: nil))
        desk.openCited(cited)
        XCTAssertEqual(app.place, .desk(.ask))
        XCTAssertEqual(app.selection.copy, cited)
        XCTAssertTrue(app.mailbox.leaveAsk())
        XCTAssertEqual(app.place, sent, "Esc later still goes back")

        // The answer's window held the keyboard: the list that comes back takes it.
        let window = window()
        window.contentView = Host()
        XCTAssertTrue(window.makeFirstResponder(window.contentView))
        window.contentView?.addSubview(controller.scrollView)
        await until { window.firstResponder === controller.table }
        window.contentView = nil
    }

    // MARK: Esc

    func testEscEndsFocusReadingBeforeTheListsOwnEsc() async throws {
        let (app, controller) = try await table()
        let shell = MeadowShell(defaults: try XCTUnwrap(UserDefaults(suiteName: "ListKeysTests-\(UUID())")))
        controller.shell = shell
        app.place = .search(Search(q: "from:grace", account: Fixture.ada))
        shell.focus = true
        press("\u{1b}", controller.table, code: 53)
        XCTAssertFalse(shell.focus, "Esc leaves focus reading, as the legend says")
        XCTAssertNotNil(app.place?.search, "and only that: the search stays")
        press("\u{1b}", controller.table, code: 53)
        XCTAssertNil(app.place?.search, "the next Esc leaves the search")
    }

    /// A file's Quick Look put up from the reader, a person back in the list: Esc takes the preview down first, the
    /// list keeping the keyboard and its place, and the next Esc is the list's own.
    func testEscTakesAFilesQuickLookDownBeforeTheListsOwnEsc() async throws {
        let (app, controller) = try await table()
        let window = window()
        defer { window.close() }
        window.contentView?.addSubview(controller.scrollView)
        window.makeFirstResponder(controller.table)
        app.place = .search(Search(q: "from:grace", account: Fixture.ada))
        var taken = 0
        QuickLookShown.up(UUID()) { taken += 1 }

        press("\u{1b}", controller.table, code: 53)
        XCTAssertEqual(taken, 1, "Esc takes the preview down")
        XCTAssertFalse(QuickLookShown.isUp)
        XCTAssertNotNil(app.place?.search, "and only that: the search stays")
        XCTAssertTrue(window.firstResponder === controller.table, "the list keeps the keyboard")
        press("\u{1b}", controller.table, code: 53)
        XCTAssertEqual(taken, 1)
        XCTAssertNil(app.place?.search, "the next Esc is the list's: it leaves the search")
    }

    /// The reader's files and the Document Hub show a file in Quick Look through `quickLookClosable`, which says
    /// while it is up, so the list's Esc can take it down, and stops saying so once it went.
    func testAFileShownInQuickLookIsOneEscCanTakeDown() throws {
        let window = NSWindow(contentRect: NSRect(x: -4000, y: -4000, width: 300, height: 200), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        let preview = PreviewHolder()
        window.contentView = NSHostingView(rootView: PreviewHolder.Shown(holder: preview))
        window.orderFront(nil)
        let base = FileManager.default.temporaryDirectory.appending(path: "ListKeysTests-\(UUID())")
        defer { try? FileManager.default.removeItem(at: base) }
        func settle(until done: () -> Bool) {
            for _ in 0..<60 where !done() { RunLoop.main.run(until: Date().addingTimeInterval(0.05)) }
        }
        preview.url = try PreviewFiles.write(Data("fictional".utf8), named: "floor-plan.txt", in: base)
        settle { QuickLookShown.isUp }
        XCTAssertTrue(QuickLookShown.close(), "a file shown is one Esc can take down")
        settle { preview.url == nil }
        XCTAssertNil(preview.url, "closing it clears what the view shows")
        XCTAssertFalse(QuickLookShown.close(), "and nothing is up after")

        preview.url = try PreviewFiles.write(Data("fictional".utf8), named: "floor-plan.txt", in: base)
        settle { QuickLookShown.isUp }
        XCTAssertTrue(QuickLookShown.isUp)
        preview.url = nil
        settle { !QuickLookShown.isUp }
        XCTAssertFalse(QuickLookShown.isUp, "a preview closed some other way is no longer up")
    }

    func testEscWithSeveralChosenKeepsTheOneChosenLast() async throws {
        let (app, controller) = try await table()
        let table = controller.table
        table.selectRowIndexes(IndexSet(integer: 1), byExtendingSelection: false)
        // ⌘-clicks on two rows below it, the fourth last.
        table.selectRowIndexes(IndexSet(integer: 2), byExtendingSelection: true)
        table.selectRowIndexes(IndexSet(integer: 3), byExtendingSelection: true)
        XCTAssertEqual(app.mailbox.chosenRows.count, 3)
        press("\u{1b}", table, code: 53)
        XCTAssertEqual(table.selectedRowIndexes, IndexSet(integer: 3))
        XCTAssertEqual(app.selection.copy?.key, 4, "the row clicked last stays, open")
        press("j", table)
        XCTAssertEqual(app.selection.copy?.key, 5, "and j goes on from it, not from the top")
    }

    // MARK: Marks

    func testXMarksTheRowOpenAndJAndKMoveOnWithoutLosingTheMarks() async throws {
        let (app, controller) = try await table()
        let table = controller.table, mailbox = app.mailbox
        table.selectRowIndexes(IndexSet(integer: 1), byExtendingSelection: false)
        XCTAssertFalse(mailbox.marking, "a row clicked is open, not marked")
        press("x", table)
        XCTAssertTrue(mailbox.marking, "x marks the row open, and the bulk bar shows for it")
        XCTAssertEqual(mailbox.marked.map(\.key), [2])
        XCTAssertEqual(app.selection.copy?.key, 2, "the message stays open")
        XCTAssertEqual(table.selectedRowIndexes, IndexSet(integer: 1))

        press("j", table)
        XCTAssertEqual(app.selection.copy?.key, 3, "j opens the next row")
        XCTAssertEqual(mailbox.marked.map(\.key), [2], "and the mark stays")
        press("\u{F701}", table, code: 125, held: [.numericPad, .function])
        XCTAssertEqual(app.selection.copy?.key, 4, "↓ goes on as j does")
        press("x", table)
        XCTAssertEqual(mailbox.marked.map(\.key), [2, 4], "x marks the next one too")
        XCTAssertEqual(table.selectedRowIndexes, IndexSet([1, 3]), "and the table checks both")
        let targets = await mailbox.targets(standing: false)
        XCTAssertEqual(targets.map(\.key), [2, 4], "a verb acts on the marks")

        press("k", table)
        press("k", table)
        XCTAssertEqual(app.selection.copy?.key, 2)
        XCTAssertEqual(mailbox.marked.map(\.key), [2, 4], "k keeps the marks too")
        press("x", table)
        XCTAssertEqual(mailbox.marked.map(\.key), [4], "x on a marked row takes its mark off")
        XCTAssertTrue(mailbox.marking, "and the other stays marked, alone")
        let left = await mailbox.targets(standing: false)
        XCTAssertEqual(left.map(\.key), [4], "a verb acts on the one marked, not the message open")

        press("j", table)
        press("j", table)
        press("x", table)
        XCTAssertFalse(mailbox.marking, "with the last mark gone, no bulk bar")
        XCTAssertEqual(mailbox.chosenRows.map(\.key), [4], "and the row open is chosen alone, as before")
        XCTAssertEqual(table.selectedRowIndexes, IndexSet(integer: 3))
    }

    func testXOnTheOpenRowAgainTakesItsMarkOff() async throws {
        let (app, controller) = try await table()
        let table = controller.table, mailbox = app.mailbox
        table.selectRowIndexes(IndexSet(integer: 2), byExtendingSelection: false)
        press("x", table)
        XCTAssertTrue(mailbox.marking)
        press("x", table)
        XCTAssertFalse(mailbox.marking)
        XCTAssertEqual(mailbox.chosenRows.map(\.key), [3])
        XCTAssertEqual(app.selection.copy?.key, 3, "the message stays open throughout")
    }

    func testXWithNothingOpenSaysSo() async throws {
        let (app, controller) = try await table()
        press("x", controller.table)
        XCTAssertFalse(app.mailbox.marking)
        XCTAssertEqual(app.mailbox.toast?.text, "Choose a message first")
    }

    func testEscTakesTheMarksOffAndKeepsTheMessageOpen() async throws {
        let (app, controller) = try await table()
        let table = controller.table, mailbox = app.mailbox
        table.selectRowIndexes(IndexSet(integer: 0), byExtendingSelection: false)
        press("x", table)
        press("j", table)
        press("x", table)
        press("j", table)
        XCTAssertEqual(mailbox.marked.map(\.key), [1, 2])
        press("\u{1b}", table, code: 53)
        XCTAssertFalse(mailbox.marking, "Esc takes every mark off")
        XCTAssertEqual(app.selection.copy?.key, 3, "and the message open beside them stays open")
        XCTAssertEqual(table.selectedRowIndexes, IndexSet(integer: 2))
    }

    func testFilingTheMarksTakesThemOffAndKeepsTheMessageOpen() async throws {
        let server = Fixture.server(rows: Array(Fixture.inbox.prefix(6)))
        server.on("POST", API.Post.BulkFile.path, API.RowsFiled(count: 2, failed: 0, filed: [], msg: "", ok: true,
                                                                partial: false, queued: 0, rowsFailed: 0, undoable: true))
        let (app, controller) = try await table(server: server)
        let table = controller.table, mailbox = app.mailbox
        table.selectRowIndexes(IndexSet(integer: 0), byExtendingSelection: false)
        press("x", table)
        press("j", table)
        press("j", table)
        press("x", table)
        press("j", table)
        press("e", table)
        await until { !mailbox.marking }
        XCTAssertEqual(app.selection.copy?.key, 4, "the message open wasn't among them, and stays")
        XCTAssertEqual(mailbox.chosenRows.map(\.key), [4])
    }

    // MARK: The ends of the list

    func testKFromTheInboxsFirstRowGoesToTheBrief() async throws {
        let (app, controller) = try await table(brief: true)
        app.mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
        press("k", controller.table)
        XCTAssertEqual(app.place, .desk(.brief), "k reaches the brief, as the up arrow does")
        press("k", controller.table)
        XCTAssertEqual(app.place, .desk(.brief), "nothing is above it, and nothing is said")
        XCTAssertNil(app.mailbox.toast)
        XCTAssertTrue(routes.sent.filter { $0.method == "POST" }.isEmpty, "the brief opens; nothing is written")
    }

    func testJAndKAtTheEndsSaySo() async throws {
        let (app, _) = try await table()
        let mailbox = app.mailbox
        app.place = .folder(account: Fixture.ada, folder: "Archive")
        let list = try XCTUnwrap(app.list)
        await until { list.total > 0 && list.copy(at: list.total - 1) != nil }
        let last = try XCTUnwrap(list.copy(at: list.total - 1))
        mailbox.chose([last])
        mailbox.move(by: 1)
        XCTAssertEqual(app.selection.copy?.key, last.key, "the last row stays open")
        XCTAssertEqual(mailbox.toast?.text, "No more messages below", "as n says there's no more unread")
        XCTAssertEqual(mailbox.toast?.refused, true, "with the alert, not the tick")
        mailbox.chose([try XCTUnwrap(list.copy(at: 0))])
        mailbox.move(by: -1)
        XCTAssertEqual(mailbox.toast?.text, "No more messages above", "outside the Inbox no brief is above")
    }

    // MARK: A row's clock

    func testARowsClockIsWhereItsSnoozeChooserHangs() async throws {
        let (app, controller) = try await table()
        let window = window()
        window.contentView?.addSubview(controller.scrollView)
        let anchors = MeadowAnchors()
        controller.anchors = anchors
        var asked: [Int] = []
        app.mailbox.snoozes = { rows in asked += rows.map(\.key) }
        let table = controller.table
        let cell = try XCTUnwrap(table.view(atColumn: 0, row: 2, makeIfNecessary: true) as? MailRowCell)
        let clock = try XCTUnwrap(cell.frame(of: .snooze))
        controller.press(.act(.snooze), row: 2)
        XCTAssertEqual(asked, [3])
        let hung = try XCTUnwrap(anchors.frames[MailboxTableController.rowSnooze], "the row's clock, not the pane's")
        // In the window, its top left first, as SwiftUI's global space has it.
        let inWindow = cell.convert(clock, to: nil)
        XCTAssertEqual(hung.minX, inWindow.minX, accuracy: 0.5)
        XCTAssertEqual(hung.minY, window.contentView!.bounds.height - inWindow.maxY, accuracy: 0.5)
        XCTAssertEqual(hung.size, clock.size)
        window.contentView = nil
    }

    /// h hangs its chooser where a click on what it snoozes would: under the pane's Snooze for the message in the
    /// pane, under the first marked row's clock for the marks, and under the pane's Snooze where no row is on screen.
    func testHHangsItsChooserUnderThePanesSnoozeOrTheMarkedRowsClock() async throws {
        let (app, controller) = try await table()
        let window = window()
        window.contentView?.addSubview(controller.scrollView)
        let anchors = MeadowAnchors()
        controller.anchors = anchors
        let pane = CGRect(x: 600, y: 20, width: 80, height: 30)
        anchors.remember("snooze", at: pane)
        let list = try XCTUnwrap(app.list)
        let open = MailCopy(try XCTUnwrap(list.copy(at: 1)))
        XCTAssertEqual(anchors.snooze(for: [open], open: open), pane, "the message in the pane: the pane's Snooze")
        let marks = [MailCopy(try XCTUnwrap(list.copy(at: 2))), MailCopy(try XCTUnwrap(list.copy(at: 4)))]
        _ = controller.table.view(atColumn: 0, row: 2, makeIfNecessary: true)
        let clock = try XCTUnwrap(controller.clockFrame(row: 2))
        XCTAssertEqual(anchors.snooze(for: marks, open: open), clock, "the marks: the first one's clock")
        XCTAssertEqual(anchors.snooze(for: marks, open: nil), clock)
        let away = MailCopy(key: 90, folder: "INBOX", uid: 90, account: Fixture.ada)
        XCTAssertEqual(anchors.snooze(for: [away], open: nil), pane, "no row on screen: the pane's Snooze")
        anchors.forget("snooze")
        XCTAssertNil(anchors.snooze(for: [away], open: nil), "nothing on screen: the middle of the window")

        // A view that goes forgets only where it said a name stands, not where the view that came after it said.
        let (gone, came) = (UUID(), UUID())
        anchors.remember("snooze", at: pane, by: gone)
        anchors.remember("snooze", at: pane.offsetBy(dx: -40, dy: 0), by: came)
        anchors.forget("snooze", by: gone)
        XCTAssertEqual(anchors.frames["snooze"], pane.offsetBy(dx: -40, dy: 0))
        anchors.forget("snooze", by: came)
        XCTAssertNil(anchors.frames["snooze"])
        window.contentView = nil
    }
}

/// A message's own window: it has the keyboard as it opens, and Space, the arrows, Page Up and Down, Home and End
/// scroll its message.
@MainActor
final class MessageWindowKeysTests: XCTestCase {
    private final class Flipped: NSView {
        override var isFlipped: Bool { true }
    }

    private func key(_ typed: String, code: UInt16 = 0, held: NSEvent.ModifierFlags = []) -> NSEvent {
        NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: held, timestamp: 0, windowNumber: 0,
                         context: nil, characters: typed, charactersIgnoringModifiers: typed, isARepeat: false,
                         keyCode: code)!
    }

    private func function(_ key: Int) -> String {
        String(Character(UnicodeScalar(UInt32(key))!))
    }

    func testAMessagesWindowScrollsItsMessageByTheKeyboard() throws {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 400, height: 300))
        let document = Flipped(frame: NSRect(x: 0, y: 0, width: 400, height: 2000))
        document.addSubview(FrameWebView(frame: document.bounds, configuration: WKWebViewConfiguration()))
        scroll.documentView = document
        window.contentView?.addSubview(scroll)
        let keys = MessageWindowKeys.Keys(frame: .zero)
        window.contentView?.addSubview(keys)
        XCTAssertTrue(window.initialFirstResponder === keys, "the message, as the window opens")
        keys.takeIfNowhere()
        XCTAssertTrue(window.firstResponder === keys)
        var top: CGFloat { scroll.contentView.bounds.minY }

        keys.keyDown(with: key(" ", code: 49))
        XCTAssertEqual(top, 260, "Space pages down")
        keys.keyDown(with: key(" ", code: 49, held: .shift))
        XCTAssertEqual(top, 0, "⇧Space back up")
        keys.keyDown(with: key(function(NSDownArrowFunctionKey), code: 125, held: [.function, .numericPad]))
        XCTAssertEqual(top, ReaderPage.line, "an arrow, a line")
        keys.keyDown(with: key(function(NSEndFunctionKey), code: 119, held: .function))
        XCTAssertEqual(top, 2000 - 300, "End, the foot")
        keys.keyDown(with: key(function(NSPageUpFunctionKey), code: 116, held: .function))
        XCTAssertEqual(top, 1700 - 260)
        keys.keyDown(with: key(function(NSHomeFunctionKey), code: 115, held: .function))
        XCTAssertEqual(top, 0, "Home, the top")

        XCTAssertFalse(ReaderPage.scrolls(key("r", code: 15), in: window), "r goes on, to the Message menu")
        XCTAssertFalse(ReaderPage.scrolls(key(function(NSDownArrowFunctionKey), code: 125, held: .command), in: window))

        // A field takes the keyboard (Find in Message's); the message takes it back once the field lets it go.
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 100, height: 22))
        window.contentView?.addSubview(field)
        XCTAssertTrue(window.makeFirstResponder(field))
        keys.takeIfNowhere()
        XCTAssertFalse(window.firstResponder === keys)
        field.removeFromSuperview()
        keys.takeIfNowhere()
        XCTAssertTrue(window.firstResponder === keys)
        window.contentView = nil
    }
}

/// A file shown in Quick Look while `url` is set, as the reader's files and the Document Hub show one.
@MainActor
@Observable
private final class PreviewHolder {
    var url: URL?

    struct Shown: View {
        @Bindable var holder: PreviewHolder

        var body: some View {
            // Read here, as a view's own `@State` is: a change draws the view again.
            let _ = holder.url
            Color.clear.quickLookClosable($holder.url)
        }
    }
}
