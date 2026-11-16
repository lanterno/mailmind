#if os(macOS)
import Foundation

/// How a secret a person types reaches the core: straight to its route, in a body encoded here, whose bytes are written
/// over once the core has answered. The core keeps it in the login Keychain (in a test or a demo, the file standing in
/// for it) and never answers with it, its length or any part of it.
///
/// Swift gives no way to wipe a `String`, so a field's text is let go of as soon as it is sent: the field is emptied,
/// and nothing here keeps, logs or describes it.
enum SecretPost {
    /// POST `body` to `route` and read its answer, as `Core.post` does, with the encoded body overwritten after.
    static func post<R: PostRoute>(_ core: Core, _ route: R.Type, _ body: R.Body) async throws -> R.Answer
        where R.Answer: Decodable
    {
        var sent = try JSONEncoder().encode(body)
        let answer = await core.send("POST", R.path, sent)
        sent.resetBytes(in: 0..<sent.count)
        return try Core.decoded(R.Answer.self, from: answer)
    }
}

/// Which of the variables this app hands the core gives a secret, so Settings can say when one wins over what the
/// Keychain keeps. Only whether each is set is read here, never what it says.
struct SecretSources: Equatable, Sendable {
    /// `ANTHROPIC_API_KEY`: Claude's key when the config file names none. No other provider has a variable.
    var apiKeyVariable = false
    /// `MAILMIND_PROTON_BRIDGE_PASS`: the Bridge's password, ahead of everything else.
    var bridgeVariable = false
    /// `MAILMIND_PROTON_OP_ITEM`: the 1Password item that holds it, ahead of the Keychain.
    var bridgeOpItem = false

    static let apiKey = "ANTHROPIC_API_KEY"
    static let bridgePassword = "MAILMIND_PROTON_BRIDGE_PASS"
    static let bridgeItem = "MAILMIND_PROTON_OP_ITEM"

    init(apiKeyVariable: Bool = false, bridgeVariable: Bool = false, bridgeOpItem: Bool = false) {
        self.apiKeyVariable = apiKeyVariable
        self.bridgeVariable = bridgeVariable
        self.bridgeOpItem = bridgeOpItem
    }

    /// What this process passes on to the core (`CoreHome.passedOn`).
    init(environment: [String: String]) {
        func given(_ name: String) -> Bool {
            CoreHome.passedOn.contains(name) && !(environment[name] ?? "").isEmpty
        }
        apiKeyVariable = given(Self.apiKey)
        bridgeVariable = given(Self.bridgePassword)
        bridgeOpItem = given(Self.bridgeItem)
    }

    static let ofThisProcess = SecretSources(environment: ProcessInfo.processInfo.environment)
}

/// What Settings says of a kept secret: whether one is set, where the one in use comes from, and whether a key kept
/// here is the one in use. Never the secret.
struct SecretStanding: Equatable, Sendable {
    /// The words beside the lock.
    let summary: String
    /// What wins over the Keychain, or why the one kept isn't used: nil when nothing does.
    let note: String?
    /// Whether the Keychain keeps one, so it can be taken out.
    let kept: Bool

    /// A provider's API key: `kept` is whether `/api/ai/key` lists it (the config file names a key for it), `ai` what
    /// the digest says of AI, which speaks of the provider in force. A key MailMind keeps wins over the environment's,
    /// and only Claude's key has a variable to stand in for one not kept.
    static func apiKey(_ provider: API.AiProvider, kept: Bool, ai: API.AiStatus?, sources: SecretSources)
        -> SecretStanding
    {
        let inForce = ai?.provider == provider ? ai : nil
        if kept {
            if inForce?.off == .noKey {
                return SecretStanding(summary: String(localized: "A key is kept, but it can’t be read"),
                                      note: String(localized: "AI is off. Keep the key again, or remove it."),
                                      kept: true)
            }
            let shadowed = provider == .anthropic && sources.apiKeyVariable
                ? String(localized: "\(SecretSources.apiKey) is set too, and the key kept here wins over it.")
                : nil
            return SecretStanding(summary: String(localized: "Kept in your login Keychain"), note: shadowed, kept: true)
        }
        if provider == .anthropic {
            // While Claude's AI is on, the digest says where its key came from; otherwise whether the variable is set.
            let fromVariable = inForce.flatMap { $0.isOn ? $0.keyFrom == SecretSources.apiKey : nil }
                ?? sources.apiKeyVariable
            if fromVariable {
                return SecretStanding(
                    summary: String(localized: "From the environment variable \(SecretSources.apiKey)"),
                    note: String(localized: "A key kept here would win over it."), kept: false)
            }
        }
        if !provider.needsKey {
            return SecretStanding(summary: String(localized: "No key"),
                                  note: provider == .local
                                      ? String(localized: "Only if your server asks for one.")
                                      : String(localized: "The service is asked with none. Keep one if it asks for one."),
                                  kept: false)
        }
        return SecretStanding(summary: String(localized: "No key: AI is off"),
                              note: String(localized: "Without a key nothing is sent to \(provider.recipient ?? provider.name)."),
                              kept: false)
    }

    /// The Proton Bridge's password: `set` is what `/api/accounts/bridge-password` says (the Keychain keeps one). The
    /// environment's password wins over everything, then 1Password's item, then the Keychain's.
    static func bridgePassword(set: Bool, sources: SecretSources) -> SecretStanding {
        let kept = set ? String(localized: "Kept in your login Keychain") : String(localized: "Not set")
        if sources.bridgeVariable {
            return SecretStanding(
                summary: String(localized: "From the environment variable \(SecretSources.bridgePassword)"),
                note: String(localized: "It wins over the Keychain.") + (set ? " " + String(localized: "The Keychain keeps one too.") : ""),
                kept: set)
        }
        if sources.bridgeOpItem {
            return SecretStanding(
                summary: String(localized: "From 1Password, the item \(SecretSources.bridgeItem) names"),
                note: String(localized: "It comes ahead of the Keychain, which is read only when 1Password gives nothing.")
                    + (set ? " " + String(localized: "The Keychain keeps one too.") : ""),
                kept: set)
        }
        return SecretStanding(summary: kept, note: nil, kept: set)
    }
}
#endif
