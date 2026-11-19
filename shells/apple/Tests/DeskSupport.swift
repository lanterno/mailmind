import AppKit
import Foundation
import SwiftUI
import XCTest

/// The core's stand-in for the desk's models: each route answers what a test scripted for it, and every request is
/// kept, so a test reads what was sent as the server would. A route nothing was scripted for answers the 404 the
/// server's router does. Answers go through the same decoding and problem reading as `Core`'s.
final class DeskRoutes: RouteCaller, @unchecked Sendable {
    struct Sent: Equatable {
        let method: String
        let path: String
        let query: [String: String]
        let body: JSONValue?
        /// Sent with the person's leave to go over today's AI limit.
        let overLimit: Bool
    }

    private let lock = NSLock()
    private var answers: [String: [CoreAnswer]] = [:]
    private var log: [Sent] = []

    /// What `method path` answers next: each scripted answer once, the last one from then on.
    func answer(_ method: String, _ path: String, status: UInt16 = 200, json: String) {
        let answer = CoreAnswer(status: status, contentType: status < 300 ? "application/json" : "application/problem+json",
                                headers: [], body: Data(json.utf8))
        lock.withLock { answers["\(method) \(path)", default: []].append(answer) }
    }

    func answer<T: Encodable>(_ method: String, _ path: String, _ value: T) {
        let json = String(decoding: try! JSONEncoder().encode(value), as: UTF8.self)
        answer(method, path, json: json)
    }

    /// A refusal as the server words one: its problem document.
    func refuse(_ method: String, _ path: String, status: UInt16, type: String = "refused", detail: String,
                more: String = "") {
        answer(method, path, status: status, json: """
            {"type": "/problems/\(type)", "title": "Refused", "status": \(status), "detail": "\(detail)"\(more.isEmpty ? "" : ", " + more)}
            """)
    }

    var sent: [Sent] { lock.withLock { log } }

    func sent(_ method: String, _ path: String) -> [Sent] {
        sent.filter { $0.method == method && $0.path == path }
    }

    /// Keep `method path`'s answers back until `release`, as a slow server does: a request is sent at once and waits
    /// for its answer.
    func hold(_ method: String, _ path: String) {
        lock.withLock { _ = held.insert("\(method) \(path)") }
    }

    func release(_ method: String, _ path: String) {
        lock.withLock { _ = held.remove("\(method) \(path)") }
    }

    private var held: Set<String> = []

    private func waitWhileHeld(_ method: String, _ path: String) async {
        while lock.withLock({ held.contains("\(method) \(path)") }) {
            try? await Task.sleep(for: .milliseconds(5))
        }
    }

    // MARK: RouteCaller

    func get<R: GetRoute>(_ route: R.Type, _ query: KeyValuePairs<String, String?>) async throws -> R.Answer
        where R.Answer: Decodable
    {
        let answer = reply("GET", R.path, query, nil)
        await waitWhileHeld("GET", R.path)
        return try Core.decoded(R.Answer.self, from: answer)
    }

