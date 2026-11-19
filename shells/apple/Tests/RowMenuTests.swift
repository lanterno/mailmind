import AppKit
import XCTest

/// A row's menu, as a right-click opens it: on the row clicked alone, or on every row marked when it is one of them,
/// and never changing what is chosen or open.
@MainActor
final class RowMenuTests: XCTestCase {
    /// Six rows of Ada's Inbox, none a conversation and the fifth unread, and the routes a row's menu calls.
    private func world() async throws -> (MailApp, FakeServer) {
        let server = Fixture.server(rows: (1...6).map { Fixture.row($0, seen: $0 != 5) })
        let filed = API.Filed(msg: "ok", ok: true, undoable: true)
        for path in [API.Post.Archive.path, API.Post.Move.path, API.Post.Seen.path] { server.on("POST", path, filed) }
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.openList()
        let list = try XCTUnwrap(app.list)
        await until { list.copy(at: list.total - 1) != nil }
        return (app, server)
    }

    private func row(_ app: MailApp, _ position: Int) throws -> API.FolderRow {
        try XCTUnwrap(app.list?.copy(at: position))
    }

    private func fire(_ item: NSMenuItem) throws {
        _ = (item.target as AnyObject).perform(try XCTUnwrap(item.action))
    }

    private func item(_ title: String, in items: [NSMenuItem]) throws -> NSMenuItem {
        try XCTUnwrap(items.first { $0.title == title }, "the menu offers \(title)")
    }

    /// The keys of the rows a request named.
    private func keys(_ asked: [FakeServer.Asked]) -> [Int] {
        asked.compactMap { try? JSONDecoder().decode(API.MoveBody.self, from: $0.body).key }
    }

    func testTheMenuOfARowNotMarkedActsOnItAloneAndKeepsTheMarksAndTheMessageOpen() async throws {
        let (app, server) = try await world()
        let mailbox = app.mailbox
        mailbox.chose([try row(app, 0)])
        mailbox.markOpen()
        mailbox.move(by: 1)
        XCTAssertEqual(mailbox.marked.map(\.key), [1])
        XCTAssertEqual(app.selection.copy?.key, 2)

        let clicked = try row(app, 3)
        XCTAssertEqual(RowMenu.rows(for: mailbox, clicked: clicked).map(\.key), [4], "the row clicked alone")
        try fire(try item("Archive", in: RowMenu.items(for: mailbox, clicked: clicked)))
        await until { !server.asked(API.Post.Archive.path).isEmpty }
        XCTAssertEqual(keys(server.asked(API.Post.Archive.path)), [4])
        await until { mailbox.toast != nil }
        XCTAssertEqual(mailbox.marked.map(\.key), [1], "the mark stays")
        XCTAssertTrue(mailbox.marking)
        XCTAssertEqual(app.selection.copy?.key, 2, "and so does the message open")
    }

    func testTheMenuOfAMarkedRowActsOnEveryRowMarked() async throws {
        let (app, _) = try await world()
        let mailbox = app.mailbox
        mailbox.chose([try row(app, 0), try row(app, 2)])
        XCTAssertEqual(RowMenu.rows(for: mailbox, clicked: try row(app, 2)).map(\.key), [1, 3])
        XCTAssertEqual(RowMenu.rows(for: mailbox, clicked: try row(app, 4)).map(\.key), [5], "but not another's")
        XCTAssertEqual(mailbox.marked.map(\.key), [1, 3], "asking changes nothing chosen")
    }

    func testTheMenuOfARowBesideTheMessageOpenLeavesItOpen() async throws {
        let (app, server) = try await world()
        let mailbox = app.mailbox
        mailbox.chose([try row(app, 1)])
        let clicked = try row(app, 4)
        // The words are the row clicked's, unread, not those of the message open, which is read.
        try fire(try item("Mark as Read", in: RowMenu.items(for: mailbox, clicked: clicked)))
        await until { !server.asked(API.Post.Seen.path).isEmpty }
        XCTAssertEqual(app.selection.copy?.key, 2, "the message open stays open")
        XCTAssertEqual(mailbox.chosenRows.map(\.key), [2], "and its row chosen")
    }

    func testMoveToMovesTheRowClickedAndHasNoEmptyItem() async throws {
        let (app, server) = try await world()
        let mailbox = app.mailbox
        mailbox.chose([try row(app, 0)])
        let clicked = try row(app, 3)
        let move = try item("Move To", in: RowMenu.items(for: mailbox, clicked: clicked))
        let folders = try XCTUnwrap(move.submenu?.items)
        XCTAssertFalse(folders.contains { $0.isSeparatorItem }, "nothing between the folders and Other Folder…")
        XCTAssertFalse(folders.contains { $0.title.isEmpty }, "and no item without words")
        XCTAssertEqual(folders.last?.title, "Other Folder…")

        try fire(try XCTUnwrap(folders.first))
        await until { !server.asked(API.Post.Move.path).isEmpty }
        XCTAssertEqual(keys(server.asked(API.Post.Move.path)), [4], "the folder named moves the row clicked")
        XCTAssertEqual(app.selection.copy?.key, 1, "not the message open")

        try fire(try XCTUnwrap(folders.last))
        let ask = try XCTUnwrap(mailbox.moving)
        XCTAssertEqual(ask.rows?.map(\.key), [4], "Other Folder… asks where for the row clicked")
        mailbox.move(to: try XCTUnwrap(mailbox.destinations(ask).first))
        await until { server.asked(API.Post.Move.path).count == 2 }
        XCTAssertEqual(keys(server.asked(API.Post.Move.path)), [4, 4])
        XCTAssertEqual(app.selection.copy?.key, 1)
    }
}
