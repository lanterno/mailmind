import AppKit
import SwiftUI
import XCTest

/// The desk's places in Meadow's window, on fictional mail: the brief beside the Inbox, the gate and the paper trail
/// across the window, and an answer where the list stands, at the mockup's 1440 by 900 and 1180 by 760. The day's
/// brief at 1440 is drawn on every run and must draw; with `TEST_RUNNER_MEADOW_PICTURES=<folder>` given to
/// `xcodebuild test`, every light and both widths are drawn and written to `<folder>/desk`, to lay beside the mockup's
/// `*-brief.png`, `*-gate.png`, `1440-*-ledger.png` and `1440-*-ask.png`.
@MainActor
final class MeadowDeskPictures: XCTestCase {
    private static var folder: URL? {
        ProcessInfo.processInfo.environment["MEADOW_PICTURES"]
            .map { URL(fileURLWithPath: $0).appendingPathComponent("desk") }
    }

    private static let sizes: [(name: String, size: CGSize)] = [("1440", CGSize(width: 1440, height: 900)),
                                                                  ("1180", CGSize(width: 1180, height: 760))]
    private var suite = ""

    override func setUp() async throws {
        _ = NSApplication.shared
        suite = "meadow-desk-\(UUID().uuidString)"
    }

    override func tearDown() async throws {
        UserDefaults().removePersistentDomain(forName: suite)
    }

    // MARK: The places

    func testTheBriefInEachLight() async throws {
        let lights: [MeadowLight] = Self.folder == nil ? [.day] : MeadowLight.allCases
        let app = try await inbox()
        let desk = try await desk()
        app.place = .desk(.brief)
        for light in lights {
            for (name, size) in Self.folder == nil ? [Self.sizes[0]] : Self.sizes {
                let pane = VStack(spacing: 0) {
                    MeadowPaneBar(app: app, mailbox: app.mailbox, message: false)
                    brief(desk, light)
                }
                try await draw("\(name)-\(light.rawValue)-brief", light: light, size: size, frame(app, desk) { pane })
            }
        }
    }

    /// The whole brief, as far down as it goes, at the pane's width beside the Inbox at 1440 and at 1180.
    func testTheWholeBrief() async throws {
        guard Self.folder != nil else { return }
        let desk = try await desk()
        for light in MeadowLight.allCases {
            for (name, width) in [("1440", 972.0), ("1180", 712.0)] {
                let page = brief(desk, light)
                    .meadowPanel()
                    .environment(desk)
                try await draw("\(name)-\(light.rawValue)-brief-whole", light: light,
                               size: CGSize(width: width, height: width > 900 ? 1500 : 2000), page)
            }
        }
    }

    /// Today & next offers Accept and Open under an invite, and nothing under an event that asks for no answer.
    func testTodayAndNextOffersAnAnswerOnlyUnderAnInvite() async throws {
        let desk = try await desk()
        let events = DeskPictures.upcoming.items
        XCTAssertTrue(events[0].invite)
        XCTAssertFalse(events[1].invite)
        func height(_ event: API.UpcomingEvent, _ replies: InviteReplies?) -> CGFloat {
            NSHostingView(rootView: UpcomingList(events: [event], now: DeskPictures.now(.day), open: { _ in },
                                                 replies: replies).frame(width: 300)).fittingSize.height
        }
        XCTAssertGreaterThan(height(events[0], desk.invites), height(events[0], nil))
        XCTAssertEqual(height(events[1], desk.invites), height(events[1], nil))
    }

