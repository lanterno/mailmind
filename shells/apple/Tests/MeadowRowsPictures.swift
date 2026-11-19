import AppKit
import SwiftUI
import XCTest

/// The rail and the list's rows as Meadow draws them, in Meadow's frame at the mockup's 1440 by 900 on fictional mail
/// like the mockup's: the rail folded and open, rows checked with the bulk bar, Trash under the pointer, an account
/// offline, each density, and an empty folder. One picture is drawn on every run and must draw; with
/// `TEST_RUNNER_MEADOW_PICTURES=<folder>` given to `xcodebuild test`, each is drawn in every light and written there
/// as `rows-*.png`, to lay beside the mockup's `1440-*-read.png`, `-rail-open`, `-select`, `-trash` and `-offline`.
@MainActor
final class MeadowRowsPictures: XCTestCase {
    private static var folder: URL? {
        ProcessInfo.processInfo.environment["MEADOW_PICTURES"].map { URL(fileURLWithPath: $0) }
    }

    private var suite = ""

    override func setUp() async throws {
        _ = NSApplication.shared
        suite = "meadow-rows-\(UUID().uuidString)"
    }

    override func tearDown() async throws {
        UserDefaults().removePersistentDomain(forName: suite)
        AppStatus.shared.hold()
    }

    func testTheRowsDraw() async throws {
        let (app, desk) = try await opened()
        try await draw("rows-day", light: .day, app: app, desk: desk)
    }

    func testTheRowsInEachLight() async throws {
        guard Self.folder != nil else { return }
        let (app, desk) = try await opened()
        // The first message open, as in the mockup.
        app.mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
        for light in MeadowLight.allCases {
            try await draw("rows-\(light.rawValue)", light: light, app: app, desk: desk)
            try await draw("rows-\(light.rawValue)-rail-open", light: light, app: app, desk: desk, railOpen: true)
        }
    }

    func testRowsCheckedTrashAndOffline() async throws {
        guard Self.folder != nil else { return }
        let (app, desk) = try await opened()
        let rows = try (0..<2).map { try XCTUnwrap(app.list?.copy(at: $0)) }
        app.mailbox.chose(rows)
        for light in MeadowLight.allCases {
            try await draw("rows-\(light.rawValue)-select", light: light, app: app, desk: desk)
        }
        app.mailbox.chose([])

        AppStatus.shared.hold(digest: StatusFixtures.digest(accounts: [Fixture.ada, Fixture.work],
                                                            status: [Fixture.ada: "error"]))
        for light in MeadowLight.allCases {
            try await draw("rows-\(light.rawValue)-offline", light: light, app: app, desk: desk)
        }
        AppStatus.shared.hold()

        app.place = .folder(account: Fixture.ada, folder: "Trash")
        await until { app.list?.copy(at: 0) != nil }
        for light in MeadowLight.allCases {
            try await draw("rows-\(light.rawValue)-trash", light: light, app: app, desk: desk, hover: 1)
        }
    }

    func testEachDensityAndAnEmptyFolder() async throws {
        guard Self.folder != nil else { return }
        let (app, desk) = try await opened()
        for density in API.Density.allCases {
            var preferences = Fixture.preferences().preferences
            preferences.density = density
            AppStatus.shared.hold(preferences: preferences)
            try await draw("rows-day-\(density.rawValue)", light: .day, app: app, desk: desk, hover: 2)
        }
        AppStatus.shared.hold()
        app.place = .folder(account: Fixture.ada, folder: "Receipts")
        await until { app.list?.firstRowsAt != nil }
        try await draw("rows-day-empty", light: .day, app: app, desk: desk)
    }

