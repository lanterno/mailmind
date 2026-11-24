import Foundation
import XCTest

/// A server for the mailbox's tests: each route answers what the test says, as the core would, and every request is
/// kept so a test can say what was asked. Every address is at example.test; nothing reaches a core, a network or a
/// keychain.
final class FakeServer: @unchecked Sendable {
    struct Asked: Equatable {
        let method: String
        let path: String
        let query: [String: String]
        let body: Data
    }

    typealias Answer = @Sendable (Asked) -> CoreAnswer

    private let lock = NSLock()
    private var routes: [String: Answer] = [:]
    private var log: [Asked] = []

    /// The wire the mailbox calls.
    var wire: Wire {
        Wire { [self] method, target, body in self.answer(method, target, body) }
    }

    var asked: [Asked] {
        lock.withLock { log }
    }

    func asked(_ path: String) -> [Asked] {
        asked.filter { $0.path == path }
    }

    /// Answer `method path` with `answer`'s JSON, or with what `make` says to each request.
    func on(_ method: String, _ path: String, _ answer: some Encodable & Sendable) {
        let data = try! JSONEncoder().encode(answer)
        on(method, path) { _ in Self.ok(data) }
    }

    func on(_ method: String, _ path: String, _ make: @escaping Answer) {
        lock.withLock { routes["\(method) \(path)"] = make }
    }

    static func ok(_ data: Data) -> CoreAnswer {
        CoreAnswer(status: 200, contentType: "application/json", headers: [], body: data)
    }

    static func ok(_ answer: some Encodable) -> CoreAnswer {
        ok(try! JSONEncoder().encode(answer))
    }

    /// A refusal as the server words one.
    static func refused(_ status: UInt16, _ detail: String, more: [String: Bool] = [:]) -> CoreAnswer {
        var fields: [String: JSONValue] = ["type": .string("/problems/refused"), "title": .string("Refused"),
                                           "status": .number(Double(status)), "detail": .string(detail)]
        for (key, value) in more { fields[key] = .bool(value) }
        return CoreAnswer(status: status, contentType: "application/problem+json", headers: [],
                          body: try! JSONEncoder().encode(JSONValue.object(fields)))
    }

    private func answer(_ method: String, _ target: String, _ body: Data) -> CoreAnswer {
        let parts = URLComponents(string: target)
        let path = parts?.path ?? target
        var query: [String: String] = [:]
        for item in parts?.queryItems ?? [] { query[item.name] = item.value ?? "" }
        let asked = Asked(method: method, path: path, query: query, body: body)
        let route = lock.withLock {
            log.append(asked)
            return routes["\(method) \(path)"]
        }
        if let route { return route(asked) }
        if method == "GET", path == API.Get.MessageRow.path { return listedCopy(asked) }
        return Self.refused(404, "No such route in this test")
    }

    /// One message's row, as the server reads it from what its folder lists: here, the folder's listing the test gives
    /// `/api/messages`, read whole, unasked and unlogged. Not found where it holds none.
    private func listedCopy(_ asked: Asked) -> CoreAnswer {
        guard let listing = lock.withLock({ routes["GET \(API.Get.Messages.path)"] }) else {
            return Self.refused(404, "No such route in this test")
        }
        var query = asked.query
        let uid = query.removeValue(forKey: "uid").flatMap { Int($0) }
        let key = query.removeValue(forKey: "key").flatMap { Int($0) }
        let folder = query["folder"] ?? "INBOX"
        query["offset"] = "0"
        query["limit"] = "1000000"
        let read = listing(Asked(method: "GET", path: API.Get.Messages.path, query: query, body: Data()))
        guard case .folderListing(let page)? = try? Core.decoded(API.MessagesAnswer.self, from: read),
              let row = page.items.first(where: {
                  $0.folder == folder && (uid == nil || $0.uid == uid) && (key == nil || $0.key == key)
              })
        else { return Self.refused(404, "not listed") }
        return Self.ok(API.ListedCopy(ok: true, row: row))
    }
}

/// Fictional mail for the mailbox's tests and pictures.
enum Fixture {
    static let ada = "ada@example.test"
    static let work = "work@example.test"