    func post<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer where R.Answer: Decodable {
        let sent = try JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(body))
        let answer = reply("POST", R.path, [:], sent)
        await waitWhileHeld("POST", R.path)
        return try Core.decoded(R.Answer.self, from: answer)
    }

    func postOverLimit<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer
        where R.Answer: Decodable
    {
        let sent = try JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(body))
        return try Core.decoded(R.Answer.self, from: reply("POST", R.path, [:], sent, overLimit: true))
    }

    func document<R: GetRoute>(_ route: R.Type, _ query: KeyValuePairs<String, String?>) async throws -> NotJSON
        where R.Answer == NotJSON
    {
        let answer = reply("GET", R.path, query, nil)
        guard (200..<300).contains(answer.status) else { throw Problem(answer) }
        return NotJSON(data: answer.body, contentType: answer.contentType)
    }

    private func reply(_ method: String, _ path: String, _ query: KeyValuePairs<String, String?>, _ body: JSONValue?,
                       overLimit: Bool = false) -> CoreAnswer
    {
        var named: [String: String] = [:]
        for (key, value) in query { if let value { named[key] = value } }
        return lock.withLock {
            log.append(Sent(method: method, path: path, query: named, body: body, overLimit: overLimit))
            let key = "\(method) \(path)"
            guard var queue = answers[key], let next = queue.first else {
                return CoreAnswer(status: 404, contentType: "application/problem+json", headers: [], body: Data(
                    #"{"type": "/problems/not-found", "title": "Not Found", "status": 404, "detail": "nothing scripted"}"#.utf8))
            }
            if queue.count > 1 {
                queue.removeFirst()
                answers[key] = queue
            }
            return next
        }
    }
}

extension DeskRoutes.Sent {
    /// A field of what was posted.
    subscript(_ field: String) -> JSONValue? {
        if case .object(let object)? = body { return object[field] }
        return nil
    }
}

/// Wait, a little at a time, until `done` holds or a few seconds pass.
@MainActor
func deskEventually(_ done: () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
    for _ in 0..<200 where !done() {
        try? await Task.sleep(for: .milliseconds(10))
    }
    XCTAssertTrue(done(), "it never came", file: file, line: line)
}

/// A view drawn to PNGs, in the light and the dark, under shells/apple/build/screens/desk: what a person would see,
/// to look at. `ImageRenderer` draws AppKit's controls (buttons, fields, pickers) as placeholders, so the view is
/// drawn as a window draws it, in a hosting view in a window that never shows.
@MainActor
enum DeskScreens {
    static let folder = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .appending(path: "build/screens/desk", directoryHint: .isDirectory)

    @discardableResult
    static func draw<V: View>(_ name: String, width: CGFloat, height: CGFloat? = nil, _ view: V) throws -> [URL] {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var drawn: [URL] = []
        for (look, appearance) in [("light", NSAppearance.Name.aqua), ("dark", .darkAqua)] {
            let host = NSHostingView(rootView: view
                .frame(width: width, height: height, alignment: .topLeading)
                .background(Color(nsColor: .windowBackgroundColor)))
            let size = NSSize(width: width, height: height ?? max(host.fittingSize.height, 40))
            host.frame = NSRect(origin: .zero, size: size)
            let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.appearance = NSAppearance(named: appearance)
            window.contentView = host
            host.layoutSubtreeIfNeeded()
            RunLoop.main.run(until: Date().addingTimeInterval(0.15))
            host.layoutSubtreeIfNeeded()
            guard let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) else {
                throw CocoaError(.fileWriteUnknown)
            }
            host.cacheDisplay(in: host.bounds, to: bitmap)
            let file = folder.appending(path: "\(name)-\(look).png")
            try bitmap.representation(using: .png, properties: [:])?.write(to: file)
            drawn.append(file)
            window.contentView = nil
        }
        return drawn
    }
}

/// Fictional mail and what Claude might say of it: every address at example.test.
enum DeskFixture {
    static let ada = MailCopy.named(key: 101, folder: "INBOX", uid: 11, account: "ada@example.test")
    static let grace = MailCopy.named(key: 102, folder: "INBOX", uid: 12, account: "ada@example.test")

    static let summary = API.Summary(
        account: "ada@example.test", action: "Send Grace the Q3 figures by Friday", cached: false,
        createdAt: "2026-11-15T09:00:00Z", messageCount: 3, model: "claude-haiku-4-5", ok: true,
        participants: ["Grace Hopper", "Ada Lovelace"],
        points: ["The board meets on Monday", "Grace will review a draft first", "Alan has the spreadsheet"],
        scope: "thread", stale: false, threadId: "t-1",
        tldr: "Grace needs the quarterly numbers by Friday and offers to review a draft before the board sees it.")

    static let invite = API.Invite(
        allDay: false,
        attendees: [API.Party(email: "ada@example.test", name: "Ada Lovelace"),
                    API.Party(email: "alan@example.test", name: "Alan Turing"),
                    API.Party(email: "grace@example.test", name: "Grace Hopper")],
        description: "", endIso: "2026-11-18T11:00:00Z", exdates: [], location: "Room 4 · video link in the invite",
        method: "REQUEST", organizer: API.Party(email: "grace@example.test", name: "Grace Hopper"), recurrence: "Weekly on Tuesdays",
        recurrenceId: "", rrule: "FREQ=WEEKLY;BYDAY=TU", sequence: 0, startIso: "2026-11-18T10:00:00Z", status: "CONFIRMED",
        summary: "Design review", tzid: "Europe/London", uid: "inv-1@example.test", when: "Tue 18 Nov, 10:00 – 11:00")