    /// An invite accepted in Today & next answers the invite's own message through the window's replies, which the
    /// message's card shows too; the brief then says only the answer, with Undo, drawn in each light.
    func testTodayAndNextWithAnInviteAnswered() async throws {
        let desk = try await desk()
        let routes = try XCTUnwrap(desk.link.routes as? DeskRoutes)
        routes.answer("POST", "/api/invite/rsvp", API.RsvpQueued(
            decision: .accept, ok: true, organizer: "Grace Hopper", outboxId: 7, queued: true, summary: "Studio sync",
            undoSeconds: 30))
        let event = DeskPictures.upcoming.items[0]
        let copy = MailCopy.named(key: event.key, folder: event.folder, uid: event.uid, account: event.account)

        await desk.invites.reply(to: copy, .accept)

        let sent = try XCTUnwrap(routes.sent("POST", "/api/invite/rsvp").first)
        XCTAssertEqual(sent["key"], .number(301))
        XCTAssertEqual(sent["uid"], .number(31))
        XCTAssertEqual(sent["folder"], .string("INBOX"))
        XCTAssertEqual(sent["decision"], .string("accept"))
        guard case .queued? = desk.invites.said[copy] else { return XCTFail("not queued") }
        for light in Self.folder == nil ? [.day] : MeadowLight.allCases {
            let card = DeskSideCard(title: "Today & next", icon: .cal) {
                UpcomingList(events: DeskPictures.upcoming.items, now: DeskPictures.now(light), open: { _ in },
                             replies: desk.invites)
            }
            .padding(16)
            .background(MeadowPalette.of(light).paper)
            try await draw("today-\(light.rawValue)-answered", light: light, size: CGSize(width: 340, height: 360),
                           card)
        }
    }

    /// The brief's top, where the line over the greeting says how the mail stands: with mail (what the digest says
    /// needs you and the Inbox's unread), and on a quiet day with AI turned off (nothing unread, no brief written).
    func testTheBriefsLineSaysHowTheMailStands() async throws {
        guard Self.folder != nil else { return }
        let busy = try await desk()
        let quiet = try await desk(DeskPictures.routes(digest: DeskPictures.quiet))
        XCTAssertTrue(quiet.brief.aiTurnedOff)
        for light in MeadowLight.allCases {
            for (name, desk, unread) in [("mail", busy, 5), ("none", quiet, 0)] {
                try await draw("brief-hero-\(name)-\(light.rawValue)", light: light,
                               size: CGSize(width: 972, height: 380),
                               brief(desk, light, unread: unread).meadowPanel().environment(desk))
            }
        }
    }

    /// The brief with a reply written ahead for the first message, as the mockup's first card has one.
    private func brief(_ desk: Desk, _ light: MeadowLight, unread: Int? = nil) -> some View {
        BriefScreen(model: desk.brief, attention: desk.attention,
                    actions: BriefActions(reply: { _ in }, draftReady: { $0 == 101 }, paperTrail: {}),
                    tasks: desk.tasks, now: DeskPictures.now(light), name: "Ada", unread: unread)
    }

    func testTheGateInEachLight() async throws {
        guard Self.folder != nil else { return }
        let app = try await inbox()
        let desk = try await desk()
        app.place = .desk(.screener(account: DeskPictures.work))
        // The gate reads the visitor's latest letter by a search of its own.
        desk.link.routes = DeskPictures.routes(found: [DeskPictures.letter])
        await desk.screener(for: DeskPictures.work).load()
        for light in MeadowLight.allCases {
            for (name, size) in Self.sizes {
                try await draw("\(name)-\(light.rawValue)-gate", light: light, size: size,
                               frame(app, desk) { Color.clear })
            }
        }
    }

    func testThePaperTrailInEachLight() async throws {
        guard Self.folder != nil else { return }
        let server = Fixture.server()
        server.on("GET", API.Get.Search.path, API.SearchAnswer.searchPage(API.SearchPage(
            generated: "2026-11-15T09:00:00Z", hasMore: false, items: DeskPictures.receipts, offset: 0, ok: true,
            query: "category:receipt", scope: "all")))
        let app = try await inbox(server)
        let desk = try await desk()
        app.place = .search(Search(q: "category:receipt", account: "", lane: .papertrail))
        await until { app.mailbox.lane?.loaded == true }
        for light in MeadowLight.allCases {
            for (name, size) in Self.sizes {
                try await draw("\(name)-\(light.rawValue)-ledger", light: light, size: size,
                               frame(app, desk) { Color.clear })
            }
        }
    }