    /// The accounts' menu as the rail's button opens it, every account together chosen and the keyboard on it.
    func testTheAccountsMenu() async throws {
        guard let folder = Self.folder else { return }
        let (app, _) = try await opened()
        let menu = RailMenuModel()
        for light in MeadowLight.allCases {
            let palette = MeadowPalette.of(light)
            menu.offer(mailbox: app.mailbox, current: nil, colours: { account in
                let accounts = account.map { [$0] } ?? app.mailbox.accounts
                return accounts.map { palette.account(app.mailbox.accounts.firstIndex(of: $0) ?? 0) }
            }, choose: { _ in })
            menu.at = 0
            let view = RailAccountMenu(model: menu).padding(28).background(palette.paper).meadowLight(light)
            let host = NSHostingView(rootView: view)
            host.frame = CGRect(origin: .zero, size: host.fittingSize)
            host.layoutSubtreeIfNeeded()
            let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            host.cacheDisplay(in: host.bounds, to: bitmap)
            let png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try png.write(to: folder.appendingPathComponent("rows-\(light.rawValue)-accounts.png"))
        }
    }

    // MARK: The mail

    /// Mail like the mockup's, all fictional: three that need you today with the AI's summaries and next steps, then
    /// yesterday's and older, a receipt, a newsletter and junk.
    static func mail(now: Date = Date()) -> [API.FolderRow] {
        let today = Int(Calendar.current.startOfDay(for: now).timeIntervalSince1970)
        func row(_ key: Int, _ from: String, _ subject: String, account: String = Fixture.ada, folder: String = "INBOX",
                 seen: Bool = true, count: Int? = nil, attach: Bool = false, flagged: Bool = false, action: String = "",
                 summary: String = "", snippet: String, bucket: String? = nil, junk: Bool = false, at: Int,
                 when: String) -> API.FolderRow {
            API.FolderRow(account: account, action: action, bucket: bucket, commitmentWorthy: false, count: count,
                          flagged: flagged, folder: folder,
                          fromAddr: from.lowercased().replacingOccurrences(of: " ", with: ".") + "@example.test",
                          fromName: from, hasAttach: attach, isJunk: junk, key: key, needsYou: !action.isEmpty,
                          replyWorthy: !action.isEmpty, safeToFile: false, seen: seen, sentTo: [], sentToMore: 0,
                          snippet: snippet,
                          subject: subject, summary: summary, threadCount: count,
                          threadId: count.map { _ in "t\(key)" }, ts: at, uid: key, when: when)
        }
        return [
            row(1, "Ren Takeda", "Harbor Library contract — signature needed", account: Fixture.work, seen: false,
                count: 3, attach: true, action: "Due Mon",
                summary: "Needs your signature by Monday. Payment moves to 45 days, and the studio credit you asked "
                    + "for is in the new draft.", snippet: "Hi — attached is the revised contract.",
                at: today + 42_120, when: "11:42 am"),
            row(2, "Mira Okafor", "Re: Wayfinding concepts — round two", seen: false, count: 4, action: "Due Tue",
                summary: "The board picked concept B. She wants larger floor numbers on the stair signs and revised "
                    + "PDFs before Tuesday's meeting.", snippet: "Thanks for the concepts!", at: today + 36_300,
                when: "10:05 am"),
            row(3, "Aoi Nakamura", "Saturday — Takao or the river walk?", flagged: true, action: "Tonight",
                summary: "Asks which hike you'd like on Saturday. If Takao, she'll book the 8:10 train — she needs to "
                    + "know tonight.", snippet: "Which one shall we do?", at: today + 33_120, when: "9:12 am"),
            row(4, "Lantern Weekly", "Five quiet tools for October", snippet: "This week: a calmer terminal, a tiny "
                + "sync engine and the case for boring software.", at: today - 20_000, when: "Yesterday"),
            row(5, "Windbell Shop", "Your order is on its way",
                snippet: "Order #4521 · Total $48.00 · arriving Thursday", bucket: "receipts/transactional",
                at: today - 30_000, when: "Yesterday"),
            row(6, "Deals Daily", "Last chance on autumn coats", seen: false,
                snippet: "Everything must go by midnight.", junk: true, at: today - 3 * 86_400, when: "Tue"),
            row(7, "Grace Hopper", "Notes from the design review", account: Fixture.work, attach: true,
                summary: "Grace sums up the review: keep the stair colours, shorten the lift signs.",
                snippet: "Notes attached.", at: today - 4 * 86_400, when: "Mon"),
        ] + (8...30).map { key in
            row(key, "Sender \(key)", "A fictional message \(key)", seen: key % 3 != 0,
                snippet: "A line of fictional mail, number \(key).", at: today - key * 86_400, when: "Oct \(key)")
        }
    }

