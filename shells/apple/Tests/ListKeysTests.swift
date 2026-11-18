import AppKit
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
    private func table(brief: Bool = false) async throws -> (MailApp, MailboxTableController) {
        let app = MailApp()
        self.app = app
        app.mailbox.wire = Fixture.server(rows: Array(Fixture.inbox.prefix(6))).wire
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
        for (typed, held) in [("7", []), ("x", []), ("q", []), ("Q", .shift), (" ", []), ("~", .shift)]
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
}