    func testAnAnswerInEachLight() async throws {
        guard Self.folder != nil else { return }
        let app = try await inbox()
        let desk = try await desk()
        await desk.asking.ask("When is my train to the coast?")
        app.place = .desk(.ask)
        for light in MeadowLight.allCases {
            try await draw("1440-\(light.rawValue)-ask", light: light, size: Self.sizes[0].size, frame(app, desk) {
                VStack(spacing: 0) {
                    MeadowPaneBar(app: app, mailbox: app.mailbox, message: false)
                    Spacer(minLength: 0)
                }
            })
        }
    }

    // MARK: The window around them

    /// The Inbox's Everything on the fixture's mail, nothing open.
    private func inbox(_ server: FakeServer = Fixture.server()) async throws -> MailApp {
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.openList()
        await until { app.list?.copy(at: 0) != nil }
        return app
    }

    /// The desk on scripted routes, the brief and the tasks read.
    private func desk(_ routes: DeskRoutes = DeskPictures.routes()) async throws -> Desk {
        let desk = Desk()
        desk.link.routes = routes
        desk.brief.paperTrailQuery = { "category:receipt" }
        await desk.tasks.load()
        await desk.brief.load()
        return desk
    }

    private func frame(_ app: MailApp, _ desk: Desk, @ViewBuilder pane: () -> some View) throws -> some View {
        let shell = MeadowShell(defaults: try XCTUnwrap(UserDefaults(suiteName: suite)))
        shell.railOpen = false
        return MeadowFrame(app: app, mailbox: app.mailbox, shell: shell, status: status(), lights: false, pane: pane)
            .environment(desk)
    }

    /// Synced two minutes ago, with the fixture's preferences.
    private func status() -> AppStatus {
        let now = Date()
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        let last = ISO8601DateFormatter().string(from: now.addingTimeInterval(-120))
        status.hold(digest: StatusFixtures.digest(accounts: [DeskPictures.work], last: last),
                    preferences: StatusFixtures.preferences(), now: now)
        return status
    }

