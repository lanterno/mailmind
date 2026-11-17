#if os(macOS)
import Foundation

/// What the assistant's, curation's, attention's and the calendar's models ask of the core: a route's GET or POST as
/// its generated types (a POST a person started may go over today's AI limit with their leave), or a document's
/// bytes. `Core` is one; a test's stand-in is another, so each turn a model takes can be held without a mail server or
/// an AI service.
protocol RouteCaller: OverLimitRoutes {
    func get<R: GetRoute>(_ route: R.Type, _ query: KeyValuePairs<String, String?>) async throws -> R.Answer
        where R.Answer: Decodable
    func document<R: GetRoute>(_ route: R.Type, _ query: KeyValuePairs<String, String?>) async throws -> NotJSON
        where R.Answer == NotJSON
}

extension Core: RouteCaller {}

/// The core the window opened, once it has, and the window's question for an AI call a person started at today's
/// limit. The models are made with the window, before the core is open, and each asks this for it when a person acts.
@MainActor
final class CoreLink {
    var routes: (any RouteCaller)?
    /// The window's alert in the app, a test's own answer in a test. With none, a call at the limit only says so.
    var asksOverLimit: AskOverLimit?

    init(_ routes: (any RouteCaller)? = nil, asking: AskOverLimit? = nil) {
        self.routes = routes
        asksOverLimit = asking
    }
}

/// How a refusal reads to a person, as the page reads it: the server's words, or the page's own where it has some.
enum DeskRefusal {
    /// AI is off (a 409 with `ai_off`): turned off in Settings, or no key, URL or model; its detail says which.
    static func isAIOff(_ error: any Error) -> Bool {
        (error as? Problem)?.more["ai_off"] == .bool(true)
    }

    /// Why AI is off, as the refusal says it ("AI is off: there is no API key"), else only that it is.
    static func aiOff(_ error: any Error) -> String {
        words(error, else: String(localized: "AI is off"))
    }

    /// Today's AI spending has reached the daily limit (a 402 with `over_limit`). A call a person started has already
    /// asked whether to go over it (`post(_:_:asking:)`), so this is a no, or a call the app made on its own.
    static func isOverLimit(_ error: any Error) -> Bool {
        (error as? Problem)?.more["over_limit"] == .bool(true)
    }

    /// The server's words for a refusal, else `fallback`: what didn't reach the server or didn't read says only that.
    /// Read only's refusal says so in the app's own words.
    static func words(_ error: any Error, else fallback: String) -> String {
        guard let problem = error as? Problem else { return fallback }
        if problem.isReadOnly { return ReadOnlyWords.refused }
        return problem.detail.flatMap { $0.isEmpty ? nil : $0 } ?? fallback
    }

    /// A refusal as `ApiError.message` reads it on the page: its detail, else its title.
    static func message(_ error: any Error) -> String {
        (error as? Problem)?.description ?? ""
    }
}

/// A count and its noun, as the page words one: "1 thread", "3 threads". The page speaks English only.
func countNoun(_ count: Int, _ one: String, _ many: String) -> String {
    "\(count) \(count == 1 ? one : many)"
}

/// One id for a bulk action, so one Undo reverses all of it, as the page's `undoGroup` makes one.
func newUndoGroup() -> String {
    "ug-\(UUID().uuidString.lowercased())"
}

extension MailCopy {
    /// A copy a route named by its parts: a cited source, a brief's card, a task's message.
    static func named(key: Int, folder: String, uid: Int, account: String) -> MailCopy {
        MailCopy(deskKey: key, folder: folder, uid: uid, account: account)
    }

    private init(deskKey key: Int, folder: String, uid: Int, account: String) {
        self.key = key
        self.folder = folder
        self.uid = uid
        self.account = account
    }

    /// The copy as a request names one message.
    var namesAMessage: API.NamesAMessage {
        API.NamesAMessage(folder: folder, key: key, uid: uid)
    }

    var aCopy: API.ACopy {
        API.ACopy(folder: folder, key: key, uid: uid)
    }
}
#endif
