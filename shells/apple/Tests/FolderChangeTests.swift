import XCTest

/// Folders made from the Mac app, through the server's folder routes: New Folder in Move To makes the folder, then
/// moves the mail chosen into it; a refusal is said in the server's words and moves nothing.
@MainActor
final class FolderChangeTests: XCTestCase {
    private func opened(_ server: FakeServer) async -> MailApp {
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.place = .folder(account: Fixture.ada, folder: "INBOX")
        await until { app.list?.copy(at: 0) != nil }
        return app
    }

    func testNewFolderMakesItThenMovesTheMailChosenThere() async throws {
        let server = Fixture.server(grouped: false)
        server.on("POST", API.Post.FoldersCreate.path, API.FolderChanged(folder: "Tickets", ok: true))
        server.on("POST", API.Post.Move.path, API.Filed(msg: "ok", ok: true, status: nil, undoable: true))
        let app = await opened(server)
        let mailbox = app.mailbox
        mailbox.chose([try XCTUnwrap(app.list?.copy(at: 2))])
        let foldersRead = server.asked(API.Get.Folders.path).count
        let said = await mailbox.makeFolderAndMove("Tickets", ask: MoveAsk(account: Fixture.ada, from: "INBOX"))
        XCTAssertEqual(said, .done("Tickets"))
        let made = try JSONDecoder().decode(API.NewFolderBody.self,
                                            from: try XCTUnwrap(server.asked(API.Post.FoldersCreate.path).first).body)
        XCTAssertEqual(made, API.NewFolderBody(account: Fixture.ada, name: "Tickets"))
        XCTAssertGreaterThan(server.asked(API.Get.Folders.path).count, foldersRead, "the rail reads the new folder")
        let moved = try JSONDecoder().decode(API.MoveToBody.self,
                                             from: try XCTUnwrap(server.asked(API.Post.Move.path).first).body)
        XCTAssertEqual(moved, API.MoveToBody(folder: "INBOX", key: 3, to: "Tickets", uid: 3))
    }

    func testARefusalIsSaidInTheServersWordsAndMovesNothing() async throws {
        let server = Fixture.server(grouped: false)
        server.on("POST", API.Post.FoldersCreate.path) { _ in
            FakeServer.refused(409, "There’s a folder by that name already.")
        }
        let app = await opened(server)
        app.mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
        let said = await app.mailbox.makeFolderAndMove("Receipts", ask: MoveAsk(account: Fixture.ada, from: "INBOX"))
        XCTAssertEqual(said, .refused("There’s a folder by that name already."))
        XCTAssertTrue(server.asked(API.Post.Move.path).isEmpty)
    }

    func testTheFolderDialogComesBeforeMoveTo() {
        let move = MoveAsk(account: Fixture.ada, from: "INBOX")
        XCTAssertEqual(WindowDialog.shown(moving: move, folder: .create(move, name: "Tickets"), savingView: nil,
                                          sheet: nil, planned: nil, palette: false, keys: false),
                       .folder(.create(move, name: "Tickets")))
        XCTAssertEqual(WindowDialog.shown(moving: move, savingView: nil, sheet: nil, planned: nil, palette: false,
                                          keys: false), .move(move))
    }

    /// What Move To offers once a name is typed: the folders it matches, then a new folder by that name, headed as one.
    func testATypedEntryCanNameItsOwnHeading() {
        let folder = Entries.Entry(id: "Receipts", label: "Receipts") {}
        let typed: (String) -> Entries.Entry? = { words in
            Entries.Entry(id: "new", group: "New folder", label: "Make “\(words)” and move there") {}
        }
        let rows = Entries.rows("Rec", entries: [folder], starters: [], typed: typed)
        XCTAssertEqual(rows.map(\.entry.id), ["Receipts", "new"])
        XCTAssertEqual(rows.map(\.section), ["Commands", "New folder"])
    }
}
