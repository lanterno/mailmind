import XCTest

/// Renaming and deleting a folder from the rail's menu, through the server's folder routes: only the folders the
/// server says a person may change are offered; a rename starts from the folder's own name and keeps the window on
/// it; a folder that holds mail is deleted only after a second yes, its mail going to Trash first, or on Gmail staying
/// in All Mail.
@MainActor
final class FolderRailTests: XCTestCase {
    private let receipts = Fixture.folder("Receipts", role: "custom")

    private func opened(_ server: FakeServer, place: Place) async -> MailApp {
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.place = place
        await until { app.list?.copy(at: 0) != nil }
        return app
    }

    /// The server's 409 for a folder that holds mail, with how many, and whether it goes to Trash.
    nonisolated private static func holds(_ count: Int, toTrash: Bool = true) -> CoreAnswer {
        conflict(toTrash ? "This folder holds \(count) messages. Deleting it moves them to Trash first."
                     : "This folder holds \(count) messages. Deleting it leaves them in All Mail.",
                 ["messages": .number(Double(count)), "to_trash": .bool(toTrash)])
    }

    nonisolated private static func conflict(_ detail: String, _ more: [String: JSONValue]) -> CoreAnswer {
        let fields: [String: JSONValue] = [
            "type": .string("/problems/conflict"), "title": .string("Conflict"), "status": .number(409),
            "detail": .string(detail),
        ].merging(more) { kept, _ in kept }
        return CoreAnswer(status: 409, contentType: "application/problem+json", headers: [],
                          body: try! JSONEncoder().encode(JSONValue.object(fields)))
    }

    func testOnlyTheFoldersTheServerSaysAPersonMayChangeAreOffered() async {
        let server = Fixture.server()
        let starred = Fixture.folder("[Gmail]/Starred", role: "custom", changeable: false)
        server.on("GET", API.Get.Folders.path, API.Folders(account: Fixture.ada, folders: Fixture.adaFolders + [starred],
                                                            muted: 0, ok: true, replylater: 0, snoozed: 0, waiting: 0))
        let app = await opened(server, place: .folder(account: Fixture.ada, folder: "INBOX"))
        let mailbox = app.mailbox
        XCTAssertEqual(mailbox.changeableFolder(.folder(account: Fixture.ada, folder: "Receipts"))?.folder, receipts)
        for name in ["INBOX", "Sent", "Trash", "Archive", "[Gmail]/Starred"] {
            XCTAssertNil(mailbox.changeableFolder(.folder(account: Fixture.ada, folder: name)), name)
        }
        XCTAssertNil(mailbox.changeableFolder(.folder(account: Fixture.ada, folder: "Gone")))
        XCTAssertNil(mailbox.changeableFolder(.allInboxes))
    }

    func testARenameKeepsTheWindowOnTheFolder() async throws {
        let server = Fixture.server()
        server.on("POST", API.Post.FoldersRename.path, API.FolderChanged(folder: "Paid", ok: true))
        let app = await opened(server, place: .folder(account: Fixture.ada, folder: "Receipts"))
        let said = await app.mailbox.renameFolder(receipts, to: "Paid", account: Fixture.ada)
        XCTAssertEqual(said, .done("Paid"))
        let body = try JSONDecoder().decode(API.RenameFolderBody.self,
                                            from: try XCTUnwrap(server.asked(API.Post.FoldersRename.path).first).body)
        XCTAssertEqual(body, API.RenameFolderBody(account: Fixture.ada, folder: "Receipts", name: "Paid"))
        XCTAssertEqual(app.place, .folder(account: Fixture.ada, folder: "Paid"))
        XCTAssertEqual(app.mailbox.toast?.text, "Renamed to Paid")
    }

