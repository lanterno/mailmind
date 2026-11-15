import AppKit
import SwiftUI
import XCTest

/// What floats over Meadow's window, drawn over the frame on fictional mail at the mockup's 1440 by 900: the palette,
/// an answer in it, a bulk action's preview, the snooze chooser and its time of one's own, a sender's card, the
/// status's menu, the keyboard's legend, the toasts and the outbox. Day's palette is drawn on every run and must
/// draw; with `TEST_RUNNER_MEADOW_PICTURES=<folder>` given to `xcodebuild test`, every light of each is written
/// there as `overlay-<light>-<what>.png`, to lay beside the mockup's `1440-*-palette.png`, `-ask`, `-bulk`,
/// `-snooze`, `-sync` and `-keys`.
@MainActor
final class MeadowOverlayPictures: XCTestCase {
    private static var folder: URL? {
        ProcessInfo.processInfo.environment["MEADOW_PICTURES"].map { URL(fileURLWithPath: $0) }
    }

    private static let size = CGSize(width: 1440, height: 900)
    private var suite = ""
    private var app: MailApp!
    private var desk: Desk!
    private var routes: DeskRoutes!
    private var status: AppStatus!
    private let anchors = MeadowAnchors()

    override func setUp() async throws {
        _ = NSApplication.shared
        suite = "meadow-overlays-\(UUID().uuidString)"
        app = MailApp()
        app.mailbox.wire = Fixture.server().wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.openList()
        await until { self.app.list?.copy(at: 0) != nil }
        app.mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
        routes = DeskRoutes()
        desk = Desk()
        desk.link.routes = routes
        status = Self.status()
    }

    override func tearDown() async throws {
        UserDefaults().removePersistentDomain(forName: suite)
    }

    /// Three fictional accounts, synced a few minutes ago.
    private static func status() -> AppStatus {
        let now = Date()
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        func ago(_ minutes: Double) -> String {
            ISO8601DateFormatter().string(from: now.addingTimeInterval(-minutes * 60))
        }
        let accounts = ["work@example.test", "home@example.test", "shop@example.test"]
        status.hold(digest: StatusFixtures.digest(accounts: accounts, aliases: [
                        "work@example.test": "Work", "home@example.test": "Personal", "shop@example.test": "Windbell",
                    ], last: ago(2)),
                    accounts: accounts.map { StatusFixtures.account($0) },
                    health: [StatusFixtures.standing(accounts[0], ok: ago(2), count: 40),
                             StatusFixtures.standing(accounts[1], ok: ago(3), count: 12),
                             StatusFixtures.standing(accounts[2], ok: ago(6), count: 5)],
                    preferences: StatusFixtures.preferences(), now: now)
        return status
    }

    /// The frame as the window draws it, its first message open under the pane's bar.
    private func frame() throws -> some View {
        let shell = MeadowShell(defaults: try XCTUnwrap(UserDefaults(suiteName: suite)))
        shell.railOpen = true
        return MeadowFrame(app: app, mailbox: app.mailbox, shell: shell, status: status, lights: false) {
            VStack(spacing: 0) {
                MeadowPaneBar(app: app, mailbox: app.mailbox, writing: PaneWriting(replyTo: app.selection.copy))
                Spacer(minLength: 0)
            }
        }
        .environment(desk)
        .environment(\.meadowAnchors, anchors)
    }

    /// The frame under a dialog: blurred, as the window blurs it, and the dialog on its scrim.
    private func dialog(_ content: some View, place: MeadowDialogPlace = .palette) throws -> some View {
        try frame()
            .blur(radius: 2)
            .overlay { MeadowDialog(place: place, close: {}) { content } }
    }

    /// The frame with a popover beside the button it hangs from, where the window's panel would stand.
    private func popover(under anchor: CGRect?, _ pop: some View) throws -> some View {
        let size = NSHostingView(rootView: pop.meadowLight(.day)).fittingSize
        let at = MeadowPopPlacement.origin(of: size, under: anchor, in: Self.size)
        return try frame()
            .overlay(alignment: .topLeading) { pop.fixedSize().offset(x: at.x, y: at.y) }
    }

    private var modes: [Entries.Mode] { desk.paletteModes(undoKey: "Z") }

    /// A sender who writes to two of the accounts.
    private static let card = API.ContactCard(
        accounts: [Fixture.ada, Fixture.work], addr: "aoi@example.test", count: 380, firstAgo: "4 years",
        known: true, lastAgo: "2 days", name: "Aoi Nakamura", ok: true, recent: [
            API.RecentMail(account: Fixture.ada, folder: "INBOX", key: 1, seen: false,
                           subject: "Lunch on Saturday?", uid: 11, when: "9:12 am"),
            API.RecentMail(account: Fixture.work, folder: "INBOX", key: 3, seen: true,
                           subject: "Re: The guild dinner", uid: 13, when: "Oct 28"),
        ], replied: 120)

    func testThePalette() async throws {
        for light in lights {
            try await draw("palette", light, try dialog(PaletteSheet(mailbox: app.mailbox, modes: modes, close: {})))
        }
    }

