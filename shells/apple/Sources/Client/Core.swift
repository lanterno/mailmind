import Foundation

/// The app's core running in this process, as SwiftUI meets it: the routes the page calls, answered without a port,
/// their answers decoded into the generated API types, a refusal thrown as its `Problem`, and the event stream as an
/// `AsyncStream`.
///
/// Every call crosses on a queue of the core's own, never the main thread: a request blocks until its answer is
/// whole, and opening a home reads the disk and may wait on the Keychain.
public final class Core: Sendable {
    private let host: CoreHost
    private static let queue = DispatchQueue(label: "mailmind.core", qos: .userInitiated, attributes: .concurrent)

    private init(host: CoreHost) {
        self.host = host
    }

    /// Open `home`: its database made or upgraded, the app assembled and its background work started.
    public static func open(_ home: CoreHome) async throws -> Core {
        let host = try await onQueue { try CoreHost.open(home: home) }
        return Core(host: host)
    }

    /// What `route` answers to a GET with `query`.
    public func get<R: GetRoute>(_ route: R.Type, _ query: KeyValuePairs<String, String?> = [:]) async throws
        -> R.Answer where R.Answer: Decodable
    {
        try Self.decoded(R.Answer.self, from: await send("GET", R.path + Self.query(query), Data()))
    }

    /// What `route` answers to a POST of `body`.
    public func post<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer where R.Answer: Decodable {
        let sent = try JSONEncoder().encode(body)
        return try Self.decoded(R.Answer.self, from: await send("POST", R.path, sent))
    }

    /// What `route` answers to a POST of `body` with the person's leave to go over today's AI limit, as the page sends
    /// one again once they say yes to its question.
    public func postOverLimit<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer
        where R.Answer: Decodable
    {
        let sent = try JSONEncoder().encode(body)
        return try Self.decoded(R.Answer.self, from: await send("POST", R.path, sent, overLimit: true))
    }

    /// A route that answers no JSON (a mail body's frame, an attachment): its bytes and their type, or its problem.
    public func document<R: GetRoute>(_ route: R.Type, _ query: KeyValuePairs<String, String?> = [:]) async throws
        -> NotJSON where R.Answer == NotJSON
    {
        let answer = await send("GET", R.path + Self.query(query), Data())
        guard (200..<300).contains(answer.status) else { throw Problem(answer) }
        return NotJSON(data: answer.body, contentType: answer.contentType)
    }

    /// Any request as the page would send it, answered as it would read it, a refusal included: for what loads a
    /// document by its URL (the reader's frame). `overLimit` carries the person's leave to go over today's AI limit
    /// (`X-MailMind-AI-Over-Limit: allow`), the one header the core lets a shell add.
    public func send(_ method: String, _ target: String, _ body: Data, overLimit: Bool = false) async -> CoreAnswer {
        let host = host
        return await Self.onQueue {
            overLimit ? host.requestOverLimit(method: method, target: target, body: body)
                : host.request(method: method, target: target, body: body)
        }
    }

    /// The event stream from now on, beginning with its `hello`. It ends when the core closes, and cancelling the
    /// task that reads it lets the stream go. A refusal (too many streams open) is thrown.
    public func events() async throws -> AsyncStream<CoreEvent> {
        let (stream, continuation) = AsyncStream<CoreEvent>.makeStream(bufferingPolicy: .unbounded)
        let listener = Listener(continuation)
        let host = host
        let subscription: EventSubscription
        do {
            subscription = try await Self.onQueue { try host.subscribe(listener: listener) }
        } catch HostProblem.Refused(let answer) {
            continuation.finish()
            throw Problem(answer)
        }
        continuation.onTermination = { _ in subscription.cancel() }
        return stream
    }

    /// The Mac woke from sleep, or its network changed: the core opens IDLE's connections afresh and syncs every
    /// account now. It returns at once, the work going on in the background.
    public func woke() {
        let host = host
        Self.queue.async { host.woke() }
    }

    /// End the streams, stop the background work and wait for the work requests set going. Every request from then
    /// on is answered 503.
    public func close() async {
        let host = host
        await Self.onQueue { host.close() }
    }

    // MARK: Crossing

    private static func onQueue<T: Sendable>(_ work: @escaping @Sendable () throws -> T) async throws -> T {
        try await withCheckedThrowingContinuation { done in
            queue.async { done.resume(with: Result { try work() }) }
        }
    }

