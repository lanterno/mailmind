#if os(macOS)
import Foundation

/// An account as its form holds it while a person adds or edits one (settings/Settings.tsx's `ConnectionForm`): what
/// the server is sent, the page's checks before it is, and its words when one fails. The server fills in what a type's
/// preset says (Gmail's host and port, the Bridge's on this computer) and refuses what it won't keep, and says which
/// preset a new address takes (`/api/accounts/preset`), which the form only offers.
///
/// A password typed here is held only until it is sent, and never described: printing, dumping or logging a draft
/// shows that one was typed and nothing of it.
struct AccountDraft: Equatable {
    /// The page's presets: Gmail, any IMAP server, and Proton through its Bridge.
    enum Kind: String, CaseIterable, Identifiable, Sendable {
        case gmail, imap, proton
        var id: String { rawValue }
        var label: String {
            switch self {
            case .gmail: "Gmail"
            case .imap: "IMAP"
            case .proton: "Proton"
            }
        }
    }

    /// Where an account's password comes from, but Proton's, which is the Bridge's.
    enum Source: String, CaseIterable, Identifiable, Sendable {
        case keychain, op, env
        var id: String { rawValue }
        var label: String {
            switch self {
            case .keychain: String(localized: "Password")
            case .op: "1Password"
            case .env: String(localized: "Env var")
            }
        }
    }

    enum Security: String, CaseIterable, Identifiable, Sendable {
        case tls, starttls
        var id: String { rawValue }
        var label: String { self == .tls ? "TLS" : "STARTTLS" }
    }

    /// The account edited, by its address, which is fixed; nil when adding one.
    let editing: String?
    var alias = ""
    var signature = ""
    var email = ""
    var kind = Kind.gmail
    var host = ""
    var port = "993"
    var security = Security.tls
    var source = Source.keychain
    var password = ""
    var opRef = ""
    var envVar = ""
    /// What the server said of the address typed (`/api/accounts/preset`): nil until it has, or once the address
    /// changes.
    var found: API.PresetFound?

    /// Where a Google Account makes an App Password, which Gmail's IMAP takes in place of the account's own password.
    static let appPasswords = URL(string: "https://myaccount.google.com/apppasswords")!

    /// How to make one, in the page's words (`AppPasswordSteps`).
    static let appPasswordSteps = [
        String(localized: "Turn on 2-Step Verification for your Google Account."),
        String(localized: "Create an App Password, named MailMind."),
        String(localized: "Paste it above as Google shows it, spaces and all."),
    ]

    init(editing row: API.AccountRow? = nil) {
        editing = row?.email
        guard let row else { return }
        alias = row.alias ?? ""
        signature = row.signature
        email = row.email ?? ""
        kind = Kind(rawValue: row.type) ?? .imap
        host = row.host ?? ""
        port = String(row.port)
        security = row.starttls ? .starttls : .tls
        switch row.credential.style {
        case "op":
            source = .op
            switch row.credential.ref {
            case .string(let ref)?: opRef = ref
            case .strings(let refs)?: opRef = refs.first ?? ""
            case nil: break
            }
        case "env":
            source = .env
            envVar = row.credential.var ?? ""
        default:
            source = .keychain
        }
    }

    /// The address the form is for: the one edited, or the one typed, trimmed.
    var address: String { editing ?? email.trimmed }

    /// The address the server is asked which preset it takes: one typed with an @ in it, only while adding, since a
    /// saved account's type is its own.
    var presetQuery: String? {
        guard editing == nil else { return nil }
        let typed = email.trimmed
        return typed.contains("@") ? typed : nil
    }

    /// What the form says of the preset the server found (the page's `PresetOffer`).
    enum PresetWords: Equatable {
        /// Gmail's settings, offered where another type is picked.
        case offer(String)
        /// Gmail's settings picked, said to fit a domain Google's servers take.
        case fits(String)
    }

    /// Gmail's settings offered when the server found them and they aren't picked; with them picked, said to fit a
    /// domain Google's servers take. Nothing for any other address, or an address at Google's own domains with
    /// Gmail's settings picked already.
    var presetWords: PresetWords? {
        guard editing == nil, let found, found.preset == .gmail else { return nil }
        let whose = found.sure ? String(localized: "A Google address")
                               : String(localized: "Google takes \(found.domain ?? "")’s mail")
        if kind == .gmail { return found.sure ? nil : .fits(String(localized: "\(whose): Gmail’s settings fit it.")) }
        return .offer(String(localized: "\(whose): use Gmail’s settings?"))
    }

