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

    /// How a key is paid for, in plain words on its card (the page's `BILLING`): per use, by the provider, apart from
    /// any chat subscription, and where credit is added; for Gemini, the project a key belongs to is billed. Nil for a
    /// model on this Mac, which costs nothing, and a compatible service, which says its own.
    var billing: String? {
        switch self {
        case .anthropic: String(localized: """
            Billed per use by Anthropic, separately from any ChatGPT or Claude subscription. Add credit in \
            Anthropic’s console, under Billing.
            """)
        case .openai: String(localized: """
            Billed per use by OpenAI, separately from any ChatGPT or Claude subscription. Add credit on OpenAI’s \
            platform, under Billing.
            """)
        case .gemini: String(localized: """
            Billed per use by Google, separately from any ChatGPT or Claude subscription. Use a key on a billed \
            project: on the free tier, a key stops each day at its quota.
            """)
        case .local, .compatible: nil
        }
    }

    /// Where credit is added, for the providers that have one place for it.
    var creditPage: URL? {
        switch self {
        case .anthropic: URL(string: "https://console.anthropic.com/settings/billing")
        case .openai: URL(string: "https://platform.openai.com/settings/organization/billing")
        case .gemini, .local, .compatible: nil
        }
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

    /// What AI mode sends, to whom and when, said before it is turned on as after: under its switch, in the first
    /// run's AI step and in Help. A model on this Mac, or a compatible service answering here, sends nothing off it.
    static func sends(_ provider: API.AiProvider, url: String = "") -> String {
        guard let recipient = provider.recipient, !(provider == .compatible && onThisMac(url)) else {
            return provider == .local ? String(localized: "With a model on this Mac, nothing leaves this Mac.")
                : String(localized: "With the service on this Mac, nothing leaves this Mac.")
        }
        return String(localized: """
            With AI mode on, each new message’s sender, subject and opening text go to \(recipient) to be summarized, \
            automatically. Whole conversations go when you ask for a summary, a draft or the brief, with what you type \
            into Ask, a search written as a sentence, or Rewrite. Attachments never leave this Mac.
            """)
    }

    /// Which model background summaries ask, and which one what a person asks for goes to, as the digest has them in
    /// force (the page's `modelsUsed`): the provider's small model and the one chosen, or one for everything.
    static func modelsUsed(_ ai: API.AiStatus) -> String? {
        guard !ai.backgroundModel.isEmpty, !ai.model.isEmpty else { return nil }
        if ai.backgroundModel == ai.model {
            return String(localized: "Summaries made in the background and everything you ask for use \(ai.model).")
        }
        return String(localized: """
            Summaries made in the background use \(ai.backgroundModel); everything you ask for uses \(ai.model).
            """)
    }

    /// How many messages from the last 30 days wait for a summary, as the digest counts them (the page's
    /// `summariesWaiting`): nil where it doesn't say.
    static func summariesWaiting(_ waiting: Int?) -> String? {
        guard let waiting else { return nil }
        if waiting == 0 { return String(localized: "Every message from the last 30 days has its summary.") }
        let count = waiting.formatted(.number.locale(Locale(identifier: "en_US")))
        return waiting == 1 ? String(localized: "1 message from the last 30 days waits for a summary.")
            : String(localized: "\(count) messages from the last 30 days wait for a summary.")
    }

    /// What AI cost over some days, as the server counts it (`/api/ai/spending`, the page's `spentOver`): in all, then
    /// its three dearest features by name, the rest summed.
    static func spent(_ when: String, _ spent: API.PeriodSpent) -> String {
        guard spent.calls > 0 else { return String(localized: "\(when): nothing.") }
        var main = spent.features.prefix(3).map { "\($0.label) \(StatusWords.dollars($0.costMicros))" }
        let rest = spent.features.dropFirst(3)
        if !rest.isEmpty {
            main.append(String(localized: "the rest \(StatusWords.dollars(rest.reduce(0) { $0 + $1.costMicros }))"))
        }
        let calls = spent.calls.formatted(.number.locale(Locale(identifier: "en_US")))
        let total = StatusWords.dollars(spent.costMicros)
        let list = main.joined(separator: ", ")
        return spent.calls == 1 ? String(localized: "\(when): \(total), 1 call (\(list)).")
            : String(localized: "\(when): \(total), \(calls) calls (\(list)).")
    }

    /// Where the bill is: the sums are MailMind's own count, from each call it made, and the provider holds the bill.
    static func billOfRecord(_ provider: API.AiProvider) -> String {
        switch provider {
        case .anthropic: String(localized: "MailMind counts these from each call it makes; Anthropic’s console holds the bill.")
        case .openai: String(localized: "MailMind counts these from each call it makes; OpenAI’s platform holds the bill.")
        case .gemini: String(localized: """
            MailMind counts these from each call it makes; Google’s billing for your project holds the bill.
            """)
        case .local: String(localized: "MailMind counts these from each call it makes; a model on this Mac costs nothing.")
        case .compatible: String(localized: "MailMind counts these from each call it makes; the service holds the bill.")
        }
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