    func testAnAnswerAndABulkActionInThePalette() async throws {
        guard Self.folder != nil else { return }
        routes.answer("POST", "/api/ai/ask", DeskFixture.answered)
        await desk.asking.ask("when is the dentist appointment")
        routes.answer("POST", "/api/ai/do-plan", API.AiDoPlanAnswer.planPreview(DeskFixture.preview(.archive)))
        await desk.plan.plan("archive newsletters older than a week")
        for light in lights {
            try await draw("ask", light, try dialog(PaletteSheet(
                mailbox: app.mailbox, initial: "? when is the dentist appointment", modes: modes, close: {})))
            try await draw("bulk", light, try dialog(PaletteSheet(
                mailbox: app.mailbox, initial: "! archive newsletters older than a week", modes: modes, close: {})))
        }
    }

    func testMoveToSaveViewAndTheKeys() async throws {
        guard Self.folder != nil else { return }
        for light in lights {
            try await draw("move", light, try dialog(MoveToSheet(
                mailbox: app.mailbox, ask: MoveAsk(account: Fixture.ada, from: "INBOX"), close: {})))
            try await draw("save-view", light, try dialog(SaveViewSheet(
                mailbox: app.mailbox, query: "from:grace@example.test has:attachment", close: {}), place: .form))
            try await draw("keys", light, try dialog(KeysLegend(status: status, close: {}), place: .keys))
            try await draw("snooze-time", light, try dialog(SnoozeTimeDialog(
                copies: [DeskFixture.ada, DeskFixture.grace], model: desk.attention, close: {}), place: .form))
            try await draw("contact", light, try dialog(ContactCard(
                mailbox: app.mailbox, address: Self.card.addr, card: Self.card, close: {}), place: .card))
        }
    }

    func testThePopovers() async throws {
        guard Self.folder != nil else { return }
        // Where the pane's Snooze stands, once the frame has laid out.
        var snooze: CGRect?
        try await draw("frame", .day, try frame()) { snooze = anchors.frames["snooze"] }
        XCTAssertNotNil(snooze, "the pane's Snooze says where it stands")
        let menu = MeadowMenuModel()
        // Where the status pill stands in the top bar, its words "Synced" and the time.
        let pill = CGRect(x: 1099, y: 11, width: 108, height: 34)
        for light in lights {
            menu.at = 0
            try await draw("snooze", light, try popover(under: snooze, SnoozeChooser(
                copies: [DeskFixture.ada], model: desk.attention, menu: menu, close: {})))
            try await draw("sync", light, try popover(under: pill, StatusMenu(
                status: status, model: menu, settings: {}, close: {})))
        }
    }

    func testTheToastsAndTheOutbox() async throws {
        guard Self.folder != nil else { return }
        let outbox = FakeRoutes()
        outbox.answer("/api/outbox", #"""
            {"ok": true, "items": [
              {"id": 1, "account": "work@example.test", "to": ["bo@example.test"], "cc": [], "bcc_count": 0,
               "subject": "The guild dinner", "state": "queued", "attempts": 0, "next_attempt_at": 1900000000,
               "scheduled": true, "retrying": false, "unsure": false, "created_at": "2026-11-02 12:00:00"},
              {"id": 2, "account": "work@example.test", "to": ["dee@example.test"], "cc": [], "bcc_count": 1,
               "subject": "Minutes", "state": "error", "error": "The server said no", "attempts": 3,
               "next_attempt_at": 0, "scheduled": false, "retrying": false, "unsure": false,
               "created_at": "2026-11-02 12:00:00"}],
             "drafts": []}
            """#)
        outbox.answer("/api/pending", #"{"ok": true, "items": [], "waiting": 0, "stuck": 0}"#)
        let tray = OutboxTray(routes: outbox) { _ in }
        await tray.load()
        app.mailbox.say("Archived", undo: "Archived")
        let toasts = VStack(spacing: 8) {
            MeadowToast(icon: .send, text: Text("Sending in \(Text(verbatim: "9s").monospacedDigit())")) {
                MeadowToastButton(title: "Undo", icon: .undo) {}
            }
            if let toast = app.mailbox.toast { MailboxToast(mailbox: app.mailbox, toast: toast) }
        }
        let button = CGRect(x: 1150, y: 11, width: 60, height: 34)
        for light in lights {
            try await draw("toasts", light, try frame().overlay(alignment: .bottom) { toasts.padding(.bottom, 20) })
            try await draw("outbox", light, try popover(under: button, OutboxList(tray: tray, readOnly: false)))
        }
    }

    private var lights: [MeadowLight] { Self.folder == nil ? [.day] : MeadowLight.allCases }

    /// Draw `view` in `light` in a window of its own, check it drew, and write it when asked; `drawn` runs while the
    /// view is still in its window, before what it remembered there is forgotten.
    private func draw(_ name: String, _ light: MeadowLight, _ view: some View,
                      drawn: () -> Void = {}) async throws {
        let host = NSHostingView(rootView: view.meadowLight(light).frame(width: Self.size.width,
                                                                          height: Self.size.height))
        host.frame = CGRect(origin: .zero, size: Self.size)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.appearance = NSAppearance(named: light.isDark ? .darkAqua : .aqua)
        window.contentView = host
        window.layoutIfNeeded()
        try await Task.sleep(for: .milliseconds(400))
        host.layoutSubtreeIfNeeded()
        host.displayIfNeeded()
        let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: bitmap)
        XCTAssertNotNil(bitmap.colorAt(x: Int(Self.size.width / 2), y: 150), name)
        drawn()
        window.contentView = nil
        guard let folder = Self.folder else { return }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
        try png.write(to: folder.appendingPathComponent("overlay-\(light.rawValue)-\(name).png"))
    }
}