    /// What the password field shows before one is typed: an App Password's shape for Gmail.
    var passwordPrompt: String {
        if editing != nil { return String(localized: "unchanged — leave blank to keep") }
        return kind == .gmail ? "abcd efgh ijkl mnop" : String(localized: "paste the password / app password")
    }

    /// The credential to send: the Bridge's for Proton; else the typed password, 1Password reference or variable's
    /// name, or nil when none is given (an edit then keeps the one it has).
    var credential: JSONValue? {
        if kind == .proton { return .object(["style": .string("proton_bridge")]) }
        switch source {
        case .keychain:
            let typed = password.trimmed
            return typed.isEmpty ? nil : .object(["style": .string("keychain"), "value": .string(typed)])
        case .op:
            return opRef.trimmed.isEmpty ? nil : .object(["style": .string("op"), "ref": .string(opRef.trimmed)])
        case .env:
            return envVar.trimmed.isEmpty ? nil : .object(["style": .string("env"), "var": .string(envVar.trimmed)])
        }
    }

    /// What the page checks before it sends, in its words: nil when the form may be saved.
    var problem: String? {
        if editing == nil, !address.contains("@") { return String(localized: "A valid email is required.") }
        if kind == .imap, host.trimmed.isEmpty { return String(localized: "A host is required for a generic IMAP account.") }
        if editing == nil, credential == nil { return String(localized: "Set a password (or 1Password ref / env var).") }
        return nil
    }

    private var port993: Int { Int(port.trimmed) ?? 993 }

    /// The body `/api/accounts/add` or `/api/accounts/edit` takes: an IMAP account's server with it, and the
    /// credential only when one is given.
    var body: API.AccountBody {
        API.AccountBody(alias: alias.trimmed, credential: credential, email: address,
                        host: kind == .imap ? .string(host.trimmed) : nil,
                        port: kind == .imap ? .number(Double(port993)) : nil,
                        signature: signature.trimmed, starttls: kind == .imap ? security == .starttls : nil,
                        type: .string(kind.rawValue))
    }

    /// What `/api/accounts/test` is asked: the form as it stands when it gives a credential; the account as it is
    /// saved when editing without one; nil, with nothing to test, when adding without one.
    var test: API.TryBody? {
        if let credential {
            return API.TryBody(draft: API.AccountDraft(credential: credential, email: address,
                                                       host: kind == .imap ? .string(host.trimmed) : nil,
                                                       port: kind == .imap ? .number(Double(port993)) : nil,
                                                       starttls: kind == .imap ? security == .starttls : nil,
                                                       type: .string(kind.rawValue)))
        }
        return editing.map { API.TryBody(email: $0) }
    }

    /// Let go of what was typed.
    mutating func forgetPassword() {
        password = ""
    }
}

extension AccountDraft: CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
    var description: String {
        "AccountDraft(\(address), \(kind.rawValue), password: \(password.isEmpty ? "none" : "typed"))"
    }

    var debugDescription: String { description }

    /// What `dump` and a debugger show: every field but the password, which shows only whether one was typed.
    var customMirror: Mirror {
        Mirror(self, children: [
            "editing": editing as Any, "alias": alias, "signature": signature, "email": email, "kind": kind,
            "host": host, "port": port, "security": security, "source": source,
            "password": password.isEmpty ? "none" : "typed", "opRef": opRef, "envVar": envVar,
        ], displayStyle: .struct)
    }
}

/// What a test of an account came to, in the page's words (`testConnection`).
enum Tested: Equatable, Sendable {
    case running
    case connected
    case failed(String)

    var text: String {
        switch self {
        case .running: String(localized: "Testing…")
        case .connected: String(localized: "Connected")
        case .failed(let why): why
        }
    }

    static func of(_ answer: API.AccountsTestAnswer) -> Tested {
        switch answer {
        case .loggedIn(let answer): answer.connect ? .connected : .failed(String(localized: "failed"))
        case .couldntLogIn(let out): .failed(out.error.isEmpty ? String(localized: "failed") : out.error)
        }
    }

    static func of(_ error: any Error) -> Tested {
        if let problem = error as? Problem, problem.isRefusal { return .failed(problem.detail ?? String(localized: "failed")) }
        return .failed(String(localized: "test failed"))
    }
}

fileprivate extension String {
    var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
}
#endif