    static func row(_ key: Int, account: String = ada, folder: String = "INBOX", seen: Bool = true, flagged: Bool = false,
                    attach: Bool = false, count: Int? = nil, thread: String? = nil, junk: Bool = false,
                    needs: Bool = false, action: String = "", summary: String = "", from: String = "Grace Hopper",
                    fromAddr: String? = nil, to: [API.Addressee] = [], more: Int = 0,
                    subject: String? = nil, when: String = "10:42") -> API.FolderRow {
        API.FolderRow(account: account, action: action, bucket: junk ? "promo/deals" : nil, commitmentWorthy: false,
                      count: count, flagged: flagged, folder: folder,
                      fromAddr: fromAddr ?? from.lowercased().replacingOccurrences(of: " ", with: ".") + "@example.test",
                      fromName: from, hasAttach: attach, isJunk: junk, key: key, needsYou: needs, replyWorthy: needs,
                      safeToFile: false, seen: seen, sentTo: to, sentToMore: more,
                      snippet: "A line of fictional mail, number \(key).",
                      subject: subject ?? "Message \(key)", summary: summary, threadCount: count,
                      threadId: thread ?? (count.map { _ in "t\(key)" }), ts: 1_790_000_000 - key * 600, uid: key, when: when)
    }

    static func hit(_ key: Int, subject: String, date: String = "2026-11-02", from: String = "Shop", seen: Bool = true,
                    account: String = ada) -> API.SearchHit {
        API.SearchHit(account: account, commitmentWorthy: false, date: date, flagged: false, folder: "INBOX",
                      fromAddr: "\(from.lowercased())@example.test", fromName: from, hasAttach: false, isJunk: false,
                      key: key, needsYou: false, replyWorthy: false, safeToFile: false, seen: seen,
                      sentTo: [], sentToMore: 0,
                      snippet: "Thanks for your order.", subject: subject, uid: key, when: "Nov 2")
    }

    /// A folder on the rail: a person's own (`custom`) one they may change, unless the server says otherwise.
    static func folder(_ name: String, role: String, unseen: Int = 0, filing: Bool = true, changeable: Bool? = nil)
        -> API.RailFolder
    {
        API.RailFolder(changeable: changeable ?? (role == "custom"), name: name,
                       ownName: name.split(separator: "/").last.map(String.init) ?? name, role: role,
                       takesFiling: filing, total: 100, unseen: unseen)
    }

    static let adaFolders = [
        folder("INBOX", role: "inbox", unseen: 4), folder("Sent", role: "sent"), folder("Drafts", role: "drafts", filing: false),
        folder("Archive", role: "archive"), folder("Junk", role: "junk", unseen: 2), folder("Trash", role: "trash"),
        folder("Receipts", role: "custom"),
    ]
    static let workFolders = [
        folder("INBOX", role: "inbox", unseen: 1), folder("[Gmail]/Sent Mail", role: "sent"),
        folder("[Gmail]/All Mail", role: "all"), folder("[Gmail]/Trash", role: "trash"),
    ]

    /// The inbox the pictures show: unread and read, starred, with files, a conversation, junk, and what needs you.
    static let inbox: [API.FolderRow] = [
        row(1, seen: false, needs: true, action: "Reply by Friday", summary: "Ada asks whether the launch can move to Thursday.",
            from: "Ada Lovelace", subject: "Launch date"),
        row(2, account: work, seen: false, flagged: true, count: 3, from: "Alan Turing", subject: "Re: The engine's notes"),
        row(3, attach: true, from: "Library of Ash Hollow", subject: "Your loan is due", when: "Yesterday"),
        row(4, seen: false, junk: true, from: "Deals Daily", subject: "Last chance", when: "Yesterday"),
        row(5, flagged: true, attach: true, from: "Katherine Johnson", subject: "Orbit figures", when: "Mon"),
        row(6, account: work, from: "Edsger Dijkstra", subject: "On the cruelty of really teaching", when: "Sun"),
    ] + (7...40).map { row($0, seen: $0 % 3 != 0, from: "Sender \($0)", when: "Oct \($0 % 28 + 1)") }

    static func preferences(conversations: Bool = true, scope: API.SearchScope = .all, singleKeys: Bool = true) -> API.PreferencesAnswer {
        API.PreferencesAnswer(ok: true, preferences: API.Preferences(
            aiBaseUrl: "", aiDailyLimitUsd: 15, aiEnabled: true, aiModel: "", aiProvider: .anthropic, autoTidy: false,
            conversations: conversations, density: .comfortable, draftAhead: false, draftTone: .balanced, guideOffered: true, highContrast: false, mailColors: .auto, morningBrief: false, notifyMeetings: false,
            notifyNewMail: false, quietFrom: "22:00", quietHours: false, quietUntil: "07:00", readFont: .sans, readSize: .m,
            readWidth: .medium, searchScope: scope, singleKeys: singleKeys, theme: .day, undoSendSeconds: 10, weeklyReview: false))
    }

