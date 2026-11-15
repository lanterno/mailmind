#if os(macOS)
import AppKit

/// Today's AI spending and its daily limit, in millionths of a dollar, as an over-limit refusal says them: a 402 with
/// `over_limit`, `spent_micros` and `limit_micros`.
struct OverLimit: Equatable, Sendable {
    let spentMicros: Int
    let limitMicros: Int

    /// The question's words, the page's (`lib/overLimit.tsx`).
    static let title = String(localized: "Today’s AI limit is reached")
    static let goOver = String(localized: "Go over the limit")
    static let notNow = String(localized: "Not now")

    var question: String {
        String(localized: """
            MailMind has spent \(StatusWords.dollars(spentMicros)) on AI today, and its daily limit is \
            \(StatusWords.dollars(limitMicros)). Go over the limit for this?
            """)
    }
}

extension OverLimit {
    /// What an over-limit refusal says, or nil for any other error. A figure it leaves out counts as nothing, as the
    /// page counts it.
    init?(_ error: any Error) {
        guard let problem = error as? Problem, problem.status == 402, problem.more["over_limit"] == .bool(true) else {
            return nil
        }
        func micros(_ name: String) -> Int {
            if case .number(let value)? = problem.more[name], value.isFinite { return Int(value) }
            return 0
        }
        self.init(spentMicros: micros("spent_micros"), limitMicros: micros("limit_micros"))
    }
}

/// Asks the person whether one AI call they started may go over today's limit: true sends it again with their leave.
typealias AskOverLimit = @MainActor @Sendable (OverLimit) async -> Bool

/// Routes an AI call a person started goes on: a POST, and the same POST with their leave to go over today's AI limit.
/// `Core` is one, and so is each area's stand-in for it.
protocol OverLimitRoutes: Sendable {
    func post<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer where R.Answer: Decodable
    /// `post`, with `X-MailMind-AI-Over-Limit: allow`: the person said this one may go over the limit.
    func postOverLimit<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer
        where R.Answer: Decodable
}

extension OverLimitRoutes {
    /// An AI call a person started, as the page's `request` sends one with `askOverLimit`: at today's limit `ask` asks
    /// them, and on yes the same POST goes again with their leave, once. With nothing to ask (a call the app makes on
    /// its own), or on no, the refusal is thrown as it came, so the caller says the limit is reached.
    func post<R: PostRoute>(_ route: R.Type, _ body: R.Body, asking ask: AskOverLimit?) async throws -> R.Answer
        where R.Answer: Decodable
    {
        do {
            return try await post(route, body)
        } catch {
            guard let ask, let over = OverLimit(error), await ask(over) else { throw error }
            return try await postOverLimit(route, body)
        }
    }
}

/// The question on screen. Calls that reach the limit while it is up share its one answer, as the page's do, so
/// several at once ask once.
@MainActor
final class OverLimitQuestion {
    /// The window's: an alert on the window in front.
    static let onScreen = OverLimitQuestion(present: alert)

    private let present: @MainActor (OverLimit) async -> Bool
    private var shown: Task<Bool, Never>?

    init(present: @escaping @MainActor (OverLimit) async -> Bool) {
        self.present = present
    }

    func ask(_ over: OverLimit) async -> Bool {
        if let shown { return await shown.value }
        let asking = Task { await present(over) }
        shown = asking
        let yes = await asking.value
        shown = nil
        return yes
    }

    /// A sheet on the key window, or on the sheet it already shows; with no window, the app's own alert. Not Now is
    /// the default, as the page's dialog focuses it, so Return never spends.
    private static func alert(_ over: OverLimit) async -> Bool {
        let alert = NSAlert()
        alert.messageText = OverLimit.title
        alert.informativeText = over.question
        alert.addButton(withTitle: OverLimit.notNow)
        alert.addButton(withTitle: OverLimit.goOver)
        var window = NSApp.keyWindow ?? NSApp.mainWindow
        while let sheet = window?.attachedSheet { window = sheet }
        guard let window else { return alert.runModal() == .alertSecondButtonReturn }
        return await withCheckedContinuation { done in
            alert.beginSheetModal(for: window) { done.resume(returning: $0 == .alertSecondButtonReturn) }
        }
    }
}
#endif
