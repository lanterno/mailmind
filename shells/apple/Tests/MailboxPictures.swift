import AppKit
import SwiftUI
import XCTest

/// The mailbox's views drawn on fictional mail, in light and dark, into build/screenshots/mailbox, so a person can look
/// at what they show without running the app. Each test checks the view drew rows, not only that it compiled.
@MainActor
final class MailboxPictures: XCTestCase {
    private static let folder = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .appending(path: "build/screenshots/mailbox", directoryHint: .isDirectory)

    override func setUp() async throws {
        _ = NSApplication.shared
        try FileManager.default.createDirectory(at: Self.folder, withIntermediateDirectories: true)
    }

    private func opened(_ server: FakeServer, place: Place) async -> MailApp {
        server.on("GET", API.Get.Search.path) { asked in
            let q = asked.query["q"] ?? ""
            let hits = q.contains("receipt") ? Self.receipts : q.contains("newsletter") ? Self.newsletters
                : Fixture.inbox.prefix(5).map { Self.hit(of: $0) }
            return FakeServer.ok(API.SearchAnswer.searchPage(API.SearchPage(generated: "", hasMore: false, items: hits, offset: 0,
                                                                            ok: true, query: q, scope: "all")))
        }
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.place = place
        return app
    }

    /// Draw `view` at `size` in light and dark, and save each as a PNG named `name`. The bitmap is returned for checks.
    @discardableResult
    private func picture(_ name: String, _ size: CGSize, _ view: @autoclosure () -> some View) async throws -> [NSBitmapImageRep] {
        var drawn: [NSBitmapImageRep] = []
        for (look, appearance) in [("light", NSAppearance.Name.aqua), ("dark", .darkAqua)] {
            let host = NSHostingView(rootView: view().background(Color(nsColor: .windowBackgroundColor)))
            host.frame = CGRect(origin: .zero, size: size)
            let window = NSWindow(contentRect: host.frame, styleMask: [.titled], backing: .buffered, defer: false)
            window.appearance = NSAppearance(named: appearance)
            window.contentView = host
            window.layoutIfNeeded()
            try await Task.sleep(for: .milliseconds(500))
            host.layoutSubtreeIfNeeded()
            host.displayIfNeeded()
            let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            host.cacheDisplay(in: host.bounds, to: bitmap)
            let png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
            try png.write(to: Self.folder.appending(path: "\(name)-\(look).png"))
            drawn.append(bitmap)
            window.contentView = nil
        }
        return drawn
    }

    func testTheSidebar() async throws {
        let app = await opened(Fixture.server(), place: .allInboxes)
        let drawn = try await picture("sidebar", CGSize(width: 260, height: 720), MailboxSidebar(app: app, mailbox: app.mailbox))
        XCTAssertTrue(drawn.allSatisfy(hasInk))
    }

    func testTheInboxList() async throws {
        let app = await opened(Fixture.server(), place: .allInboxes)
        await until { app.list?.copy(at: 0) != nil }
        app.mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
        app.mailbox.say("Archived", undo: "Archived")
        let drawn = try await picture("inbox", CGSize(width: 460, height: 720),
                                      MailboxColumn(app: app, mailbox: app.mailbox))
        XCTAssertTrue(drawn.allSatisfy(hasInk))
    }

    func testAFolderWithRowsMarked() async throws {
        let app = await opened(Fixture.server(grouped: false), place: .folder(account: Fixture.ada, folder: "Receipts"))
        await until { app.list?.copy(at: 2) != nil }
        app.mailbox.chose((0..<3).compactMap { app.list?.copy(at: $0) })
        try await picture("folder-marked", CGSize(width: 460, height: 520), MailboxColumn(app: app, mailbox: app.mailbox))
    }

    func testTheRowsAtAWideWidth() async throws {
        let app = await opened(Fixture.server(), place: .allInboxes)
        await until { app.list?.copy(at: 5) != nil }
        let list = try XCTUnwrap(app.list)
        try await picture("rows-wide", CGSize(width: 760, height: 420),
                          MailboxTable(list: list, mailbox: app.mailbox, chosen: [MailCopy(list.copy(at: 1)!)], showsAccount: true))
    }

    func testASearch() async throws {
        let app = await opened(Fixture.server(), place: .search(Search(q: "from:grace", account: Fixture.ada)))
        await until { app.list?.copy(at: 0) != nil }
        try await picture("search", CGSize(width: 460, height: 420), MailboxColumn(app: app, mailbox: app.mailbox))
    }

    func testTheLanes() async throws {
        let feed = await opened(Fixture.server(), place: .search(Search(q: "category:newsletter", lane: .feed)))
        await until { feed.mailbox.lane?.loaded == true }
        XCTAssertEqual(feed.mailbox.lane?.hits.count, Self.newsletters.count)
        try await picture("feed", CGSize(width: 460, height: 620), MailboxColumn(app: feed, mailbox: feed.mailbox))
        let trail = await opened(Fixture.server(), place: .search(Search(q: "category:receipt", lane: .papertrail)))
        await until { trail.mailbox.lane?.loaded == true }
        try await picture("paper-trail", CGSize(width: 460, height: 620), MailboxColumn(app: trail, mailbox: trail.mailbox))
    }