    /// `colours` is the places the core keeps; none, as a server that keeps none says.
    static func digest(accounts: [String] = [ada, work], ai: Bool = true, off: API.AiOff = .noKey,
                       readOnly: Bool = false, colours: [String: Int] = [:]) -> API.Digest {
        API.Digest(accounts: accounts,
                   ai: API.AiStatus(backgroundModel: ai ? "test" : "", cloudModel: false, engine: ai ? "api" : "none", keyFrom: "test",
                                    lastTs: 0, limitMicros: 0,
                                    model: ai ? "test" : "", off: ai ? nil : off, provider: .anthropic),
                   aliases: [ada: "Ada", work: nil], colours: colours, generated: "2026-11-15T09:00:00Z", items: [],
                   profile: readOnly ? "read-only" : "full", sendAccounts: accounts, signatureDefault: "", signatures: [:],
                   stats: API.DigestStats(kept: 0, needs: 1, summarized: 0, total: 40, verified: 0),
                   status: [:], sync: API.SyncState(result: "ok", running: false))
    }

    /// A server with the sidebar's routes answered, and `rows` in every account's folders and inboxes.
    static func server(rows: [API.FolderRow] = inbox, grouped: Bool = true, singleKeys: Bool = true) -> FakeServer {
        let server = FakeServer()
        server.on("GET", API.Get.Digest.path, digest())
        server.on("GET", API.Get.Folders.path) { asked in
            let account = asked.query["account"] ?? ""
            return FakeServer.ok(API.Folders(account: account, folders: account == work ? workFolders : adaFolders,
                                             muted: 0, ok: true, replylater: account == ada ? 2 : 0, snoozed: 1, waiting: 0))
        }
        server.on("GET", API.Get.FoldersAcross.path, API.ViewCounts(muted: 0, ok: true, replylater: 2, snoozed: 1, waiting: 3))
        server.on("GET", API.Get.Labels.path) { asked in
            FakeServer.ok(API.Labels(labels: asked.query["account"] == work
                ? [API.Label(count: 12, name: "Projects"), API.Label(count: 3, name: "Travel \"2026\"")] : [], ok: true))
        }
        server.on("GET", API.Get.Views.path, API.Views(ok: true, views: [
            API.ListedView(builtin: true, id: "__lane_feed__", name: "Feed", q: "category:newsletter"),
            API.ListedView(builtin: true, id: "__lane_papertrail__", name: "Paper trail", q: "category:receipt"),
            API.ListedView(builtin: false, id: "v1", name: "From Grace", q: "from:grace"),
        ]))
        server.on("GET", API.Get.Preferences.path, preferences(conversations: grouped, singleKeys: singleKeys))
        let page: @Sendable (FakeServer.Asked) -> (offset: Int, items: [API.FolderRow], hasMore: Bool) = { asked in
            let offset = Int(asked.query["offset"] ?? "0") ?? 0, limit = Int(asked.query["limit"] ?? "200") ?? 200
            let items = Array(rows.dropFirst(offset).prefix(limit))
            return (offset, items, offset + items.count < rows.count)
        }
        server.on("GET", API.Get.Messages.path) { asked in
            let (offset, items, more) = page(asked)
            return FakeServer.ok(API.MessagesAnswer.folderListing(API.FolderListing(
                account: asked.query["account"], folder: asked.query["folder"] ?? "INBOX", grouped: asked.query["group"] != nil,
                hasMore: more, items: items, offset: offset, ok: true, total: rows.count)))
        }
        server.on("GET", API.Get.Inboxes.path) { asked in
            let (offset, items, more) = page(asked)
            return FakeServer.ok(API.InboxesListing(folder: "INBOX", grouped: asked.query["group"] != nil, hasMore: more,
                                                    items: items, offset: offset, ok: true, total: rows.count))
        }
        return server
    }
}

/// Wait on the main actor until `done`, or fail after five seconds.
@MainActor
func until(_ done: @MainActor () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
    let started = ContinuousClock.now
    while !done() {
        guard ContinuousClock.now - started < .seconds(5) else {
            XCTFail("still waiting after five seconds", file: file, line: line)
            return
        }
        try? await Task.sleep(for: .milliseconds(2))
    }
}