    private static func onQueue<T: Sendable>(_ work: @escaping @Sendable () -> T) async -> T {
        await withCheckedContinuation { done in
            queue.async { done.resume(returning: work()) }
        }
    }

    static func decoded<T: Decodable>(_ type: T.Type, from answer: CoreAnswer) throws -> T {
        guard (200..<300).contains(answer.status) else { throw Problem(answer) }
        do {
            return try JSONDecoder().decode(T.self, from: answer.body)
        } catch {
            throw Unreadable(type: String(describing: T.self), status: Int(answer.status), why: String(describing: error))
        }
    }

    /// A query string as the page's client writes one: each value percent-encoded but for the unreserved characters,
    /// `+` included, so the server reads it back as it was. A nil value is left out.
    static func query(_ pairs: KeyValuePairs<String, String?>) -> String {
        let given = pairs.compactMap { key, value in value.map { "\(encoded(key))=\(encoded($0))" } }
        return given.isEmpty ? "" : "?" + given.joined(separator: "&")
    }

    private static let unreserved = CharacterSet(charactersIn:
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")

    private static func encoded(_ text: String) -> String {
        text.addingPercentEncoding(withAllowedCharacters: unreserved) ?? ""
    }
}

/// A route's refusal: the problem document the page gets (RFC 9457), `type` saying which kind, `detail` what a person
/// is told when the server words it, and whatever else the page acts on (`busy`, `read_only`) in `more`.
public struct Problem: Error, Hashable, Sendable, CustomStringConvertible {
    public let status: Int
    public let type: String
    public let title: String
    public let detail: String?
    public let more: [String: JSONValue]

    /// The answer's problem document, or one that says only its status when it carries none.
    init(_ answer: CoreAnswer) {
        status = Int(answer.status)
        var fields = (try? JSONDecoder().decode([String: JSONValue].self, from: answer.body)) ?? [:]
        func text(_ name: String) -> String? {
            if case .string(let value)? = fields.removeValue(forKey: name) { return value }
            return nil
        }
        type = text("type") ?? "about:blank"
        title = text("title") ?? HTTPURLResponse.localizedString(forStatusCode: Int(answer.status))
        detail = text("detail")
        fields.removeValue(forKey: "status")
        more = fields
    }

    /// The server's no, which asking again won't change: a 4xx.
    public var isRefusal: Bool { (400..<500).contains(status) }

    /// MailMind runs read only, and turned down, before anything was queued or hidden, a change that would have
    /// reached a mail server (a 409 with `read_only`).
    public var isReadOnly: Bool { more["read_only"] == .bool(true) }

    /// What to tell a person: the server's words, else its title.
    public var description: String { detail ?? title }
}

/// An answer that didn't read as its route's type: the app and the server disagree on a schema, which
/// `just apple check-api` catches before it ships.
public struct Unreadable: Error, Hashable, Sendable {
    public let type: String
    public let status: Int
    public let why: String
}

/// Hands each event to the stream, on the core's thread, in the order sent.
private final class Listener: EventListener {
    private let continuation: AsyncStream<CoreEvent>.Continuation

    init(_ continuation: AsyncStream<CoreEvent>.Continuation) {
        self.continuation = continuation
    }

    func onEvent(event: CoreEvent) {
        continuation.yield(event)
    }

    func onEnd() {
        continuation.finish()
    }
}

extension CoreEvent {
    /// The event's data as `type`, or nil when it doesn't read as one.
    public func decoded<T: Decodable>(_ type: T.Type) -> T? {
        try? JSONDecoder().decode(T.self, from: Data(data.utf8))
    }
}

extension CoreHome {
    /// What the app's data folder is called in Application Support, whatever the build's bundle identifier.
    public static let folderName = "io.github.lanterno.mailmind"

