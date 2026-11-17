import AppKit
import SwiftUI
import XCTest

/// The process opens one core over its data folder: the mail window, closed and opened again from the Dock, shows the
/// one already open, and two windows starting at once open it once.
@MainActor
final class OneCoreTests: XCTestCase {
    /// Guards only this test's empty database.
    private static let key = "6c1e9a3f5b7d0c2e4a6f8b1d3e5c7a9f0b2d4e6a8c1f3b5d7e9a2c4f6b8d0e1a"
    nonisolated(unsafe) private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appending(path: "mailmind-one-core-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: folder)
    }

    private var home: CoreHome {
        CoreHome(folder: folder.path(percentEncoded: false),
                 variables: ["MAILMIND_DB_KEY": Self.key, "MAILMIND_NOSYNC": "1"],
                 standIn: folder.appending(path: "keychain.json").path(percentEncoded: false))
    }

    func testEveryMailWindowShowsTheProcesssOneApp() {
        XCTAssertTrue(MailApp.shared === MailApp.shared, "the window takes the process's app, never one of its own")
    }

    func testTwoStartsAtOnceOpenOneCoreAndALaterStartOpensNone() async throws {
        let opened = Opened()
        let home = home
        let app = MailApp(home: { home }, open: { home in
            await opened.add()
            return try await Core.open(home)
        })
        async let first: Void = app.start()
        async let second: Void = app.start()
        _ = await (first, second)
        let once = await opened.count
        XCTAssertEqual(once, 1, "a second window's start leaves the opening to the first")
        XCTAssertEqual(app.stage, .open)
        XCTAssertEqual(app.folder?.lastPathComponent, folder.lastPathComponent, "the folder its log and Start Fresh use")
        // The window closed, and opened again from the Dock.
        await app.start()
        let still = await opened.count
        XCTAssertEqual(still, 1, "a window opened again shows the core already open")
        await app.core?.close()
    }

    /// The process's app over this test's core, open.
    private func opened() async -> MailApp {
        _ = NSApplication.shared
        let home = home
        let app = MailApp(home: { home }, open: { try await Core.open($0) })
        await app.start()
        XCTAssertEqual(app.stage, .open)
        return app
    }

    /// A status of the test's own, so the app's stays as it was.
    private func status() -> AppStatus {
        AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true }, badge: { _ in },
                                         announce: { _ in }))
    }

    /// The mail window over `app` as `MailWindow` draws it once its core is open, in a window that never shows.
    private func show(_ app: MailApp, _ status: AppStatus) -> NSWindow {
        let mail = MeadowFrame(app: app, mailbox: app.mailbox, status: status, lights: false) { ReaderPane(app: app) }
            .modifier(WindowAreas(app: app, status: status))
        let host = NSHostingView(rootView: mail.frame(width: 1180, height: 760))
        host.frame = CGRect(x: 0, y: 0, width: 1180, height: 760)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        window.layoutIfNeeded()
        return window
    }

    /// Whether every area of a window has hung its hooks on the mailbox: the desk's h, w, l, Ask and the brief, the
    /// pane's r and c and its double-click's own window, the shell's [ and the status's ?.
    private func hooked(_ mailbox: Mailbox) -> Bool {
        ["h", "w", "l", "r", "c", "[", "?"].allSatisfy { mailbox.keys[$0] != nil } && mailbox.asks != nil
            && mailbox.opensBrief != nil && mailbox.snoozes != nil && !mailbox.rowMenu.isEmpty
            && mailbox.opensWindow != nil
    }

    /// What the desk hooked on the mailbox reaches a desk that is on screen: Reply later (l) on the message open is
    /// said in the window's notice.
    private func deskAnswers(_ app: MailApp) async {
        app.mailbox.dismissToast()
        app.selection.copy = MailCopy.named(key: 101, folder: "INBOX", uid: 11, account: "ada@example.test")
        app.mailbox.keys["l"]?()
        await until { app.mailbox.toast != nil }
    }

    func testAWindowOpenedAgainFromTheDockHangsItsOwnHooks() async throws {
        let app = await opened()
        let status = status()
        let mailbox = app.mailbox
        let first = show(app, status)
        await until { hooked(mailbox) }

        // Closed with the red button: its desk, shell and pane go, and the hooks that reached them.
        first.contentView = nil
        await until { mailbox.keys.isEmpty && mailbox.asks == nil && mailbox.more.isEmpty }
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertTrue(mailbox.keys.isEmpty, "the closed window's views never hang theirs again")
        XCTAssertNil(mailbox.opensWindow, "a double-click opens no window through the closed one's pane")

        // Opened again from the Dock: the new window's areas join, and reach its desk and its notices.
        let second = show(app, status)
        await until { hooked(mailbox) }
        await deskAnswers(app)
        mailbox.keys["?"]?()
        XCTAssertTrue(status.showingKeys, "the status's ? shows the keyboard's legend")
        mailbox.opensBrief?(false)
        XCTAssertEqual(app.place, .desk(.brief), "the rail's brief opens in the new window")

        second.contentView = nil
        await app.core?.close()
    }

    func testAWindowStillOpenHangsItsHooksAgainWhenAnotherCloses() async throws {
        let app = await opened()
        let status = status()
        let mailbox = app.mailbox
        let first = show(app, status)
        await until { hooked(mailbox) }
        let second = show(app, status)
        try await Task.sleep(for: .milliseconds(100))

        // The first closes, and lets go of what it hung: the second's areas hang theirs.
        let round = mailbox.joinRound
        first.contentView = nil
        await until { mailbox.joinRound > round && hooked(mailbox) }
        await deskAnswers(app)

        second.contentView = nil
        await app.core?.close()
    }

    /// The brief's line counts the Inbox unread of the mailbox of the window the desk is in.
    func testTheDeskCountsTheWindowsInboxesForTheBriefsLine() async {
        let app = await opened()
        let desk = Desk()
        XCTAssertNil(desk.inboxesUnread, "in no window yet")
        desk.attach(app)
        app.mailbox.wire = Fixture.server().wire
        await app.mailbox.refresh()
        XCTAssertEqual(desk.inboxesUnread, 5, "Ada's 4 and Work's 1, as the rail counts them")
        await app.core?.close()
    }

    func testACoreThatDidntOpenSaysTheCoresOwnWords() async {
        let app = MailApp(home: { self.home }, open: { _ in
            throw HostProblem.NotOpened(why: "the database key doesn't open the database")
        })
        await app.start()
        XCTAssertEqual(app.stage, .failed("the database key doesn't open the database"),
                       "the core's words, never the name of a Swift type")
    }
}

private actor Opened {
    private(set) var count = 0

    func add() {
        count += 1
    }
}
