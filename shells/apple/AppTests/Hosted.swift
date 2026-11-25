import AppKit
import XCTest
@testable import MailMind

/// What every hosted test stands on: the app itself as the host, open on the fictional home `just apple test-hosted`
/// names (AppTests/hosted_home.py), active, with its mail window key and the inbox's rows in its list. Each test then
/// acts as a person would, through the real menu bar, the real key window and AppKit's own routing.
///
/// A run that named no home has an app that opened nothing (`NoHomeNamed`): every test fails here, saying so, rather
/// than skip, so a hosted run never passes on nothing.
@MainActor
class HostedCase: XCTestCase {
    /// The app's one mail window, key.
    private(set) var window: NSWindow!
    /// Its list: the mailbox's table.
    private(set) var table: MailboxTableView!
    var app: MailApp { MailApp.shared }
    var mailbox: Mailbox { MailApp.shared.mailbox }

    override func setUp() async throws {
        try await super.setUp()
        try onTheTestHome()
        try await until("the core opened", within: 60) {
            if case .failed(let why) = MailApp.shared.stage { throw KitError("MailMind didn't open: \(why)") }
            return MailApp.shared.stage == .open
        }
        // A fresh home's mail has no AI verdicts yet, so nothing needs you: the Inbox shows Everything.
        mailbox.tab = .all
        try await until("the mail window", within: 30) { Self.mailWindow() != nil }
        window = Self.mailWindow()
        try await frontAndKey()
        table = try XCTUnwrap(Self.table(in: window))
        try await until("the inbox's rows", within: 90) { self.table.numberOfRows > 0 }
        // Nothing of a test before stands over the window.
        mailbox.paletteOpen = false
        AppStatus.shared.showingKeys = false
        mailbox.chose([])
        try await settle()
    }

    /// The home this run opened is the one hosted_home.py named, a temp folder, never a person's.
    private func onTheTestHome() throws {
        let environment = ProcessInfo.processInfo.environment
        guard let home = environment["MAILMIND_HOME"], !home.isEmpty else {
            throw KitError("no MAILMIND_HOME: run the hosted tests through `just apple test-hosted`, which names a "
                + "temp home on fictional mail")
        }
        XCTAssertNotNil(environment["MAILMIND_TEST_KEYCHAIN"], "the keychain's stand-in")
        XCTAssertNotNil(environment["MAILMIND_DB_KEY"], "a key made for the run")
        XCTAssertFalse(CoreHome.isAPersonsRun(environment))
        if let folder = MailApp.shared.folder {
            XCTAssertEqual(folder.standardizedFileURL.path, URL(filePath: home).standardizedFileURL.path)
        }
    }

    /// The app in front and the mail window key, as a person's click on it leaves them.
    func frontAndKey() async throws {
        Self.activate()
        window.makeKeyAndOrderFront(nil)
        do {
            try await until("the app active and the mail window key", within: 20) {
                if !NSApp.isActive { Self.activate() }
                if !self.window.isKeyWindow { self.window.makeKeyAndOrderFront(nil) }
                return NSApp.isActive && self.window.isKeyWindow
            }
        } catch {
            throw KitError("\(error): \(Self.whereFocusIs(self.window))")
        }
    }

    /// Who has the screen and the keyboard, for a failure to say: the app in front, the key window, the mail window.
    static func whereFocusIs(_ window: NSWindow) -> String {
        let front = NSWorkspace.shared.frontmostApplication
        let windows = NSApp.windows.filter(\.isVisible).map { describe($0) }
        return "in front: \(front?.bundleIdentifier ?? front?.localizedName ?? "none") (pid \(front?.processIdentifier ?? 0));"
            + " this app active: \(NSApp.isActive), pid \(ProcessInfo.processInfo.processIdentifier);"
            + " key: \(NSApp.keyWindow.map { describe($0) } ?? "none"); main: \(NSApp.mainWindow.map { describe($0) } ?? "none");"
            + " mail window: \(describe(window)), visible \(window.isVisible), on screen \(window.occlusionState.contains(.visible)),"
            + " can be key \(window.canBecomeKey); modal: \(NSApp.modalWindow.map { describe($0) } ?? "none"); windows: \(windows)"
    }

    /// A window as a failure names it: its class, identifier, title and whether it can close.
    static func describe(_ window: NSWindow) -> String {
        "\(type(of: window)) #\(window.identifier?.rawValue ?? "-") \"\(window.title)\""
            + (window.styleMask.contains(.closable) ? " closable" : "") + (window.isKeyWindow ? " key" : "")
    }

    /// The app made active, over whatever is in front where it must be: the runner's session has no person in it to
    /// mind, and macOS 14's cooperative `activate()` may leave another app in front.
    private static func activate() {
        NSApp.activate()
        if !NSApp.isActive { NSApp.activate(ignoringOtherApps: true) }
    }

    /// The window the mail scene opened: the one that holds the mailbox's table.
    static func mailWindow() -> NSWindow? {
        NSApp.windows.first { $0.isVisible && table(in: $0) != nil }
    }

    static func table(in window: NSWindow) -> MailboxTableView? {
        func find(_ view: NSView?) -> MailboxTableView? {
            guard let view else { return nil }
            if let table = view as? MailboxTableView { return table }
            for child in view.subviews {
                if let found = find(child) { return found }
            }
            return nil
        }
        return find(window.contentView?.superview ?? window.contentView)
    }

    /// Waits, letting the app run, until `condition` holds; fails saying what never came.
    func until(_ what: String, within seconds: Double = 10, _ condition: @MainActor () throws -> Bool) async throws {
        let end = Date(timeIntervalSinceNow: seconds)
        while Date() < end {
            if try condition() { return }
            try await Task.sleep(for: .milliseconds(50))
        }
        if try condition() { return }
        throw KitError("\(what) never came in \(Int(seconds)) s")
    }

    /// A moment for SwiftUI to draw again and hand the menus what changed.
    func settle() async throws {
        try await Task.sleep(for: .milliseconds(400))
    }

    /// The list's first message chosen by a click on it, as a person chooses one: on its row's text, below any day's
    /// head and off its buttons and check box. The brief's line over the list, where there is one, isn't mail.
    func clickFirstRow() async throws {
        let (cell, point) = try firstMailRow()
        try RealMouse.click(at: point, in: cell)
        try await until("a row chosen") { !self.mailbox.chosenRows.isEmpty }
        try await settle()
    }

    /// The first row of mail the list shows, and a point in it that a click chooses the row at.
    func firstMailRow() throws -> (MailRowCell, CGPoint) {
        for row in 0..<table.numberOfRows {
            guard let cell = table.view(atColumn: 0, row: row, makeIfNecessary: false) as? MailRowCell else {
                continue
            }
            let bounds = cell.bounds
            let y = cell.isFlipped ? bounds.maxY - 8 : bounds.minY + 8
            for x in [0.45, 0.55, 0.35, 0.65].map({ bounds.minX + bounds.width * $0 }) {
                let point = CGPoint(x: x, y: y)
                if !cell.inHead(point), cell.target(at: point) == nil { return (cell, point) }
            }
        }
        throw KitError("no row of mail in the list to click (\(table.numberOfRows) rows)")
    }
}