    func testTheDocumentHub() async throws {
        let server = Fixture.server()
        let docs = [
            Self.document(1, "Orbit figures.pdf", kind: .pdf, from: "Katherine Johnson", group: "s1", size: 482_000),
            Self.document(2, "Launch plan.key", kind: .slide, from: "Katherine Johnson", group: "s1", size: 3_100_000),
            Self.document(3, "Loan receipt.png", kind: .image, from: "Library of Ash Hollow", group: "s2", size: 88_000),
        ]
        server.on("GET", API.Get.Documents.path, API.Documents(
            docs: docs, facets: ["pdf": 1, "slide": 1, "image": 1], groups: [
                API.DocumentSection(count: 2, id: "s1", name: "Katherine Johnson"),
                API.DocumentSection(count: 1, id: "s2", name: "Library of Ash Hollow"),
            ], matched: 3, ok: true, shown: 3, total: 3))
        let app = await opened(server, place: .documents)
        await until { app.mailbox.documents?.first != nil }
        XCTAssertEqual(app.mailbox.documents?.countText, "3 files")
        try await picture("documents", CGSize(width: 460, height: 520), MailboxColumn(app: app, mailbox: app.mailbox))
    }

    func testTheContactCardAndThePalette() async throws {
        let server = Fixture.server()
        server.on("GET", API.Get.Contact.path, API.ContactCard(
            accounts: [Fixture.ada, Fixture.work], addr: "grace.hopper@example.test", count: 12, firstAgo: "2y", known: true,
            lastAgo: "3d", name: "Grace Hopper", ok: true, recent: [
                API.RecentMail(account: Fixture.ada, folder: "INBOX", key: 9, seen: false, subject: "Compilers, again", uid: 9, when: "3d"),
                API.RecentMail(account: Fixture.work, folder: "INBOX", key: 8, seen: true, subject: "Nanoseconds", uid: 8, when: "2w"),
            ], replied: 3))
        let app = await opened(server, place: .allInboxes)
        try await picture("contact", CGSize(width: 420, height: 360),
                          ContactCard(mailbox: app.mailbox, address: "grace.hopper@example.test", close: {}))
        app.mailbox.chose([Fixture.row(1)])
        try await picture("palette", CGSize(width: 520, height: 420), PaletteSheet(mailbox: app.mailbox, close: {}))
        try await picture("move-to", CGSize(width: 520, height: 420),
                          MoveToSheet(mailbox: app.mailbox, ask: MoveAsk(account: Fixture.ada, from: "INBOX"),
                                      close: {}))
    }

    /// Whether a picture has more than its background: some pixels differ from the corner's.
    private func hasInk(_ bitmap: NSBitmapImageRep) -> Bool {
        guard let corner = bitmap.colorAt(x: 1, y: 1) else { return false }
        var differing = 0
        for y in stride(from: 0, to: bitmap.pixelsHigh, by: 7) {
            for x in stride(from: 0, to: bitmap.pixelsWide, by: 7) {
                if let color = bitmap.colorAt(x: x, y: y), color != corner { differing += 1 }
            }
        }
        return differing > 50
    }

    nonisolated private static func hit(of row: API.FolderRow) -> API.SearchHit {
        API.SearchHit(account: row.account, commitmentWorthy: false, date: "2026-11-02", flagged: row.flagged, folder: row.folder,
                      fromAddr: row.fromAddr, fromName: row.fromName, hasAttach: row.hasAttach, isJunk: row.isJunk, key: row.key,
                      needsYou: row.needsYou, replyWorthy: false, safeToFile: false, seen: row.seen, snippet: row.snippet,
                      subject: row.subject, uid: row.uid, when: row.when)
    }

    nonisolated private static let receipts = [
        Fixture.hit(31, subject: "Your order: $42.10", from: "Bookshop"),
        Fixture.hit(32, subject: "Refund of $5.00", from: "Bookshop"),
        Fixture.hit(33, subject: "Invoice 2211 — €1.234,50", from: "Studio", seen: false),
        Fixture.hit(34, subject: "Receipt for your ride", from: "Cabs", account: Fixture.work),
        Fixture.hit(35, subject: "Paid $1.50", date: "2026-10-30", from: "Coffee"),
    ]

    nonisolated private static let newsletters = [
        Fixture.hit(41, subject: "This week in compilers", from: "Weekly", seen: false),
        Fixture.hit(42, subject: "Twelve letters from the archive", from: "Letters"),
        Fixture.hit(43, subject: "On orbits", from: "Sky Notes", account: Fixture.work),
    ]

    private static func document(_ key: Int, _ name: String, kind: API.DocumentKind, from: String, group: String, size: Int)
        -> API.Document
    {
        API.Document(account: Fixture.ada, filename: name, folder: "INBOX", from: from, group: group, key: key, kind: kind,
                     partId: "2", preview: kind == .pdf ? "/api/attachment?key=\(key)&part=2&inline=1" : nil, size: size,
                     subject: "Files for you", uid: key, url: "/api/attachment?key=\(key)&part=2", when: "Nov 2")
    }
}
