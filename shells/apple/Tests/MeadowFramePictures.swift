import AppKit
import SwiftUI
import XCTest

/// Meadow's window on fictional mail at the mockup's 1440 by 900: the top bar, and the frame with the rail folded and
/// open. Day's folded frame is drawn on every run and must draw; with `TEST_RUNNER_MEADOW_PICTURES=<folder>` given to
/// `xcodebuild test`, every light is drawn and each is written there, to lay beside the mockup's
/// `1440-*-read.png` and `1440-*-rail-open.png`.
@MainActor
final class MeadowFramePictures: XCTestCase {
    private static var folder: URL? {
        ProcessInfo.processInfo.environment["MEADOW_PICTURES"].map { URL(fileURLWithPath: $0) }
    }

    private var suite = ""

    override func setUp() async throws {
        _ = NSApplication.shared
        suite = "meadow-frame-\(UUID().uuidString)"
    }

    override func tearDown() async throws {
        UserDefaults().removePersistentDomain(forName: suite)
    }

    /// The Inbox's Everything on the fixture's mail, its first message open.
    private func opened() async throws -> MailApp {
        let app = MailApp()
        app.mailbox.wire = Fixture.server().wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.openList()
        await until { app.list?.copy(at: 0) != nil }
        app.mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
        return app
    }

    /// Synced two minutes ago, with the fixture's preferences.
    private func status() -> AppStatus {
        let now = Date()
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        let last = ISO8601DateFormatter().string(from: now.addingTimeInterval(-120))
        status.hold(digest: StatusFixtures.digest(accounts: ["ada@example.test"], last: last),
                    preferences: StatusFixtures.preferences(), now: now)
        return status
    }

    /// The pane as far as the frame draws it: its bar over the open message, on paper.
    private func pane(_ app: MailApp) -> some View {
        VStack(spacing: 0) {
            MeadowPaneBar(app: app, mailbox: app.mailbox, writing: PaneWriting(replyTo: app.selection.copy))
            Spacer(minLength: 0)
        }
    }

    func testTheFrameInEachLight() async throws {
        let app = try await opened()
        let status = status()
        let lights: [MeadowLight] = Self.folder == nil ? [.day] : MeadowLight.allCases
        for light in lights {
            for railOpen in Self.folder == nil ? [false] : [false, true] {
                let shell = MeadowShell(defaults: try XCTUnwrap(UserDefaults(suiteName: suite)))
                shell.railOpen = railOpen
                let frame = MeadowFrame(app: app, mailbox: app.mailbox, shell: shell, status: status, lights: false) {
                    pane(app)
                }
                .environment(Desk())
                try await draw("frame-\(light.rawValue)\(railOpen ? "-rail-open" : "")", light: light,
                               size: CGSize(width: 1440, height: 900), frame)
            }
        }
    }

    /// A narrower window (the mockup's 1180, where the pane's bar is icons), focus reading, and under 1100 the drawer.
    func testTheFrameNarrowFocusedAndWithTheDrawer() async throws {
        guard Self.folder != nil else { return }
        let app = try await opened()
        let status = status()
        func frame(_ shell: MeadowShell) -> some View {
            MeadowFrame(app: app, mailbox: app.mailbox, shell: shell, status: status, lights: false) { pane(app) }
                .environment(Desk())
        }
        for light in MeadowLight.allCases {
            let shell = MeadowShell(defaults: try XCTUnwrap(UserDefaults(suiteName: suite)))
            shell.railOpen = false
            try await draw("frame-\(light.rawValue)-1180", light: light, size: CGSize(width: 1180, height: 760),
                           frame(shell))
        }
        let focused = MeadowShell(defaults: try XCTUnwrap(UserDefaults(suiteName: suite)))
        focused.focus = true
        try await draw("frame-day-focus", light: .day, size: CGSize(width: 1440, height: 900), frame(focused))
        let drawer = MeadowShell(defaults: try XCTUnwrap(UserDefaults(suiteName: suite)))
        drawer.width = 1000
        drawer.drawerOpen = true
        try await draw("frame-day-drawer", light: .day, size: CGSize(width: 1000, height: 700), frame(drawer))
    }

    func testTheTopBarInEachLight() async throws {
        guard Self.folder != nil else { return }
        let app = try await opened()
        let status = status()
        for light in MeadowLight.allCases {
            let shell = MeadowShell(defaults: try XCTUnwrap(UserDefaults(suiteName: suite)))
            let bar = MeadowTopBar(app: app, mailbox: app.mailbox, shell: shell, status: status, lights: 0)
                .background { MeadowSky() }
            try await draw("top-\(light.rawValue)", light: light, size: CGSize(width: 1440, height: 56), bar)
        }
    }

    /// Draw `view` in `light` at `size` in a window of its own, check it drew, and write it when asked.
    private func draw(_ name: String, light: MeadowLight, size: CGSize, _ view: some View) async throws {
        let host = NSHostingView(rootView: view.meadowLight(light).frame(width: size.width, height: size.height))
        host.frame = CGRect(origin: .zero, size: size)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.appearance = NSAppearance(named: light.isDark ? .darkAqua : .aqua)
        window.contentView = host
        window.layoutIfNeeded()
        try await Task.sleep(for: .milliseconds(400))
        host.layoutSubtreeIfNeeded()
        host.displayIfNeeded()
        let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: bitmap)
        XCTAssertTrue(Self.hasInk(bitmap), name)
        window.contentView = nil
        guard let folder = Self.folder else { return }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
        try png.write(to: folder.appendingPathComponent("\(name).png"))
    }

    /// Whether a picture has more than one colour: a pixel in its middle differs from its corner's.
    private static func hasInk(_ bitmap: NSBitmapImageRep) -> Bool {
        guard let corner = bitmap.colorAt(x: 0, y: 0) else { return false }
        for y in stride(from: 0, to: bitmap.pixelsHigh, by: 7) {
            for x in stride(from: 0, to: bitmap.pixelsWide, by: 7) where bitmap.colorAt(x: x, y: y) != corner {
                return true
            }
        }
        return false
    }
}
