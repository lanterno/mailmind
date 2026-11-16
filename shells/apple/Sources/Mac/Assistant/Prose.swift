#if os(macOS)
import Foundation
import Observation

/// Whether what's typed in the search field reads as a sentence worth asking the AI to turn into a search, as the
/// page's `readsAsProse` decides it. It decides a call that costs money, on Return only, so it leans hard towards no:
/// a miss is only the literal search. Three or more words that don't speak the search grammar, opening like a request
/// or a question, or holding a word that relates one term to another, or a time said in words. English only, as the
/// model's prompt is. Names and titles ("bank of america", "meeting with john") hold none of these words.
enum Prose {
    static let fewest = 3
    /// More than this was pasted, not typed as a search.
    static let most = 30

    private static let asks: Set<String> = [
        "where", "what", "when", "who", "whom", "which", "why", "how", "did", "does", "has", "is", "are", "was", "were",
    ]
    /// Only with one of `requested` after: "list of attendees" isn't a request.
    private static let requests: Set<String> = ["find", "show", "get", "search", "look", "list"]
    private static let requested: Set<String> = ["me", "my", "the", "all", "any", "every", "emails", "mail", "messages", "for"]
    private static let relates: Set<String> = [
        "from", "about", "since", "before", "after", "regarding", "between", "mentioning", "containing", "sent",
        "received", "wrote", "attached",
    ]
    private static let timeAlone: Set<String> = ["yesterday", "today", "tomorrow", "ago", "recently", "lately"]
    private static let near: Set<String> = ["last", "this", "next", "past"]
    private static let span: Set<String> = [
        "week", "month", "year", "night", "weekend", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
        "sunday",
    ]

    static func readsAsProse(_ typed: String) -> Bool {
        let text = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        // A phrase in quotes means exactly that.
        if text.contains("\"") { return false }
        let raw = text.split(whereSeparator: \.isWhitespace).map(String.init)
        guard (fewest...most).contains(raw.count), !raw.contains(where: speaksTheGrammar) else { return false }
        let words = raw.map(bare)
        if asks.contains(words[0]) || (requests.contains(words[0]) && requested.contains(words[1])) { return true }
        return words.indices.contains { at in
            (relates.contains(words[at]) && at > 0 && at < words.count - 1)
                || timeAlone.contains(words[at])
                || (near.contains(words[at]) && at + 1 < words.count && span.contains(words[at + 1]))
        }
    }

    /// A negation ("-word") or an operator ("from:ada"): the grammar, meant.
    private static func speaksTheGrammar(_ word: String) -> Bool {
        if word.count > 1, word.hasPrefix("-") { return true }
        guard let colon = word.firstIndex(of: ":"), colon > word.startIndex else { return false }
        let name = word[..<colon]
        return name.allSatisfy { $0.isLetter || $0.isNumber || $0 == "_" } && word.index(after: colon) < word.endIndex
    }

    /// A word lowered, without the marks around it.
    private static func bare(_ word: String) -> String {
        let kept: (Character) -> Bool = { $0.isLetter || $0.isNumber }
        let lowered = word.lowercased()
        guard let first = lowered.firstIndex(where: kept), let last = lowered.lastIndex(where: kept) else { return "" }
        return String(lowered[first...last])
    }
}

/// A sentence typed in the search field, turned into the search it means by the planner a bulk action uses
/// (`/api/ai/do-plan`), as the page's `translateProse` does. Only on Return, with AI on, and only with the literal
/// search already on screen: a call that fails, is off or is over the day's limit leaves that standing and asks
/// nobody anything. A plan's verb goes unread, so a sentence that reads like a command is only a search here.
@MainActor
@Observable
final class ProseSearch {
    /// The words being translated, for the field's "Translating…".
    private(set) var translating: String?
    @ObservationIgnored private var seq = 0
    /// Return again on the same words costs nothing.
    @ObservationIgnored private var last: (prose: String, filter: String)?
    /// Words whose translation someone took back: searched as typed from then on.
    @ObservationIgnored private var keptLiteral: Set<String> = []
    @ObservationIgnored let link: CoreLink

    init(link: CoreLink) {
        self.link = link
    }

    /// The search `typed` means, or nil to leave the literal search standing. The caller shows the result with
    /// "Translated from" `typed` beside it.
    func translate(_ typed: String, aiOn: Bool) async -> String? {
        let prose = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard aiOn, Prose.readsAsProse(prose), let routes = link.routes, !keptLiteral.contains(prose),
              translating != prose else { return nil }
        if let last, last.prose == prose { return last.filter }
        seq += 1
        let mine = seq
        translating = prose
        var filter = ""
        switch try? await routes.post(API.Post.AiDoPlan.self, API.PlanBody(cmd: prose)) {
        case .planPreview(let plan)?: filter = plan.filter
        case .planIsASearch(let search)?: filter = search.filter
        case nil: break
        }
        guard mine == seq else { return nil }
        translating = nil
        filter = filter.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !filter.isEmpty, filter.lowercased() != prose.lowercased() else { return nil }
        last = (prose, filter)
        return filter
    }

    /// The search moved on: a translation that comes now is dropped.
    func stop() {
        seq += 1
        translating = nil
    }

    /// Search for `prose` as typed instead, from now on (the chip's ×).
    func keepLiteral(_ prose: String) {
        keptLiteral.insert(prose)
        if last?.prose == prose { last = nil }
    }
}
#endif
