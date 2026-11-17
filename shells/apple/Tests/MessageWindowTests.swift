import XCTest

/// A double-click on a row, or the row menu's Open in New Window, opens its message in a window of its own: the
/// window is handed the copy, which it keeps as the scene's value.
@MainActor
final class MessageWindowTests: XCTestCase {
    private func table(brief: BriefLine? = nil) async throws -> (MailApp, MailboxTableController) {
        let app = MailApp()
        app.mailbox.wire = Fixture.server().wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.openList()
        let list = try XCTUnwrap(app.list)
        await until { list.copy(at: 0) != nil }
        let look = RowLook(inks: RowInks.of(.day, light: .day, highContrast: false), measure: RowMeasure(.comfortable),
                           accounts: app.mailbox.accounts)
        let controller = MailboxTableController(list: list, mailbox: app.mailbox, look: look, sections: true)
        controller.setBrief(brief, chosen: false)
        controller.scrollView.frame = NSRect(x: 0, y: 0, width: 420, height: 800)
        controller.table.reloadData()
        return (app, controller)
    }

    func testADoubleClickOpensTheRowsMessageInAWindowOfItsOwn() async throws {
        let (app, controller) = try await table()
        var opened: [MailCopy] = []
        app.mailbox.opensWindow = { opened.append($0) }
        XCTAssertEqual(controller.table.doubleAction, #selector(MailboxTableController.openedTwice))
        XCTAssertTrue(controller.table.target === controller)
        controller.openWindow(row: 2)
        XCTAssertEqual(opened, [MailCopy(key: 3, folder: "INBOX", uid: 3, account: Fixture.ada)])
        controller.openWindow(row: -1)
        XCTAssertEqual(opened.count, 1, "a double-click on no row opens nothing")
    }

    func testTheBriefsRowOpensNoWindow() async throws {
        let (app, controller) = try await table(brief: BriefLine(needs: [], waiting: 0, canWait: 0))
        var opened: [MailCopy] = []
        app.mailbox.opensWindow = { opened.append($0) }
        controller.openWindow(row: 0)
        XCTAssertTrue(opened.isEmpty)
        controller.openWindow(row: 1)
        XCTAssertEqual(opened.map(\.key), [1])
    }

    func testTheRowMenuOffersANewWindowForOneRow() async throws {
        let (app, _) = try await table()
        let mailbox = app.mailbox
        XCTAssertNotEqual(RowMenu.items(for: mailbox, clicked: Fixture.row(1)).first?.title, "Open in New Window",
                          "only where the reader can open one")
        var opened: [MailCopy] = []
        mailbox.opensWindow = { opened.append($0) }
        let items = RowMenu.items(for: mailbox, clicked: Fixture.row(1))
        XCTAssertEqual(items.first?.title, "Open in New Window")
        let first = try XCTUnwrap(items.first)
        _ = (first.target as AnyObject).perform(try XCTUnwrap(first.action))
        XCTAssertEqual(opened.map(\.key), [1])
        mailbox.chose([Fixture.row(1), Fixture.row(3)])
        XCTAssertNotEqual(RowMenu.items(for: mailbox, clicked: nil).first?.title, "Open in New Window",
                          "several rows open no one window")
    }

    /// A message's window speaks the core the mail window opened, the process's one, and none before it is open or
    /// once it didn't open: it never opens a core of its own.
    func testAMessagesWindowSpeaksTheProcesssOneCore() async throws {
        XCTAssertTrue(MessageWindow(copy: nil).app === MailApp.shared, "the window takes the process's app")
        let folder = FileManager.default.temporaryDirectory.appending(path: "mailmind-message-window-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let home = CoreHome(folder: folder.path(percentEncoded: false),
                            variables: ["MAILMIND_DB_KEY": Self.key, "MAILMIND_NOSYNC": "1"],
                            standIn: folder.appending(path: "keychain.json").path(percentEncoded: false))
        let app = MailApp(home: { home }, open: { try await Core.open($0) })
        XCTAssertNil(MessageWindow.core(of: app), "none before the mail window opened it")
        await app.start()
        let core = try XCTUnwrap(MessageWindow.core(of: app))
        XCTAssertTrue(core === app.core, "the mail window's core")
        await app.core?.close()

        let failed = MailApp(home: { home }, open: { _ in throw HostProblem.NotOpened(why: "not today") })
        await failed.start()
        XCTAssertNil(MessageWindow.core(of: failed), "none once the core didn't open")
    }

    /// Guards only this test's empty database.
    private static let key = "3d5f7a9c1e2b4d6f8a0c2e4b6d8f1a3c5e7b9d0f2a4c6e8b1d3f5a7c9e0b2d4f"

    /// A window's value is kept as data: the copy comes back as it went.
    func testACopyIsKeptAsData() throws {
        let copy = MailCopy(key: 7, folder: "Receipts/2026", uid: 41, account: Fixture.work)
        XCTAssertEqual(try JSONDecoder().decode(MailCopy.self, from: JSONEncoder().encode(copy)), copy)
    }
}