    static let extracted = API.Extracted(
        events: [API.SuggestedEvent(allDay: false, end: "2026-11-20T13:30:00Z", place: "Café Lumen",
                                    start: "2026-11-20T12:30:00Z", title: "Lunch with Grace")],
        model: "claude-haiku-4-5", ok: true,
        tasks: [API.SuggestedTask(due: "2026-11-21", title: "Send the signed form back")])

    static let answered = API.Answered(
        account: "all", answer: "The dentist moved your check-up to Thursday 20 November at 3:30 pm. They asked you to confirm by replying to their message.",
        generated: "2026-11-15T09:00:00Z", model: "claude-sonnet-4-5", ok: true, question: "When is the dentist appointment?",
        sources: [
            API.AskSource(account: "ada@example.test", date: "2026-11-12", folder: "INBOX", from: "Smile Dental",
                          key: 201, snippet: "Your appointment has been moved to Thursday 20 November at 3:30 pm. Please reply to confirm.",
                          subject: "Your appointment has moved", uid: 21),
            API.AskSource(account: "ada@example.test", date: "2026-10-30", folder: "Archive", from: "Smile Dental",
                          key: 202, snippet: "A reminder of your check-up on Tuesday 18 November.", subject: "Check-up reminder", uid: 4),
        ])

    static func preview(_ verb: API.PlanVerb = .trash, count: Int = 42, capped: Bool = false) -> API.PlanPreview {
        API.PlanPreview(cap: 30, capped: capped, count: count, filter: "from:news@shop.example.test older_than:7d", intent: "act",
                        ok: true, plan: "plan-7", sample: [
                            API.PlanSample(from: "Shop Weekly", subject: "Ten deals for your weekend", when: "Nov 3"),
                            API.PlanSample(from: "Shop Weekly", subject: "Last chance: autumn sale", when: "Oct 30"),
                            API.PlanSample(from: "Shop Weekly", subject: "New in: winter coats", when: "Oct 27"),
                        ], snoozePreset: .tomorrow, verb: verb)
    }

    static func item(_ key: Int, from: String, name: String, subject: String, needs: Bool = true, importance: Int = 3,
                     action: String = "", summary: String? = nil, safe: Bool = false, commitment: Bool = false,
                     account: String = "ada@example.test", thread: String? = nil) -> API.DigestItem {
        API.DigestItem(account: account, action: action, alias: "Work", commitmentWorthy: commitment, flagged: false,
                       folder: "INBOX", fromAddr: from, fromName: name, hasAttach: false, importance: importance, key: key,
                       kind: "message", link: "", needsYou: needs, replyWorthy: needs, safeToFile: safe, seen: false,
                       sentTo: [], sentToMore: 0,
                       subject: subject, summary: summary, threadId: thread, uid: key - 100, when: "9:12 am")
    }

    /// `ai` is the engine; "none" is AI off, for want of a key unless `off` says otherwise.
    static func digest(ai: String = "anthropic", off: API.AiOff = .noKey, items: [API.DigestItem]? = nil) -> API.Digest {
        API.Digest(
            accounts: ["ada@example.test"],
            ai: API.AiStatus(backgroundModel: ai == "none" ? "" : "claude-haiku-4-5", cloudModel: false, engine: ai,
                             keyFrom: ai == "none" ? "" : "keychain", lastTs: 0, limitMicros: 15_000_000,
                             model: ai == "none" ? "" : "claude-haiku-4-5", off: ai == "none" ? off : nil,
                             provider: .anthropic),
            aliases: ["ada@example.test": "Work"], generated: "2026-11-15T09:00:00Z",
            items: items ?? [
                item(101, from: "grace@example.test", name: "Grace Hopper", subject: "Q3 figures before the board",
                     importance: 5, action: "Send the figures by Friday",
                     summary: "Grace needs the quarterly numbers by Friday and will review a draft first.", commitment: true),
                item(103, from: "alan@example.test", name: "Alan Turing", subject: "Can you look at my draft?", importance: 4,
                     action: "Read Alan’s draft", summary: "Alan wants comments on chapter two before Wednesday."),
                item(104, from: "school@example.test", name: "Riverside School", subject: "Permission slip for the museum trip",
                     importance: 3, action: "Sign and return the slip", commitment: true),
                item(105, from: "news@shop.example.test", name: "Shop Weekly", subject: "Ten deals for your weekend",
                     needs: false, importance: 1, safe: true),
                item(106, from: "receipts@cafe.example.test", name: "Café Lumen", subject: "Your receipt", needs: false,
                     importance: 1, safe: true),
            ],
            profile: "full", sendAccounts: ["ada@example.test"], signatureDefault: "", signatures: [:],
            stats: API.DigestStats(kept: 3, needs: 3, summarized: 5, total: 5, verified: 5),
            status: [:], sync: API.SyncState(result: "ok", running: false))
    }