    func testAFolderThatHoldsMailIsDeletedOnlyAfterASecondYes() async throws {
        let server = Fixture.server()
        server.on("POST", API.Post.FoldersDelete.path) { asked in
            let body = try? JSONDecoder().decode(API.DeleteFolderBody.self, from: asked.body)
            return body?.confirm == true ? FakeServer.ok(API.FolderDeleted(ok: true, trashed: 3)) : Self.holds(3)
        }
        let app = await opened(server, place: .folder(account: Fixture.ada, folder: "Receipts"))
        let mailbox = app.mailbox
        let asked = await mailbox.deleteFolder(receipts, account: Fixture.ada, confirm: false)
        XCTAssertEqual(asked, .holds(3, "This folder holds 3 messages. Deleting it moves them to Trash first.",
                                     toTrash: true))
        XCTAssertEqual(app.place, .folder(account: Fixture.ada, folder: "Receipts"), "nothing changed yet")
        let done = await mailbox.deleteFolder(receipts, account: Fixture.ada, confirm: true)
        XCTAssertEqual(done, .done("Receipts"))
        let bodies = try server.asked(API.Post.FoldersDelete.path).map {
            try JSONDecoder().decode(API.DeleteFolderBody.self, from: $0.body)
        }
        XCTAssertEqual(bodies, [API.DeleteFolderBody(account: Fixture.ada, folder: "Receipts"),
                                API.DeleteFolderBody(account: Fixture.ada, confirm: true, folder: "Receipts")])
        XCTAssertEqual(app.place, .folder(account: Fixture.ada, folder: "INBOX"), "the window goes to the Inbox")
        XCTAssertEqual(mailbox.toast?.text, "Moved 3 to Trash and deleted Receipts")
    }

    func testAGmailLabelThatHoldsMailSaysItsMailStays() async {
        let server = Fixture.server()
        server.on("POST", API.Post.FoldersDelete.path) { _ in Self.holds(2, toTrash: false) }
        let app = await opened(server, place: .folder(account: Fixture.ada, folder: "Receipts"))
        let asked = await app.mailbox.deleteFolder(receipts, account: Fixture.ada, confirm: false)
        XCTAssertEqual(asked, .holds(2, "This folder holds 2 messages. Deleting it leaves them in All Mail.",
                                     toTrash: false))
        XCTAssertEqual(FolderSheet.deleteAction(nil), "Delete")
        XCTAssertEqual(FolderSheet.deleteAction(true), "Move to Trash and Delete")
        XCTAssertEqual(FolderSheet.deleteAction(false), "Delete Folder")
    }

    /// Mail went to Trash and then the server kept the folder: its words stand, and the folders are read again.
    func testADeleteStoppedHalfwayReadsTheFoldersAgain() async {
        let server = Fixture.server()
        server.on("POST", API.Post.FoldersDelete.path) { _ in
            Self.conflict("Moved 3 messages to Trash, but the server kept the folder.", ["trashed": .number(3)])
        }
        let app = await opened(server, place: .folder(account: Fixture.ada, folder: "Receipts"))
        let read = server.asked(API.Get.Folders.path).count
        let said = await app.mailbox.deleteFolder(receipts, account: Fixture.ada, confirm: true)
        XCTAssertEqual(said, .refused("Moved 3 messages to Trash, but the server kept the folder."))
        XCTAssertGreaterThan(server.asked(API.Get.Folders.path).count, read)
    }

    func testARefusalIsSaidInTheServersWords() async {
        let server = Fixture.server()
        server.on("POST", API.Post.FoldersDelete.path) { _ in
            FakeServer.refused(409, "This folder has folders inside it. Delete or move those first.")
        }
        let app = await opened(server, place: .folder(account: Fixture.ada, folder: "INBOX"))
        let said = await app.mailbox.deleteFolder(receipts, account: Fixture.ada, confirm: false)
        XCTAssertEqual(said, .refused("This folder has folders inside it. Delete or move those first."))
    }

    func testARenameStartsFromTheFoldersOwnNameAsAPersonReadsIt() {
        let named = API.RailFolder(changeable: true, name: "Projects.R&-D", ownName: "R&D", role: "custom",
                                   takesFiling: true, total: 0, unseen: 0)
        XCTAssertEqual(FolderSheet.startingName(.rename(account: Fixture.ada, folder: named)), "R&D")
        let move = MoveAsk(account: Fixture.ada, from: "INBOX")
        XCTAssertEqual(FolderSheet.startingName(.create(move, name: "Tickets")), "Tickets")
        XCTAssertEqual(FolderSheet.startingName(.delete(account: Fixture.ada, folder: named)), "")
        XCTAssertEqual(WindowDialog.shown(moving: nil, folder: .delete(account: Fixture.ada, folder: receipts),
                                          savingView: nil, sheet: nil, planned: nil, palette: true, keys: false),
                       .folder(.delete(account: Fixture.ada, folder: receipts)))
    }
}