    /// The variables the app reads, passed on from this process's environment when set: those MailMind declares
    /// (`MAILMIND_PROFILE` for a read-only run, a demo's database key and its fake Claude API), and the few a
    /// program it starts needs. Nothing else of the environment reaches the core.
    static let passedOn = [
        "MAILMIND_PROFILE", "MAILMIND_NOSYNC", "MAILMIND_WORKERS", "MAILMIND_POOL", "MAILMIND_WARM_FOLDERS",
        "MAILMIND_LOG_LEVEL", "MAILMIND_RECONCILE_SECS", "MAILMIND_EXPIRE_SECS", "MAILMIND_NO_DRAFT_TICK",
        "MAILMIND_NO_BATCHES", "MAILMIND_NO_SCHEMA", "MAILMIND_MODEL", "MAILMIND_PROTON_BRIDGE_PASS",
        "MAILMIND_PROTON_OP_ITEM", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "MAILMIND_DB_KEY",
        "PATH", "HOME", "TMPDIR", "TZ",
        // The core's test world: its clock paused, standing at a time of the test's. The core reads them only in a
        // home with a keychain stand-in, which a person's never has.
        "MAILMIND_TEST_CLOCK", "MAILMIND_TEST_NOW",
    ]

    /// The variables of a run that is not a person's: a measure or demo run, a test, a keychain's stand-in. Such a run
    /// opens only the home `MAILMIND_HOME` names, never the real folder and the login Keychain.
    static let notAPersonsRun = [
        "MAILMIND_MEASURE", "MAILMIND_TEST_KEYCHAIN", "XCTestConfigurationFilePath", "XCTestSessionIdentifier",
    ]

    /// Where Settings keeps the read-only switch ("Read only: MailMind changes nothing on your mail servers"), in the
    /// app's defaults. The core reads it once, as it opens: a change takes effect the next time MailMind opens.
    public static let readOnlyKey = "readOnly"

    /// Whether a person turned the read-only switch on.
    public static var readOnlyChosen: Bool { UserDefaults.standard.bool(forKey: readOnlyKey) }

    /// The variables the core is handed: those passed on from the environment, and `MAILMIND_PROFILE=read-only` when
    /// the switch is on, the environment names no profile of its own, and this is a person's run. The switch is the
    /// person's own MailMind's: a demo or test run keeps preferences of its own (`RunPreferences`), and runs only as it
    /// was started.
    static func variables(_ environment: [String: String], readOnly: Bool) -> [String: String] {
        var variables = environment.filter { passedOn.contains($0.key) }
        if readOnly, isAPersonsRun(environment), variables["MAILMIND_PROFILE"] == nil {
            variables["MAILMIND_PROFILE"] = "read-only"
        }
        return variables
    }

    /// Whether this is a person's run: one that names no home of its own and isn't a measure, demo or test run. Only
    /// such a run may be opened again by itself (a relaunch through Finder's way of opening apps), since another run's
    /// home and keychain are in its environment, which a relaunch wouldn't carry.
    public static func isAPersonsRun(_ environment: [String: String] = ProcessInfo.processInfo.environment) -> Bool {
        (environment["MAILMIND_HOME"] ?? "").isEmpty && !notAPersonsRun.contains { environment[$0] != nil }
    }

    /// The home this process runs: `MAILMIND_HOME`'s folder when it names one (a demo's or a test's), with the file
    /// `MAILMIND_TEST_KEYCHAIN` names standing in for the login Keychain; else, in a person's run only, MailMind's own
    /// folder in Application Support, made if it isn't there, on the login Keychain. Read only when `readOnly`, as the
    /// switch in Settings says.
    public static func ofThisProcess(_ environment: [String: String] = ProcessInfo.processInfo.environment,
                                     readOnly: Bool = readOnlyChosen) throws -> CoreHome
    {
        let variables = variables(environment, readOnly: readOnly)
        if let named = environment["MAILMIND_HOME"], !named.isEmpty {
            let standIn = environment["MAILMIND_TEST_KEYCHAIN"].flatMap { $0.isEmpty ? nil : $0 }
            return CoreHome(folder: named, variables: variables, standIn: standIn)
        }
        if let variable = notAPersonsRun.first(where: { environment[$0] != nil }) {
            throw NoHomeNamed(variable: variable)
        }
        let support = try FileManager.default.url(
            for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        let folder = support.appending(path: folderName, directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return CoreHome(folder: folder.path(percentEncoded: false), variables: variables, standIn: nil)
    }
}

/// A measure, demo or test run that named no home: it opens nothing rather than the real folder.
public struct NoHomeNamed: Error, Hashable, Sendable, CustomStringConvertible {
    public let variable: String

    public var description: String {
        "\(variable) is set, so MailMind opens only the folder MAILMIND_HOME names, and none is named."
    }
}