    static let brief = API.Brief(
        cached: false, generated: "2026-11-15T08:00:00Z",
        groups: [API.BriefGroup(count: 2, label: "Work", messages: [API.Copy(folder: "INBOX", key: 101, uid: 1),
                                                                    API.Copy(folder: "INBOX", key: 103, uid: 3)]),
                 API.BriefGroup(count: 1, label: "School", messages: [API.Copy(folder: "INBOX", key: 104, uid: 4)])],
        model: "claude-haiku-4-5",
        narrative: "A quiet Sunday. Grace needs the Q3 figures before Monday’s board, and Alan would like comments on his draft. The school trip slip is due back this week.",
        ok: true, stale: false)

    static let upcoming = API.Upcoming(items: [
        API.UpcomingEvent(account: "ada@example.test", allDay: false, end: "2026-11-15T16:00:00Z", folder: "INBOX",
                          invite: true, key: 301, location: "Café Lumen", organizer: "Grace Hopper", recurrence: "",
                          start: "2026-11-15T15:00:00Z", summary: "Coffee with Grace", uid: 31),
        API.UpcomingEvent(account: "ada@example.test", allDay: false, end: "2026-11-18T11:00:00Z", folder: "INBOX",
                          invite: true, key: 302, location: "Room 4", organizer: "Grace Hopper", recurrence: "Weekly on Tuesdays",
                          start: "2026-11-18T10:00:00Z", summary: "Design review", uid: 32),
        API.UpcomingEvent(account: "ada@example.test", allDay: true, end: "2026-11-27", folder: "INBOX", invite: false,
                          key: 303, location: "", organizer: "", recurrence: "", start: "2026-11-26", summary: "Museum trip", uid: 33),
    ], ok: true)

    static let waiting = API.WaitingOnOthers(items: [
        API.WaitingRow(account: "ada@example.test", count: 1, flagged: false, folder: "Sent", hasAttach: false, isJunk: false,
                       key: 401, kind: "message", seen: true, sentTo: [], sentToMore: 0,
                       snippet: "", subject: "Contract for the new office",
                       to: "Ruth Lane <ruth@example.test>", ts: 1_762_819_200, uid: 41, waiting: true, waitingLabel: "",
                       when: "Nov 11"),
    ], ok: true)

    static let tasks = API.TaskList(items: [
        API.Task(account: "ada@example.test", done: false, due: "2026-11-14", folder: "INBOX", fromAddr: "school@example.test",
                 fromName: "Riverside School", id: 1, key: 104, subject: "Permission slip for the museum trip",
                 title: "Sign the museum trip slip", uid: 4),
        API.Task(account: "ada@example.test", done: true, due: nil, folder: nil, fromAddr: "alan@example.test",
                 fromName: "Alan Turing", id: 2, key: nil, subject: "Lunch", title: "Book a table for Thursday", uid: nil),
    ], ok: true)

    static let review = API.Review(
        cached: true, generated: "2026-11-15T07:00:00Z",
        meetings: [API.ReviewMeeting(account: "ada@example.test", allDay: false, folder: "INBOX", key: 302, past: false,
                                     start: "2026-11-18T10:00:00Z", summary: "Design review", uid: 32)],
        model: "claude-sonnet-4-5",
        narrative: "A busy week: you answered most of what came in, cleared two newsletters for good, and the Q3 figures are the one thing still open.",
        needs: [API.ReviewNeeded(account: "ada@example.test", action: "Send the figures", folder: "INBOX", importance: 5,
                                 key: 101, subject: "Q3 figures before the board", uid: 1, who: "Grace Hopper")],
        next: ["Send Grace the Q3 figures", "Return the museum trip slip"], ok: true, stale: false,
        stats: API.ReviewStats(byBucket: [:], cleaned: 14, handled: 22, needsYou: 1, received: 87, replyLater: 2, sent: 19,
                               waitingOn: 1),
        waiting: [API.ReviewAwaited(account: "ada@example.test", folder: "Sent", key: 401, sentTs: 1_762_819_200,
                                    subject: "Contract for the new office", threadId: "t-9", to: "Ruth Lane", uid: 41)],
        weekStart: "2026-11-09")

