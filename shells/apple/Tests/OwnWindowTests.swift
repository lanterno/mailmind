import AppKit
import PDFKit
import SwiftUI
import XCTest

/// A message in a window of its own shows what the pane's reader shows for it: the cards the desk puts in a reader.
@MainActor
final class OwnWindowTests: XCTestCase {
    private static let key = "5c7e9a1b3d5f7a9c2e4b6d8f0a1c3e5b7d9f2a4c6e8b0d1f3a5c7e9b2d4f6a8c"
    private var folder: URL!
    private var core: Core?
    private var app: MailApp?
    private var desk: Desk?

    override func setUp() async throws {
        _ = NSApplication.shared
        folder = FileManager.default.temporaryDirectory.appending(path: "mailmind-own-window-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    override func tearDown() async throws {
        if let core { await core.close() }
        core = nil
        app = nil
        desk = nil
        try? FileManager.default.removeItem(at: folder)
    }

    /// A core over an empty home of the test's own: the frame's scheme answers through it, and asks it nothing here.
    private func openCore() async throws -> Core {
        let core = try await Core.open(CoreHome(folder: folder.path(percentEncoded: false),
                                                variables: ["MAILMIND_DB_KEY": Self.key, "MAILMIND_NOSYNC": "1"],
                                                standIn: folder.appending(path: "keychain.json")
                                                    .path(percentEncoded: false)))
        self.core = core
        return core
    }

    /// The mailbox over the verbs' fake server, its Inbox read.
    private func mailbox(_ server: FakeServer = OpenedMessageVerbsTests.server()) async -> Mailbox {
        let app = MailApp()
        self.app = app
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        return app.mailbox
    }

    private static let copy = OpenedMessageVerbsTests.away

    /// The window's message, opened, with an invite in it.
    private static func invited() -> API.MessageOpened {
        var opened = OpenedMessageVerbsTests.head(copy, thread: nil)
        opened.invite = DeskFixture.invite
        return opened
    }

    /// What `view` says, drawn as a PDF 680 points wide, its lines joined.
    private static func words(_ view: some View) -> String {
        let data = NSMutableData()
        let renderer = ImageRenderer(content: view.frame(width: 680).meadowLight(.day))
        renderer.render { size, draw in
            var box = CGRect(origin: .zero, size: size)
            guard let consumer = CGDataConsumer(data: data as CFMutableData),
                  let pdf = CGContext(consumer: consumer, mediaBox: &box, nil) else { return }
            pdf.beginPDFPage(nil)
            draw(pdf)
            pdf.endPDFPage()
            pdf.closePDF()
        }
        return (PDFDocument(data: data as Data)?.string ?? "").split(whereSeparator: \.isNewline).joined(separator: " ")
    }

    // MARK: Cards

    /// The window stands inside the mail window's desk, as the pane's reader does, so the reader's card slot shows an
    /// invite's card with RSVP there; with no desk joined to the mailbox (no mail window), nothing lends one.
    func testAnInviteInItsOwnWindowShowsItsCardAsThePaneDoes() async throws {
        let core = try await openCore()
        let mailbox = await mailbox()
        let context = ReaderContext(core: core, copy: Self.copy, opened: Self.invited(), row: nil, conversation: nil)
        func shown() -> String {
            Self.words(MessageWindow.inDesk(of: mailbox, ReaderCards(placement: .message, context: context)))
        }
        XCTAssertFalse(shown().contains(DeskFixture.invite.summary), "no desk, no card")

        let desk = Desk()
        self.desk = desk
        desk.use(try XCTUnwrap(app), routes: DeskRoutes())
        let card = shown()
        XCTAssertTrue(card.contains(DeskFixture.invite.summary), "the invite's card: \(card)")
        XCTAssertTrue(card.contains("Accept") && card.contains("Decline"), "with its RSVP: \(card)")

        mailbox.leave()
        XCTAssertFalse(shown().contains(DeskFixture.invite.summary), "the mail window gone, its desk goes with it")
    }
}