    /// The window on the Inbox, with a desk whose brief has read the digest: three need you.
    private func opened() async throws -> (MailApp, Desk) {
        let app = MailApp()
        let rows = Self.mail()
        let server = Fixture.server(rows: rows)
        server.on("GET", API.Get.Messages.path) { asked in
            let folder = asked.query["folder"] ?? "INBOX"
            let items: [API.FolderRow] = switch folder {
            case "Trash": rows.prefix(6).map { row in
                var row = row
                row.folder = "Trash"
                row.action = ""
                return row
            }
            case "INBOX": rows
            default: []
            }
            return FakeServer.ok(API.MessagesAnswer.folderListing(API.FolderListing(
                account: asked.query["account"], folder: folder, grouped: false, hasMore: false, items: items,
                offset: 0, ok: true, total: items.count)))
        }
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.openList()
        await until { app.list?.copy(at: 0) != nil }
        let desk = Desk()
        let routes = DeskRoutes()
        routes.answer("GET", API.Get.Digest.path, DeskFixture.digest())
        desk.link.routes = routes
        await desk.brief.refresh()
        return (app, desk)
    }

    // MARK: Drawing

    /// Draw the frame on `app` in `light`, the pointer over row `hover` if one is named, check it drew, and write it
    /// when asked.
    private func draw(_ name: String, light: MeadowLight, app: MailApp, desk: Desk, railOpen: Bool = false,
                      hover: Int? = nil) async throws {
        let shell = MeadowShell(defaults: try XCTUnwrap(UserDefaults(suiteName: suite)))
        shell.railOpen = railOpen
        let size = CGSize(width: 1440, height: 900)
        let frame = MeadowFrame(app: app, mailbox: app.mailbox, shell: shell, status: AppStatus.shared, lights: false) {
            Color.clear
        }
        .environment(desk)
        let host = NSHostingView(rootView: frame.meadowLight(light).frame(width: size.width, height: size.height))
        host.frame = CGRect(origin: .zero, size: size)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.appearance = NSAppearance(named: light.isDark ? .darkAqua : .aqua)
        window.contentView = host
        window.layoutIfNeeded()
        try await Task.sleep(for: .milliseconds(400))
        if let hover, let table = ListTable.find(in: host), hover < table.numberOfRows {
            let rect = table.rect(ofRow: hover)
            table.controller?.hover(at: NSPoint(x: rect.maxX - 40, y: rect.maxY - 20))
        }
        host.layoutSubtreeIfNeeded()
        host.displayIfNeeded()
        let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: bitmap)
        XCTAssertTrue(Self.drewRows(bitmap), name)
        window.contentView = nil
        guard let folder = Self.folder else { return }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
        try png.write(to: folder.appendingPathComponent("\(name).png"))
    }

    /// Whether the list's panel has rows in it: some pixel across its middle differs from the panel's paper.
    private static func drewRows(_ bitmap: NSBitmapImageRep) -> Bool {
        let scale = CGFloat(bitmap.pixelsWide) / 1440
        let y = Int(300 * scale)
        guard let paper = bitmap.colorAt(x: Int(100 * scale), y: Int(880 * scale)) else { return false }
        return stride(from: Int(110 * scale), to: Int(500 * scale), by: 3).contains { x in
            bitmap.colorAt(x: x, y: y) != paper
        }
    }
}
