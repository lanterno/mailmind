#if os(macOS)
import Foundation

/// The AI providers a person can choose (#49), as the app names them. The set itself, each one's defaults and what it
/// refuses are the server's: this is only how each is said, where its keys are made, and which of Settings' fields it
/// asks for.
extension API.AiProvider {
    /// What Settings' menu lists.
    var label: String {
        switch self {
        case .anthropic: String(localized: "Claude (Anthropic)")
        case .openai: String(localized: "OpenAI")
        case .gemini: String(localized: "Google Gemini")
        case .local: String(localized: "A model on this Mac")
        case .compatible: String(localized: "Another OpenAI-compatible service")
        }
    }

    /// Its name in a sentence: "a key for OpenAI", "written by Gemini", "asking your model server".
    var name: String {
        switch self {
        case .anthropic: String(localized: "Claude")
        case .openai: String(localized: "OpenAI")
        case .gemini: String(localized: "Gemini")
        case .local: String(localized: "your model server")
        case .compatible: String(localized: "your AI service")
        }
    }

    /// What writes, where the provider in force is said: "AI is on: Claude, …", "come from a model on this Mac".
    var writer: String { self == .local ? String(localized: "a model on this Mac") : name }

    /// The same at the head of a line: "Claude · …", "A model on this Mac · …".
    var title: String { writer.prefix(1).uppercased() + writer.dropFirst() }

    /// Who the text of a message goes to while it is the provider: nil for a model on this Mac, where it never leaves.
    var recipient: String? {
        switch self {
        case .anthropic: String(localized: "Anthropic")
        case .openai: String(localized: "OpenAI")
        case .gemini: String(localized: "Google")
        case .local: nil
        case .compatible: String(localized: "the service at the URL you gave")
        }
    }

    /// Whether AI stays off without a key: a model on this Mac and an OpenAI-compatible service may need none, and
    /// are asked with one only if one is kept.
    var needsKey: Bool { self != .local && self != .compatible }

    /// Where a person makes a key, for the providers that have one place for it.
    var keyPage: URL? {
        switch self {
        case .anthropic: URL(string: "https://console.anthropic.com")
        case .openai: URL(string: "https://platform.openai.com/api-keys")
        case .gemini: URL(string: "https://aistudio.google.com/apikey")
        case .local, .compatible: nil
        }
    }

    /// Where the key page is, as it is said: the host and path, no scheme.
    var keyPlace: String? {
        keyPage.map { ($0.host() ?? "") + ($0.path() == "/" ? "" : $0.path()) }
    }

    /// Whether Settings asks for the service's URL: a model server's on this Mac, prefilled, and a compatible
    /// service's, which has none.
    var asksForURL: Bool { self == .local || self == .compatible }

    /// Whether a model must be chosen: these have no default of their own.
    var needsModel: Bool { self == .local || self == .compatible }

    /// The URL Settings shows for a model on this Mac when none is chosen: Ollama's, where the server reaches one by
    /// default ("" means this).
    static let localURL = "http://127.0.0.1:11434/v1"

    /// What the URL field holds when none is chosen.
    var urlPrefill: String { self == .local ? Self.localURL : "" }
}

extension API.AiStatus {
    /// AI is on: a call can go.
    var isOn: Bool { off == nil && engine != "none" }

    /// A person turned AI off in Settings: nothing only AI can serve is shown, and nothing asks them to turn it on.
    var isTurnedOff: Bool { off == .turnedOff }
}

/// What the app says of AI, from what the digest says of it: in Settings, in the brief and wherever an AI card would
/// be. Plain functions, so the tests hold the words.
enum AIWords {
    /// Whether a service's URL is on this Mac: http or https at 127.0.0.1, ::1 or localhost, as the core holds a
    /// model on this Mac to.
    static func onThisMac(_ url: String) -> Bool {
        guard let parts = URLComponents(string: url.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = parts.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = parts.host?.lowercased() else { return false }
        return ["127.0.0.1", "::1", "[::1]", "localhost"].contains(host)
    }

    /// What is sent and to whom, as Settings and the first run say it under the AI's settings. `url` is the service's
    /// URL as chosen: a compatible service answering on this Mac keeps the text here too.
    static func privacy(_ provider: API.AiProvider, url: String = "") -> String {
        guard let recipient = provider.recipient, !(provider == .compatible && onThisMac(url)) else {
            return String(localized: """
                The model runs on this Mac, so the text of a message never leaves it: it goes to \(provider.name), \
                here, only when it is summarized, briefed or drafted.
                """)
        }
        return String(localized: """
            With AI on, the text of a message goes to \(recipient) only when it is summarized, briefed or drafted; \
            nothing else leaves this Mac.
            """)
    }

    /// Why AI is off, in a few words: nil while it is on.
    static func why(_ ai: API.AiStatus) -> String? {
        switch ai.off {
        case .turnedOff?: String(localized: "AI mode is off in Settings")
        case .noKey?: String(localized: "there is no \(ai.provider.name) API key")
        case .noUrl?: String(localized: "the service has no URL")
        case .noModel?: String(localized: "no model is chosen")
        case nil: ai.engine == "none" ? String(localized: "there is no API key") : nil
        }
    }

    /// How AI stands, as Settings' AI pane says it at its foot: on with which model, off and why, and the last call
    /// that failed.
    static func standing(_ ai: API.AiStatus) -> (text: String, failing: Bool) {
        guard ai.isOn else {
            if ai.isTurnedOff {
                return (String(localized: "AI mode is off. MailMind sends nothing to any AI service."), false)
            }
            return (String(localized: "AI is off: \(why(ai) ?? "")."), false)
        }
        let on = String(localized: "AI is on: \(ai.provider.writer), \(ai.model).")
        if ai.lastOk == false, let error = ai.lastError, !error.isEmpty {
            return (on + " " + String(localized: "The last call failed: \(error)"), true)
        }
        return (on, false)
    }

    /// AI off for want of something a person can give, as the brief and the cards say it where AI would be: why, and
    /// what turns it on in Settings. None while AI is on, or once a person turned it off: then nothing asks.
    static func offNote(_ ai: API.AiStatus?) -> (why: String, fix: String)? {
        guard let ai, !ai.isOn, !ai.isTurnedOff else { return nil }
        let fix = switch ai.off {
        case .noUrl?: String(localized: "To turn it on, give the service’s URL in Settings › AI.")
        case .noModel?: String(localized: "To turn it on, choose a model in Settings › AI.")
        default: String(localized: "To turn it on, keep a \(ai.provider.name) API key in Settings › AI.")
        }
        return (String(localized: "AI is off: \(why(ai) ?? "")."), fix)
    }
}
#endif
