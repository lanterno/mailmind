import AppKit
import SwiftUI
import WebKit
import XCTest

/// The reading pane's document as Meadow draws it (#44), in Meadow's frame at the mockup's sizes on fictional mail like
/// the mockup's: a conversation with its summary, at 1440 and 1180 and in focus; an invite, a newsletter, Trash, a
/// reply open in the box, the reading settings and the empty pane. A web view draws only by WebKit's own snapshot, so
/// each frame is given the core's kind of document and its snapshot laid where the frame stands. One picture is drawn
/// on every run and must draw; with `TEST_RUNNER_MEADOW_PICTURES=<folder>` each is drawn in every light and written
/// there as `reader-*.png`, to lay beside the mockup's `-read`, `-focus`, `-invite`, `-news`, `-trash`, `-draft`,
/// `-aa` and `-clear` pictures.
@MainActor
final class ReaderPictures: XCTestCase {
    private static var folder: URL? {
        ProcessInfo.processInfo.environment["MEADOW_PICTURES"].map { URL(fileURLWithPath: $0) }
    }

    private static let key = "8f3a1c5e7b9d2f4a6c8e0b1d3f5a7c9e2b4d6f8a0c1e3b5d7f9a2c4e6b8d0f1a"

    private var home: URL!
    private var suite = ""
    private var core: Core?

