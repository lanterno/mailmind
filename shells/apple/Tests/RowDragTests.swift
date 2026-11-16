import XCTest

/// Rows dragged from the list drop on the rail's folders and move there, as Move To moves them: only on a folder that
/// takes filing, and only rows of that folder's account. Into another app a row goes as its subject.
@MainActor
final class RowDragTests: XCTestCase {
    private func opened(_ server: FakeServer) async throws -> (MailApp, MailboxTableController) {
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.place = .folder(account: Fixture.ada, folder: "INBOX")
        let list = try XCTUnwrap(app.list)
        await until { list.copy(at: 0) != nil }
        let look = RowLook(inks: RowInks.of(.day, light: .day, highContrast: false), measure: RowMeasure(.comfortable),
                           accounts: app.mailbox.accounts)
        let controller = MailboxTableController(list: list, mailbox: app.mailbox, look: look, sections: true)
        controller.scrollView.frame = NSRect(x: 0, y: 0, width: 420, height: 800)
        controller.table.reloadData()
        return (app, controller)
    }

    private let receipts = Fixture.folder("Receipts", role: "custom")

    func testAFolderTakesOnlyItsAccountsRowsAndOnlyIfItTakesFiling() async throws {
        let (app, _) = try await opened(Fixture.server(grouped: false))
        let mailbox = app.mailbox
        XCTAssertFalse(mailbox.takesDrop(receipts, of: Fixture.ada), "nothing is being dragged")
        mailbox.dragging = [Fixture.row(1), Fixture.row(3)]
        XCTAssertTrue(mailbox.takesDrop(receipts, of: Fixture.ada))
        XCTAssertFalse(mailbox.takesDrop(Fixture.folder("Drafts", role: "drafts", filing: false), of: Fixture.ada))
        XCTAssertFalse(mailbox.takesDrop(Fixture.folder("INBOX", role: "inbox"), of: Fixture.work),
                       "a move stays in its account")
        mailbox.dragging = [Fixture.row(1), Fixture.row(2, account: Fixture.work)]
        XCTAssertFalse(mailbox.takesDrop(receipts, of: Fixture.ada))
        XCTAssertFalse(mailbox.drop(on: receipts, of: Fixture.ada))
    }

    func testADropMovesTheRowsDraggedThere() async throws {
        let server = Fixture.server(grouped: false)
        server.on("POST", API.Post.Move.path, API.Filed(msg: "ok", ok: true, status: nil, undoable: true))
        let (app, _) = try await opened(server)
        let mailbox = app.mailbox
        mailbox.dragging = [Fixture.row(3)]
        XCTAssertTrue(mailbox.drop(on: receipts, of: Fixture.ada))
        XCTAssertTrue(mailbox.dragging.isEmpty, "the drag is done with")
        await until { !server.asked(API.Post.Move.path).isEmpty }
        let body = try JSONDecoder().decode(API.MoveToBody.self,
                                            from: try XCTUnwrap(server.asked(API.Post.Move.path).first).body)
        XCTAssertEqual(body, API.MoveToBody(folder: "INBOX", key: 3, to: "Receipts", uid: 3))
    }

    func testARowGoesToAnotherAppAsItsSubject() async throws {
        let (_, controller) = try await opened(Fixture.server(grouped: false))
        let writer = try XCTUnwrap(controller.tableView(controller.table, pasteboardWriterForRow: 0) as? NSPasteboardItem)
        XCTAssertEqual(writer.string(forType: .string), "Launch date")
        XCTAssertNil(controller.tableView(controller.table, pasteboardWriterForRow: 10_000))
    }
}
