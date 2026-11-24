import AppKit
import Foundation
import SwiftUI
import XCTest

/// A cleanup done in Curate, taken back from Curate's own sheet: its Edit ▸ Undo, and the toast's Undo, as long as the
/// window's other Undo toasts stay.
@MainActor
final class CurateUndoTests: XCTestCase {
    private func opened(_ server: FakeServer) async -> MailApp {
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.place = .folder(account: Fixture.ada, folder: "INBOX")
        await until { app.list?.copy(at: 0) != nil }
        return app
    }

    private static let cleaned = API.CleanedUp(
        blockScope: .sender, blockedToken: "news@shop.example.test", method: "one-click", ok: true,
        reversible: API.Reversible(block: true, sweep: true, unsubscribe: false), status: "unsubscribed_oneclick",
        sweepCapped: false, sweepFailed: 0, sweepQueued: 0, swept: 31, undoGroup: "ug-1")

    /// Edit ▸ Undo on the sheet takes the cleanup back through the mail window's Edit ▸ Undo, once between them: after
    /// the toast's Undo took it, the sheet's undoes nothing older.
    func testTheSheetsEditUndoTakesACleanupBackOnce() async throws {
        let server = Fixture.server(grouped: false)
        server.on("GET", API.Get.CleanupSender.path, DeskFixture.senderPreview)
        server.on("POST", API.Post.CleanupExecute.path, Self.cleaned)
        server.on("GET", API.Get.CleanupSenders.path,
                  API.NoisySenders(generated: "2026-11-15T09:00:00Z", ok: true, senders: []))
        server.on("POST", API.Post.Undo.path, API.Undone(label: "Clean Up", ok: true, remaining: 0, reversed: 31))
        let app = await opened(server)
        let desk = Desk()
        desk.use(app, routes: server.wire)
        let window = UndoManager(), sheet = UndoManager()
        window.groupsByEvent = false
        sheet.groupsByEvent = false
        app.mailbox.undoManager = window
        desk.curate.sheetUndo = sheet

        func cleanUp() async {
            await desk.curate.previewCleanup("news@shop.example.test")
            await desk.curate.pressCleanup()
            window.beginUndoGrouping()
            sheet.beginUndoGrouping()
            await desk.curate.pressCleanup()
            sheet.endUndoGrouping()
            window.endUndoGrouping()
        }

        await cleanUp()
        XCTAssertEqual(sheet.undoActionName, "Clean Up")
        sheet.undo()
        await until { server.asked(API.Post.Undo.path).count == 1 }
        XCTAssertFalse(window.canUndo, "taken back through the window's Edit ▸ Undo")

        await cleanUp()
        app.mailbox.undo()   // the toast's Undo
        await until { server.asked(API.Post.Undo.path).count == 2 }
        XCTAssertTrue(sheet.canUndo, "the sheet's entry is still there")
        sheet.undo()
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(server.asked(API.Post.Undo.path).count, 2, "nothing older is undone")
        withExtendedLifetime(desk) {}
    }

    /// Curate's sheet hands its model the undo manager of the window it is drawn in, which a sheet's own window's Edit
    /// ▸ Undo asks while it is in front.
    func testTheSheetHandsItsModelItsWindowsUndoManager() async throws {
        let routes = DeskRoutes()
        let model = CurateModel(link: CoreLink(routes), notices: DeskNotices())
        let view = NSHostingView(rootView: CurateSheet(model: model, nameOf: { $0 }, open: { _ in }).meadowLight(.day))
        view.frame = CGRect(x: 0, y: 0, width: 620, height: 600)
        let window = NSWindow(contentRect: view.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = view
        defer { window.contentView = nil }
        for _ in 0..<50 where model.sheetUndo == nil {
            view.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(10))
        }
        let undo = try XCTUnwrap(model.sheetUndo)
        XCTAssertTrue(undo === window.undoManager)
    }
}