    /// Draw `view` in `light` at `size` in a window of its own, check it drew, and write it when asked.
    private func draw(_ name: String, light: MeadowLight, size: CGSize, _ view: some View) async throws {
        let host = NSHostingView(rootView: view.meadowLight(light).frame(width: size.width, height: size.height))
        host.frame = CGRect(origin: .zero, size: size)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.appearance = NSAppearance(named: light.isDark ? .darkAqua : .aqua)
        window.contentView = host
        window.layoutIfNeeded()
        try await Task.sleep(for: .milliseconds(500))
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

/// A fictional day's mail for the desk's pictures, every address at example.test: two accounts, three messages that
/// need you, a few worth a read and some that can wait, a meeting, someone you're waiting on, tasks, a gate with
/// three visitors, receipts over five months and an answer.
@MainActor
enum DeskPictures {
    static let work = "ada@example.test"
    static let home = "ada.home@example.test"

    /// Mid-afternoon in the day, early evening at dusk, late at night, on Sunday 15 November 2026 here.
    static func now(_ light: MeadowLight) -> Date {
        let hour = switch light {
        case .day: 14
        case .dusk: 17
        case .night: 22
        }
        return Calendar.current.date(from: DateComponents(year: 2026, month: 11, day: 15, hour: hour, minute: 30))
            ?? Date()
    }

    private static func item(_ key: Int, account: String = work, from: String, name: String, subject: String,
                             needs: Bool = false, importance: Int = 2, action: String = "", summary: String? = nil,
                             safe: Bool = false, seen: Bool = false, commitment: Bool = false,
                             when: String = "9:12 am") -> API.DigestItem {
        API.DigestItem(account: account, action: action, alias: account == work ? "Work" : "Personal",
                       commitmentWorthy: commitment, flagged: false, folder: "INBOX", fromAddr: from, fromName: name,
                       hasAttach: false, importance: importance, key: key, kind: "message", link: "", needsYou: needs,
                       replyWorthy: needs, safeToFile: safe, seen: seen, sentTo: [], sentToMore: 0,
                       subject: subject, summary: summary,
                       uid: key - 100, when: when)
    }

    static let items: [API.DigestItem] = [
        item(101, from: "ruth@studio.example.test", name: "Ruth Lane",
             subject: "Office lease — signature needed by Monday", needs: true, importance: 5, action: "Due Mon",
             summary: "Needs your signature by Monday. Rent is fixed for two years, and the deposit you asked about "
                + "is waived.",
             commitment: true, when: "11:42 am"),
        item(102, from: "grace@example.test", name: "Grace Hopper", subject: "Re: Q3 figures — round two",
             needs: true, importance: 4, action: "Due Tue",
             summary: "The board liked the draft. She wants the regional split and a cleaner chart by Tuesday.",
             when: "10:05 am"),
        item(103, account: home, from: "alan@example.test", name: "Alan Turing",
             subject: "Saturday — the coast or the river walk?", needs: true, importance: 4, action: "Tonight",
             summary: "Asks which walk you'd like on Saturday. If the coast, he'll book the 8:10 train tonight.",
             when: "9:12 am"),
        item(104, account: home, from: "tickets@rail.example.test", name: "Northwind Rail",
             subject: "Your e-ticket: the coast, Sat 21 Nov", importance: 3,
             summary: "Two returns to the coast on the 8:10, seats 12C and 12D.", when: "8:40 am"),
        item(105, from: "notes@field.example.test", name: "Field Notes", subject: "Issue 12: winter light",
             importance: 2, summary: "Essays on painting short days, and a reading list.", when: "7:55 am"),
        item(106, account: home, from: "marie@example.test", name: "Marie Curie",
             subject: "Photos from the pottery class", importance: 2,
             summary: "Twelve photos from Thursday, and a date for the next class.", when: "Yesterday"),
        item(107, from: "news@shop.example.test", name: "Shop Weekly", subject: "Ten deals for your weekend",
             safe: true),
        item(108, from: "receipts@cafe.example.test", name: "Café Lumen", subject: "Your receipt", safe: true),
        item(109, account: home, from: "noreply@alerts.example.test", name: "Alerts", subject: "New sign-in",
             safe: true),
        item(110, from: "digest@forum.example.test", name: "Forum Digest", subject: "This week in the forum",
             safe: true),
        item(111, account: home, from: "hello@garden.example.test", name: "Garden Club", subject: "Seed swap",
             safe: true),
    ]

    static let digest = API.Digest(
        accounts: [work, home],
        ai: API.AiStatus(backgroundModel: "claude-haiku-4-5", cloudModel: false, engine: "anthropic", keyFrom: "keychain", lastTs: 0,
                         limitMicros: 15_000_000,
                         model: "claude-haiku-4-5", provider: .anthropic),
        aliases: [work: "Work", home: "Personal"], colours: [work: 0, home: 1], generated: "2026-11-15T09:00:00Z", items: items, profile: "full",
        sendAccounts: [work, home], signatureDefault: "", signatures: [:],
        stats: API.DigestStats(kept: 6, needs: 3, summarized: 11, total: 11, verified: 11),
        status: [:], sync: API.SyncState(result: "ok", running: false))

    /// A quiet day with AI turned off: the same accounts, nothing that needs you, and no brief written.
    static let quiet = API.Digest(
        accounts: [work, home],
        ai: API.AiStatus(backgroundModel: "", cloudModel: false, engine: "none", keyFrom: "", lastTs: 0,
                         limitMicros: 15_000_000, model: "", off: .turnedOff, provider: .anthropic),
        aliases: [work: "Work", home: "Personal"], colours: [work: 0, home: 1], generated: "2026-11-15T09:00:00Z", items: [], profile: "full",
        sendAccounts: [work, home], signatureDefault: "", signatures: [:],
        stats: API.DigestStats(kept: 0, needs: 0, summarized: 0, total: 0, verified: 0),
        status: [:], sync: API.SyncState(result: "ok", running: false))

    static let brief = API.Brief(
        cached: false, generated: "2026-11-15T08:10:00Z",
        groups: [API.BriefGroup(count: 2, label: "Work", messages: [API.Copy(folder: "INBOX", key: 101, uid: 1),
                                                                    API.Copy(folder: "INBOX", key: 102, uid: 2)]),
                 API.BriefGroup(count: 1, label: "Personal", messages: [API.Copy(folder: "INBOX", key: 103, uid: 3)])],
        model: "claude-haiku-4-5",
        narrative: "A calm Sunday. Three things need you: Ruth needs the signed office lease back by Monday, Grace "
            + "wants the regional split by Tuesday, and Alan is waiting to hear about Saturday — he'd like an answer "
            + "tonight. Your train ticket for the 21st arrived; the rest is routine.",
        ok: true, stale: false)

    static let upcoming = API.Upcoming(items: [
        API.UpcomingEvent(account: work, allDay: false, end: "2026-11-15T16:30:00Z", folder: "INBOX", invite: true,
                          key: 301, location: "Roof room", organizer: "Grace Hopper", recurrence: "",
                          start: "2026-11-15T16:00:00Z", summary: "Studio sync", uid: 31),
        API.UpcomingEvent(account: home, allDay: false, end: "2026-11-21T17:00:00Z", folder: "INBOX", invite: false,
                          key: 302, location: "", organizer: "Alan Turing", recurrence: "",
                          start: "2026-11-21T08:10:00Z", summary: "Walk with Alan", uid: 32),
        API.UpcomingEvent(account: home, allDay: false, end: "2026-11-26T10:00:00Z", folder: "INBOX", invite: true,
                          key: 303, location: "Riverside Clinic", organizer: "", recurrence: "",
                          start: "2026-11-26T09:30:00Z", summary: "Check-up", uid: 33),
    ], ok: true)

    static let waiting = API.WaitingOnOthers(items: [
        API.WaitingRow(account: work, count: 1, flagged: false, folder: "Sent", hasAttach: false, isJunk: false,
                       key: 401, kind: "message", seen: true, sentTo: [], sentToMore: 0,
                       snippet: "", subject: "Sign quote for the lobby",
                       to: "Kit Marlowe <kit@example.test>", ts: 1_794_480_000, uid: 41, waiting: true,
                       waitingLabel: "", when: "Nov 12"),
    ], ok: true)

    static let tasks = API.TaskList(items: [
        API.Task(account: work, done: false, due: "2026-11-16", folder: "INBOX", fromAddr: "ruth@studio.example.test",
                 fromName: "Ruth Lane", id: 1, key: 101, subject: "Office lease", title: "Sign and return the lease",
                 uid: 1),
        API.Task(account: home, done: true, due: nil, folder: nil, fromAddr: "alan@example.test",
                 fromName: "Alan Turing", id: 2, key: nil, subject: "Lunch", title: "Book a table for Thursday",
                 uid: nil),
    ], ok: true)

    static let screener = API.ScreenerQueue(account: work, items: [
        API.ScreenerItem(count: 1, domain: "library.example.test", fromAddr: "yuki@library.example.test",
                         fromName: "Yuki Mori", when: "9:40 am"),
        API.ScreenerItem(count: 3, domain: "growth.example.test", fromAddr: "hello@growth.example.test",
                         fromName: "Growth Partners", when: "Yesterday"),
        API.ScreenerItem(count: 1, domain: "example.test", fromAddr: "tomo@example.test", fromName: "Tomo Arai",
                         when: "Wed"),
    ], ok: true, total: 3)

    static let letter = API.SearchHit(
        account: work, commitmentWorthy: false, date: "2026-11-15", flagged: false, folder: "INBOX",
        fromAddr: "yuki@library.example.test", fromName: "Yuki Mori", hasAttach: false, isJunk: false, key: 501,
        needsYou: false, replyWorthy: false, safeToFile: false, seen: false, sentTo: [], sentToMore: 0,
        snippet: "Dear Ada, Ruth mentioned you're finishing the lease this week, so I wanted to introduce myself "
            + "before the November opening. I chair the library board, and we'd love a few words from you at the "
            + "opening.",
        subject: "Hello from the library board", uid: 51, when: "9:40 am")

    private static func receipt(_ key: Int, _ date: String, from: String, subject: String,
                                account: String = Fixture.ada, folder: String = "INBOX") -> API.SearchHit {
        API.SearchHit(account: account, commitmentWorthy: false, date: date, flagged: false, folder: folder,
                      fromAddr: "receipts@\(from.lowercased().filter(\.isLetter)).example.test", fromName: from,
                      hasAttach: false, isJunk: false, key: key, needsYou: false, replyWorthy: false, safeToFile: true,
                      seen: true, sentTo: [], sentToMore: 0,
                      snippet: "Thank you for your order.", subject: subject, uid: key, when: date)
    }

    static let receipts: [API.SearchHit] = [
        receipt(601, "2026-11-13", from: "Northwind Rail", subject: "Your e-ticket — $98.50", folder: "Travel"),
        receipt(602, "2026-11-11", from: "Tessera", subject: "Invoice for the team plan — $45.00",
                account: Fixture.work, folder: "Invoices"),
        receipt(603, "2026-11-09", from: "Kiln Supply", subject: "Order 2231: 12 glass chimes — $126.40",
                account: work, folder: "Orders"),
        receipt(604, "2026-11-06", from: "Hoshi Books", subject: "Your receipt: two titles — $22.50"),
        receipt(605, "2026-11-02", from: "Clay Studio", subject: "Refund for a cancelled class — $20.00"),
        receipt(606, "2026-10-28", from: "Café Lumen", subject: "Your receipt — $18.20"),
        receipt(607, "2026-10-14", from: "Tessera", subject: "Invoice for the team plan — $45.00",
                account: Fixture.work),
        receipt(608, "2026-10-03", from: "Northwind Rail", subject: "Your e-ticket — $250.20"),
        receipt(609, "2026-09-21", from: "Hoshi Books", subject: "Your receipt — $64.00"),
        receipt(610, "2026-09-12", from: "Tessera", subject: "Invoice for the team plan — $45.00",
                account: Fixture.work),
        receipt(611, "2026-08-30", from: "Kiln Supply", subject: "Order 2107 — $188.00", account: Fixture.work),
        receipt(612, "2026-07-18", from: "Café Lumen", subject: "Your receipt — $31.60"),
    ]

    static let answered = API.Answered(
        account: "all",
        answer: "Saturday 21 November. You leave on the 8:10 and reach the coast at 9:52 — coach 7, seats 12C and 12D. "
            + "You paid $98.50.",
        generated: "2026-11-15T09:00:00Z", model: "claude-sonnet-4-5", ok: true,
        question: "When is my train to the coast?",
        sources: [API.AskSource(account: home, date: "2026-11-13", folder: "INBOX", from: "Northwind Rail", key: 104,
                                snippet: "Two returns to the coast on the 8:10, seats 12C and 12D.",
                                subject: "Your e-ticket: the coast, Sat 21 Nov", uid: 4)])

    /// The routes the desk reads, answered with the day above.
    static func routes(found: [API.SearchHit] = receipts, digest: API.Digest = digest) -> DeskRoutes {
        let routes = DeskRoutes()
        routes.answer("GET", "/api/digest", digest)
        routes.answer("GET", "/api/briefing", API.BriefingAnswer.brief(brief))
        routes.answer("GET", "/api/upcoming", upcoming)
        routes.answer("GET", "/api/waiting-on-others", waiting)
        routes.answer("GET", "/api/tidy", API.TidyRun(filed: 0, group: nil, items: [], ok: true))
        routes.answer("GET", "/api/folders", API.Folders(account: work, folders: [], muted: 1, ok: true, replylater: 2,
                                                         snoozed: 3, waiting: 1))
        routes.answer("GET", "/api/tasks", tasks)
        routes.answer("GET", "/api/screener", screener)
        routes.answer("GET", "/api/search", API.SearchAnswer.searchPage(API.SearchPage(
            generated: "2026-11-15T09:00:00Z", hasMore: false, items: found, offset: 0, ok: true,
            query: "category:receipt", scope: "all")))
        routes.answer("POST", "/api/ai/ask", answered)
        return routes
    }
}