    static let screener = API.ScreenerQueue(account: "ada@example.test", items: [
        API.ScreenerItem(count: 3, domain: "studio.example.test", fromAddr: "hello@studio.example.test",
                         fromName: "Studio Nine", when: "Nov 13"),
        API.ScreenerItem(count: 1, domain: "example.test", fromAddr: "marie@example.test", fromName: "Marie Curie",
                         when: "Nov 14"),
        API.ScreenerItem(count: 12, domain: "deals.example.test", fromAddr: "offers@deals.example.test",
                         fromName: "Daily Deals", when: "today"),
    ], ok: true, total: 3)

    static let suggestions = API.ScreenerSuggestions(account: "ada@example.test", asked: 3, ok: true, suggestions: [
        API.ScreenerSuggestion(confidence: 0.92, fromAddr: "marie@example.test", verdict: .keep,
                               why: "A person writing to you about the lab visit."),
        API.ScreenerSuggestion(confidence: 0.95, fromAddr: "offers@deals.example.test", verdict: .block,
                               why: "Daily promotions you never open."),
    ])

    static let flood = API.NoisySenders(generated: "2026-11-15T09:00:00Z", ok: true, senders: [
        API.NoisySender(accounts: [API.SenderAccount(count: 40, email: "ada@example.test")], bucket: "newsletters/marketing",
                        count: 40, domain: "shop.example.test", fromAddr: "news@shop.example.test", fromName: "Shop Weekly",
                        lastSeen: "today", luLikely: true, noiseScore: 9.4, unseen: 36, unseenRatio: 0.9),
        API.NoisySender(accounts: [API.SenderAccount(count: 12, email: "ada@example.test"),
                                   API.SenderAccount(count: 6, email: "ada.home@example.test")],
                        bucket: "automated-notifications", count: 18, domain: "alerts.example.test",
                        fromAddr: "noreply@alerts.example.test", fromName: "Alerts", lastSeen: "Nov 12", luLikely: false,
                        noiseScore: 6.1, unseen: 4, unseenRatio: 0.22),
    ])

    static let senderPreview = API.SenderPreview(
        accounts: ["ada@example.test"], confirmToken: "tok-1", count: 40, domain: "shop.example.test",
        fromAddr: "news@shop.example.test", ok: true, reversible: API.Reversible(block: true, sweep: true, unsubscribe: false),
        sweepCapped: false, sweepCount: 31, unseen: 36,
        unsub: API.WayOut(httpsHost: "shop.example.test", mailtoTo: nil, method: "one-click"))

    static let stats = API.Stats(
        accounts: [API.AccountStats(account: "ada@example.test", alias: "Work",
                                    byBucket: ["personal/protected": 40, "newsletters/marketing": 30, "receipts/transactional": 12,
                                               "spam/scam": 6],
                                    inInbox: 88, junk: 36, keep: 52, kind: "imap", sweepable: 20, topSenders: [:])],
        buckets: [API.Bucket(isJunk: false, name: "personal/protected"), API.Bucket(isJunk: true, name: "newsletters/marketing"),
                  API.Bucket(isJunk: false, name: "receipts/transactional"), API.Bucket(isJunk: true, name: "spam/scam")],
        generated: "2026-11-15T09:00:00Z", totals: API.Totals(inInbox: 88, junk: 36, keep: 52))

    static let rules = API.Rules(blockDomains: ["deals.example.test"], blockSenders: ["noreply@alerts.example.test"],
                                 expireHours: [:],
                                 filters: [API.MailFilter(id: "f1", q: "from:grace@example.test", read: false, star: true),
                                           API.MailFilter(id: "f2", q: "subject:receipt", read: true, star: false)],
                                 keepDomains: ["school.example.test"], keepSenders: ["grace@example.test"])

    static let undoneAnswer = #"{"ok": true, "remaining": 0, "label": "Snoozed"}"#
}