    override func setUp() async throws {
        _ = NSApplication.shared
        suite = "meadow-reader-\(UUID().uuidString)"
        home = FileManager.default.temporaryDirectory.appending(path: "mailmind-reader-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
    }

    override func tearDown() async throws {
        if let core { await core.close() }
        core = nil
        UserDefaults().removePersistentDomain(forName: suite)
        try? FileManager.default.removeItem(at: home)
        AppStatus.shared.hold()
    }

    // MARK: The pictures

    func testTheDocumentDraws() async throws {
        let world = try await opened()
        try await draw("reader-day-1440-read", light: .day, width: 1440, world: world) { conversation(world) }
    }

    func testTheConversationInEachLightAndWidth() async throws {
        guard Self.folder != nil else { return }
        let world = try await opened()
        for light in MeadowLight.allCases {
            for width in [1440, 1180] as [CGFloat] {
                try await draw("reader-\(light.rawValue)-\(Int(width))-read", light: light, width: width,
                               world: world) { conversation(world) }
            }
            try await draw("reader-\(light.rawValue)-1440-focus", light: light, width: 1440, world: world,
                           focus: true) { conversation(world) }
        }
    }

    func testAnInviteANewsletterAndTrash() async throws {
        guard Self.folder != nil else { return }
        let world = try await opened()
        for light in MeadowLight.allCases {
            try await draw("reader-\(light.rawValue)-1440-invite", light: light, width: 1440, world: world,
                           chosen: 3) { message(world, Mail.invite) }
            try await draw("reader-\(light.rawValue)-1440-news", light: light, width: 1440, world: world,
                           chosen: 4) { message(world, Mail.news) }
        }
        world.app.place = .folder(account: Fixture.ada, folder: "Trash")
        await until { world.app.list?.copy(at: 2) != nil }
        for light in MeadowLight.allCases {
            try await draw("reader-\(light.rawValue)-1440-trash", light: light, width: 1440, world: world,
                           chosen: 2) { message(world, Mail.trashed) }
        }
    }

    func testAReplyOpenInTheBox() async throws {
        guard Self.folder != nil else { return }
        let world = try await opened()
        let composing = Composing.shared
        composing.use(world.routes, digest: Fixture.digest(), preferences: Fixture.preferences().preferences)
        let copy = MailCopy(Mail.aoiRow)
        composing.offerInline(copy)
        let composer = try await replied(composing, copy)
        composer.setBody("Takao sounds lovely — let’s take the 8:10.\n\nSee you Saturday!" + composer.body,
                         selection: NSRange(location: 0, length: 0))
        for light in MeadowLight.allCases {
            try await draw("reader-\(light.rawValue)-1440-draft", light: light, width: 1440, world: world,
                           chosen: 2, scrolledToFoot: true) { message(world, Mail.aoi) }
        }
        composer.close(force: true)
        composing.inlineSlot = nil
    }

    func testTheReadingSettingsAndTheEmptyPane() async throws {
        guard let folder = Self.folder else { return }
        let world = try await opened()
        Composing.shared.use(world.routes, digest: Fixture.digest(), preferences: Fixture.preferences().preferences)
        for light in MeadowLight.allCases {
            let palette = MeadowPalette.of(light)
            // On the pane's paper, as Meadow's popover hangs it: its paper, corners and shadow are its own.
            let view = ReadingSettings()
                .padding(28)
                .background(palette.paper)
                .meadowLight(light)
            let host = NSHostingView(rootView: view)
            host.frame = CGRect(origin: .zero, size: host.fittingSize)
            host.layoutSubtreeIfNeeded()
            let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            host.cacheDisplay(in: host.bounds, to: bitmap)
            try write(bitmap, "reader-\(light.rawValue)-aa", to: folder)

            try await draw("reader-\(light.rawValue)-1440-clear", light: light, width: 1440, world: world,
                           chosen: nil) {
                PaneEmpty(words: .of(needsDone: true, hasMail: false)) {}
            }
        }
    }

    // MARK: The readers

    private func conversation(_ world: World) -> some View {
        ConversationReader(core: world.core, model: world.thread, row: Mail.renRow, prefs: ReadingPrefs(nil),
                           mine: world.mine, app: world.app)
    }

    private func message(_ world: World, _ mail: (row: API.FolderRow, opened: API.MessageOpened)) -> some View {
        MessageReader(core: world.core, copy: MailCopy(mail.row), opened: mail.opened, row: mail.row,
                      prefs: ReadingPrefs(nil), mine: world.mine, app: world.app)
    }

    // MARK: The window

    private struct World {
        let app: MailApp
        let desk: Desk
        let core: Core
        let routes: PictureRoutes
        let thread: ConversationModel
        let mine: Set<String>
    }

    /// The window on the Inbox's Everything, Ren's conversation chosen, its summary written; the core opened over a
    /// home of the test's own, with no mail in it.
    private func opened() async throws -> World {
        let core = try await Core.open(CoreHome(folder: home.path(percentEncoded: false),
                                                variables: ["MAILMIND_DB_KEY": Self.key],
                                                standIn: home.appending(path: "keychain.json")
                                                    .path(percentEncoded: false)))
        self.core = core
        let app = MailApp()
        let rows = Mail.rows
        let server = Fixture.server(rows: rows)
        server.on("GET", API.Get.Messages.path) { asked in
            let folder = asked.query["folder"] ?? "INBOX"
            let items: [API.FolderRow] = switch folder {
            case "Trash": rows.prefix(6).map { row in
                var row = row
                row.folder = "Trash"
                row.action = ""
                row.needsYou = false
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
        app.mailbox.composeTo = { _ in }
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.openList()
        await until { app.list?.copy(at: 0) != nil }

        let routes = PictureRoutes()
        let thread = ConversationModel(routes: routes, account: Fixture.work, id: "t1")
        await thread.load()

        let desk = Desk()
        let deskRoutes = DeskRoutes()
        deskRoutes.answer("GET", API.Get.Digest.path, DeskFixture.digest())
        deskRoutes.answer("POST", API.Post.AiSummarize.path, Mail.summary)
        desk.link.routes = deskRoutes
        await desk.summaries.summarize(MailCopy(Mail.renRow))
        return World(app: app, desk: desk, core: core, routes: routes, thread: thread,
                     mine: SendGuard.mineSet([Fixture.ada, Fixture.work]))
    }

    // MARK: Drawing

    /// Draw the frame at `width` by 900 in `light` with `reader` in its pane under the pane's bar, row `chosen` of the
    /// list open, each mail's body laid where its frame stands; check it drew, and write it when asked.
    private func draw(_ name: String, light: MeadowLight, width: CGFloat, world: World, chosen: Int? = 0,
                      focus: Bool = false, scrolledToFoot: Bool = false,
                      @ViewBuilder reader: () -> some View) async throws {
        let app = world.app
        if let chosen, let row = app.list?.copy(at: chosen) { app.mailbox.chose([row]) } else { app.mailbox.chose([]) }
        let shell = MeadowShell(defaults: try XCTUnwrap(UserDefaults(suiteName: suite)))
        if focus { shell.toggleFocus() }
        let size = CGSize(width: width, height: 900)
        let replyTo = app.list.flatMap { list in list.chosenPosition.flatMap { list.copy(at: $0) } }.map(MailCopy.init)
        let pane = VStack(spacing: 0) {
            MeadowPaneBar(app: app, mailbox: app.mailbox, message: chosen != nil,
                          writing: PaneWriting(replyTo: replyTo))
            reader()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        let frame = MeadowFrame(app: app, mailbox: app.mailbox, shell: shell, status: AppStatus.shared,
                                lights: false) { pane }
            .environment(world.desk)
        let host = NSHostingView(rootView: frame.meadowLight(light).frame(width: size.width, height: size.height))
        host.frame = CGRect(origin: .zero, size: size)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.appearance = NSAppearance(named: light.isDark ? .darkAqua : .aqua)
        window.contentView = host
        window.layoutIfNeeded()
        try await Task.sleep(for: .milliseconds(600))
        let webs = Self.webViews(in: host)
        for web in webs {
            web.navigationDelegate = nil
            web.loadHTMLString(Mail.document(for: web.accessibilityLabel() ?? "", light: light), baseURL: nil)
        }
        if !webs.isEmpty { try await Task.sleep(for: .milliseconds(1200)) }
        if scrolledToFoot, let scroll = Self.scrollViews(in: host).max(by: { $0.frame.height < $1.frame.height }),
           let document = scroll.documentView {
            document.scroll(NSPoint(x: 0, y: max(0, document.bounds.height - scroll.contentView.bounds.height)))
            try await Task.sleep(for: .milliseconds(300))
        }
        host.layoutSubtreeIfNeeded()
        host.displayIfNeeded()
        let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: bitmap)
        for web in webs where !web.isHidden {
            try await Self.lay(web, on: bitmap, in: host)
        }
        XCTAssertTrue(Self.drewTheDocument(bitmap, width: width), name)
        window.contentView = nil
        window.close()
        guard let folder = Self.folder else { return }
        try write(bitmap, name, to: folder)
    }

    private func write(_ bitmap: NSBitmapImageRep, _ name: String, to folder: URL) throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
        try png.write(to: folder.appendingPathComponent("\(name).png"))
    }

    /// A frame's snapshot, the part of it that shows, drawn over the picture where the frame stands.
    private static func lay(_ web: WKWebView, on bitmap: NSBitmapImageRep, in host: NSView) async throws {
        let visible = web.visibleRect
        guard !visible.isEmpty, web.bounds.height > 0 else { return }
        let shot = try await web.takeSnapshot(configuration: nil)
        let scale = shot.size.height / web.bounds.height
        let inHost = web.convert(visible, to: host)
        let destination = CGRect(x: inHost.minX, y: host.isFlipped ? host.bounds.height - inHost.maxY : inHost.minY,
                                 width: inHost.width, height: inHost.height)
        let top = web.isFlipped ? visible.minY : web.bounds.height - visible.maxY
        let source = CGRect(x: visible.minX * scale, y: shot.size.height - (top + visible.height) * scale,
                            width: visible.width * scale, height: visible.height * scale)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        shot.draw(in: destination, from: source, operation: .sourceOver, fraction: 1)
        NSGraphicsContext.restoreGraphicsState()
    }

    private static func webViews(in view: NSView) -> [WKWebView] {
        view.subviews.flatMap { sub -> [WKWebView] in
            if let web = sub as? WKWebView { return [web] }
            return webViews(in: sub)
        }
    }

    private static func scrollViews(in view: NSView) -> [NSScrollView] {
        view.subviews.flatMap { sub -> [NSScrollView] in
            (sub as? NSScrollView).map { [$0] + scrollViews(in: $0) } ?? scrollViews(in: sub)
        }
    }

    /// Whether the pane has its document in it: some pixel along the subject's line differs from the paper.
    private static func drewTheDocument(_ bitmap: NSBitmapImageRep, width: CGFloat) -> Bool {
        let scale = CGFloat(bitmap.pixelsWide) / width
        let y = Int(300 * scale)
        guard let paper = bitmap.colorAt(x: Int((width - 30) * scale), y: Int(880 * scale)) else { return false }
        return stride(from: Int((width - 600) * scale), to: Int((width - 40) * scale), by: 3).contains { x in
            bitmap.colorAt(x: x, y: y) != paper
        }
    }
}

// MARK: The mail

/// Mail like the mockup's, every name and address fictional.
private enum Mail {
    static let ren = "ren.takeda@example.test"

    static let renRow = row(1, from: "Ren Takeda", subject: "Harbor Library contract — signature needed",
                            account: Fixture.work, count: 3, action: "Due Mon", attach: true,
                            summary: "Needs your signature by Monday. Payment moves to 45 days, and the studio credit "
                                + "you asked for is in the new draft.", when: "11:42 am")
    static let aoiRow = row(3, from: "Aoi Nakamura", subject: "Saturday — Takao or the river walk?", action: "Tonight",
                            summary: "Asks which hike you'd like on Saturday. If Takao, she'll book the 8:10 train.",
                            when: "9:12 am")
    static let clinicRow = row(50, from: "Sato Family Clinic", subject: "Appointment: Thu 6 Nov, 9:30 am",
                               action: "Reply needed", when: "8:30 am")
    static let newsRow = row(4, from: "Lantern Weekly", subject: "Five quiet tools for October", when: "Yesterday")

    static let rows: [API.FolderRow] = [
        renRow,
        row(2, from: "Mira Okafor", subject: "Re: Wayfinding concepts — round two", count: 4, action: "Due Tue",
            summary: "The board picked concept B. She wants larger floor numbers on the stair signs.",
            when: "10:05 am"),
        aoiRow, clinicRow, newsRow,
        row(5, from: "Windbell Shop", subject: "Your order is on its way", snippet: "Order #4521 · Total $48.00",
            bucket: "receipts/transactional", when: "Yesterday"),
        row(6, from: "Grace Hopper", subject: "Notes from the design review", when: "Mon"),
    ] + (8...20).map { row($0, from: "Sender \($0)", subject: "A fictional message \($0)", when: "Oct \($0)") }

    static func row(_ key: Int, from: String, subject: String, account: String = Fixture.ada, count: Int? = nil,
                    action: String = "", attach: Bool = false, summary: String = "", snippet: String? = nil,
                    bucket: String? = nil, when: String) -> API.FolderRow {
        API.FolderRow(account: account, action: action, bucket: bucket, commitmentWorthy: false, count: count,
                      flagged: false, folder: "INBOX", fromAddr: address(from), fromName: from, hasAttach: attach,
                      isJunk: false, key: key, needsYou: !action.isEmpty, replyWorthy: !action.isEmpty,
                      safeToFile: false, seen: true, snippet: snippet ?? summary, subject: subject, summary: summary,
                      threadCount: count, threadId: count.map { _ in "t\(key)" }, ts: 1_793_620_800 - key * 600,
                      uid: key, when: when)
    }

    static func address(_ name: String) -> String {
        name.lowercased().replacingOccurrences(of: " ", with: ".") + "@example.test"
    }

    static func opened(_ row: API.FolderRow, to: String = Fixture.ada, html: Bool = false,
                       attachments: [API.AttachmentPart] = [], facts: API.SenderFacts? = nil, blocked: Int = 0,
                       invite: API.Invite? = nil) -> API.MessageOpened {
        API.MessageOpened(account: row.account, attachments: attachments, blockedCssUrls: 0, blockedImages: blocked,
                          cc: "", date: row.when, folder: row.folder, fromAddr: row.fromAddr, fromName: row.fromName,
                          hasHtml: html, invite: invite, invitePart: invite == nil ? nil : "2", key: row.key,
                          messageId: "<m\(row.key)@example.test>", ok: true, replyTo: "", sender: facts,
                          subject: row.subject, threadId: row.threadId, to: to, uid: row.uid)
    }

    static let invite: (row: API.FolderRow, opened: API.MessageOpened) = (clinicRow, opened(
        clinicRow, facts: API.SenderFacts(count: 1, replied: 0),
        invite: API.Invite(allDay: false, attendees: [], description: "", endIso: "2026-11-06T10:00:00", exdates: [],
                           location: "Sato Family Clinic, 2-14 Kichijoji", method: "REQUEST",
                           organizer: API.Party(email: "appointments@example.test", name: "Sato Family Clinic"),
                           recurrence: "", recurrenceId: "", rrule: "", sequence: 0,
                           startIso: "2026-11-06T09:30:00", status: "CONFIRMED",
                           summary: "Check-up with Dr. Emi Sato", tzid: "", uid: "clinic-1@example.test",
                           when: "Thursday 6 November · 9:30 – 10:00 am")))

    static let news: (row: API.FolderRow, opened: API.MessageOpened) = (newsRow, opened(
        newsRow, html: true, facts: API.SenderFacts(count: 24, firstAgo: "6mo", replied: 0), blocked: 3))

    static let trashed: (row: API.FolderRow, opened: API.MessageOpened) = {
        var row = aoiRow
        row.folder = "Trash"
        return (row, opened(row, facts: API.SenderFacts(count: 88, firstAgo: "2y", replied: 40)))
    }()

    static let aoi: (row: API.FolderRow, opened: API.MessageOpened) = (aoiRow, opened(
        aoiRow, facts: API.SenderFacts(count: 88, firstAgo: "2y", replied: 40)))

    static let summary = API.Summary(
        account: Fixture.work, action: "Sign and return the agreement", cached: false, createdAt: "2026-11-15",
        messageCount: 3, model: "test", ok: true, participants: [], points: [
            "Payment terms move from 30 to 45 days after each milestone",
            "The studio credit you asked for is on the entrance sign",
            "Everything else matches v2, which you approved",
        ], scope: "thread", stale: false, threadId: "t1",
        tldr: "Ren needs the signed v3 agreement back by Monday so the board can announce the project in November.")

    /// The conversation: Ren's first draft, your answer, and Ren's latest.
    static let thread = API.Thread(account: Fixture.work, count: 3, items: [
        API.ThreadMessage(answered: true, flagged: false, folder: "INBOX", fromAddr: ren, fromName: "Ren Takeda",
                          hasAttach: true, isJunk: false, key: 101, seen: true,
                          snippet: "First draft of the agreement attached — have a look when you can.",
                          subject: "Harbor Library contract", uid: 101, when: "Mon 26 Oct"),
        API.ThreadMessage(answered: false, flagged: false, folder: "[Gmail]/Sent Mail", fromAddr: Fixture.work,
                          fromName: "Hana Ito", hasAttach: false, isJunk: false, key: 102, seen: true,
                          snippet: "Happy with v2. Could we get a small studio credit on the entrance sign?",
                          subject: "Re: Harbor Library contract", uid: 102, when: "Tue 27 Oct"),
        API.ThreadMessage(answered: false, flagged: false, folder: "INBOX", fromAddr: ren, fromName: "Ren Takeda",
                          hasAttach: true, isJunk: false, key: 1, seen: true,
                          snippet: "Legal came back on the Harbor Library agreement — v3 is attached.",
                          subject: "Harbor Library contract — signature needed", uid: 1, when: "11:42 am"),
    ], ok: true, replyKey: 1, threadId: "t1")

    static let bodies: [Int: API.MessageOpened] = [
        1: opened(renRow, to: Fixture.work, attachments: [
            API.AttachmentPart(contentType: "application/pdf", filename: "Harbor-Library-agreement-v3.pdf",
                               isInline: 0, partId: "2", size: 1_288_000, travels: true),
            API.AttachmentPart(contentType: "application/pdf", filename: "Changes-from-v2.pdf", isInline: 0,
                               partId: "3", size: 96_000, travels: true),
        ], facts: API.SenderFacts(count: 41, firstAgo: "8mo", replied: 12)),
    ]

    /// What each frame shows, by the sender its title names: the core's kind of document with the look written in.
    static func document(for title: String, light: MeadowLight) -> String {
        let html = title.contains("Lantern Weekly")
        let text: String
        if title.contains("Ren Takeda") {
            text = "Hi Hana,\n\nLegal came back on the Harbor Library agreement — v3 is attached. Two changes from the "
                + "version you saw:\n\n1. Payment terms move from 30 to 45 days after each milestone.\n2. The studio "
                + "credit you asked for is now on the entrance sign.\n\nEverything else matches v2. If you can sign "
                + "and send it back by Monday, the board can announce the project in November.\n\nThanks,\nRen\n\n"
                + "On Tue 27 Oct, Hana Ito wrote:\n> Happy with v2. Could we get a small studio credit on the "
                + "entrance sign?"
        } else if title.contains("Sato") {
            text = "This is a reminder of your appointment with Dr. Emi Sato. Please arrive ten minutes early and "
                + "bring your insurance card.\n\nIf you need to change the time, reply to this email or call the "
                + "front desk."
        } else if title.contains("Aoi") {
            text = "Hi!\n\nWhich hike would you like on Saturday — Takao or the river walk? If Takao, I’ll book the "
                + "8:10 train, so I need to know tonight.\n\nAoi"
        } else {
            text = ""
        }
        let policy = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; base-uri 'none';"
            + " form-action 'none'"
        let head = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta http-equiv="
            + "\"Content-Security-Policy\" content=\"\(policy)\"><base target=\"_blank\"></head>"
        let body = html
            ? "<body><h2>Five quiet tools for October</h2><p>This week: a calmer terminal, a tiny sync engine and the "
                + "case for boring software.</p><p><a href=\"https://example.test/read\">Read the issue</a></p>"
                + "</body></html>"
            : "<body><pre>" + PlainQuote.escaped(text) + "</pre></body></html>"
        let look = ReaderPalette.look(html: html, prefs: MailLook.Prefs(), palette: .of(light), dark: light.isDark)
        let answer = CoreAnswer(status: 200, contentType: "text/html; charset=utf-8", headers: [],
                                body: Data((head + body).utf8))
        return String(decoding: FrameGuard.dressed(answer, look: look, html: html), as: UTF8.self)
    }
}

/// The routes the pictures' conversation and reply read: the thread, and each message's body by its key.
final class PictureRoutes: Routes, @unchecked Sendable {
    func get<R: GetRoute>(_ route: R.Type, _ query: KeyValuePairs<String, String?>) async throws -> R.Answer
        where R.Answer: Decodable
    {
        let key = query.first { $0.key == "key" }?.value.flatMap { $0 }.flatMap(Int.init)
        let value: any Encodable
        switch R.path {
        case API.Get.Thread.path:
            value = Mail.thread
        case API.Get.MessageBody.path:
            if let key, let body = Mail.bodies[key] {
                value = body
            } else if let key, let row = (Mail.rows + [Mail.trashed.row]).first(where: { $0.key == key }) {
                value = Mail.opened(row)
            } else if let key, let member = Mail.thread.items.first(where: { $0.key == key }) {
                value = API.MessageOpened(
                    account: Fixture.work, attachments: [], blockedCssUrls: 0, blockedImages: 0, cc: "",
                    date: member.when, folder: member.folder, fromAddr: member.fromAddr, fromName: member.fromName,
                    hasHtml: false, key: member.key, ok: true, replyTo: "", subject: member.subject,
                    to: Fixture.work, uid: member.uid)
            } else {
                throw FakeRoutes.problem(404, "Not found")
            }
        default:
            throw FakeRoutes.problem(404, "Not found")
        }
        return try JSONDecoder().decode(R.Answer.self, from: JSONEncoder().encode(value))
    }

    func post<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer where R.Answer: Decodable {
        throw FakeRoutes.problem(404, "Not found")
    }

    func postOverLimit<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer
        where R.Answer: Decodable
    {
        throw FakeRoutes.problem(404, "Not found")
    }
}
