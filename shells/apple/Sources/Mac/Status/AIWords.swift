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
        case .ollama: String(localized: "Ollama, on this Mac")
        case .compatible: String(localized: "Another OpenAI-compatible service")
        }
    }

    /// Its name in a sentence: "a key for OpenAI", "written by Gemini".
    var name: String {
        switch self {
        case .anthropic: String(localized: "Claude")
        case .openai: String(localized: "OpenAI")
        case .gemini: String(localized: "Gemini")
        case .ollama: String(localized: "Ollama")
        case .compatible: String(localized: "your AI service")
        }
    }

    /// Who the text of a message goes to while it is the provider: nil for Ollama, where it never leaves this Mac.
    var recipient: String? {
        switch self {
        case .anthropic: String(localized: "Anthropic")
        case .openai: String(localized: "OpenAI")
        case .gemini: String(localized: "Google")
        case .ollama: nil
        case .compatible: String(localized: "the service at the URL you gave")
        }
    }

    /// Whether it takes a key at all: Ollama refuses one.
    var takesKey: Bool { self != .ollama }

    /// Whether AI stays off without a key: an OpenAI-compatible service may need none.
    var needsKey: Bool { takesKey && self != .compatible }

    /// Where a person makes a key, for the providers that have one place for it.
    var keyPage: URL? {
        switch self {
        case .anthropic: URL(string: "https://console.anthropic.com")
        case .openai: URL(string: "https://platform.openai.com/api-keys")
        case .gemini: URL(string: "https://aistudio.google.com/apikey")
        case .ollama, .compatible: nil
        }
    }

    /// Where the key page is, as it is said: the host and path, no scheme.
    var keyPlace: String? {
        keyPage.map { ($0.host() ?? "") + ($0.path() == "/" ? "" : $0.path()) }
    }

    /// Whether Settings asks for the service's URL: Ollama's, prefilled, and a compatible service's, which has none.
    var asksForURL: Bool { self == .ollama || self == .compatible }

    /// Whether a model must be chosen: these have no default of their own.
    var needsModel: Bool { self == .ollama || self == .compatible }

    /// The URL Settings shows for Ollama when none is chosen: where the server reaches it by default ("" means this).
    static let ollamaURL = "http://127.0.0.1:11434/v1"

    /// What the URL field holds when none is chosen.
    var urlPrefill: String { self == .ollama ? Self.ollamaURL : "" }
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
    /// What is sent and to whom, as Settings and the first run say it under the AI's settings.
    static func privacy(_ provider: API.AiProvider) -> String {
        guard let recipient = provider.recipient else {
            return String(localized: """
                With Ollama, nothing leaves this Mac: the text of a message goes to Ollama, here, only when it is \
                summarized, briefed or drafted.
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
        let on = String(localized: "AI is on: \(ai.provider.name), \(ai.model).")
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
