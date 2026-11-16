#if os(macOS)
import Foundation

/// An account as its form holds it while a person adds or edits one (settings/Settings.tsx's `ConnectionForm`): what
/// the server is sent, the form's checks before it is, and its words when one fails. A new account starts at its
/// address, and the server says which preset it takes (`/api/accounts/preset`): a provider's own address takes its
/// settings, one whose mail a provider's servers take is offered them, and any other is Other IMAP, with its incoming
/// and outgoing servers typed. The server fills in what a type gives (Gmail's host and port, the Bridge's on this
/// computer) and refuses what it won't keep.
///
/// A new account's password goes to the login Keychain, and nowhere else; an account that already reads it from
/// 1Password or the environment keeps that, shown and not changed here.
///
/// A password typed here is held only until it is sent, and never described: printing, dumping or logging a draft
/// shows that one was typed and nothing of it.
struct AccountDraft: Equatable {
    /// The types an account is saved as: Gmail, Proton through its Bridge, and any other IMAP server.
    enum Kind: String, CaseIterable, Identifiable, Sendable {
        case gmail, imap, proton
        var id: String { rawValue }
        var label: String {
            switch self {
            case .gmail: "Gmail"
            case .imap: String(localized: "Other IMAP")
            case .proton: "Proton"
            }
        }
    }

    /// Where a saved account's password comes from, but Proton's, which is the Bridge's. Only the Keychain is offered;
    /// the others are an existing account's own.
    enum Source: String, Sendable {
        case keychain, op, env
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
    /// The type, which a new account has none of until the server has said which its address takes.
    var kind: Kind?
    var host = ""
    var port = "993"
    var security = Security.tls
    var smtpHost = ""
    var smtpPort = "587"
    var smtpSecurity = Security.starttls
    /// An edited Other IMAP account's outgoing port and security as the form first showed them, where the file holds
    /// none and they are the type's: sent only once changed, so the account goes on taking them from its type.
    private var outgoingUnsaved = OutgoingUnsaved()
    /// The Trash a preset names (iCloud's "Deleted Messages"); nil leaves it to the server to find.
    var trash: String?
    var source = Source.keychain
    var password = ""
    var opRef = ""
    var envVar = ""
    /// What the server said of the address typed (`/api/accounts/preset`): nil until it has, or once the address
    /// changes.
    var found: API.PresetFound?
    /// The preset whose settings are in the form, taken or offered and used.
    var applied: API.Preset?

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
        // No outgoing host saved is the incoming one, which the field's prompt shows: an edit that leaves it blank
        // keeps it following the incoming host rather than writing that host into the file.
        let given = row.defaultsApplied
        smtpHost = given.smtpHost ? "" : row.smtpHost ?? ""
        smtpPort = String(row.smtpPort)
        smtpSecurity = row.smtpStarttls ? .starttls : .tls
        if kind == .imap {
            outgoingUnsaved = OutgoingUnsaved(port: given.smtpPort ? smtpPort : nil,
                                              security: given.smtpStarttls ? smtpSecurity : nil)
        }
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

    /// The type the account is sent as: Other IMAP until the server has said otherwise.
    var type: Kind { kind ?? .imap }

    /// The address the form is for: the one edited, or the one typed, trimmed.
    var address: String { editing ?? email.trimmed }

    /// The address the server is asked which preset it takes: one typed with an @ in it, only while adding, since a
    /// saved account's type is its own.
    var presetQuery: String? {
        guard editing == nil else { return nil }
        let typed = email.trimmed
        return typed.contains("@") ? typed : nil
    }

    /// The server's answer for the address typed: a provider's own address takes its settings, and any other is Other
    /// IMAP, with what a preset filled in before taken out again (what was typed stays).
    mutating func take(_ answer: API.PresetFound) {
        found = answer
        if answer.sure, let settings = answer.settings, let preset = answer.preset {
            use(settings, preset)
            return
        }
        if applied != nil {
            host = ""; port = "993"; security = .tls
            smtpHost = ""; smtpPort = "587"; smtpSecurity = .starttls
            kind = .imap
        }
        if kind == nil { kind = .imap }
        applied = nil
        trash = nil
    }

    /// Put the preset the server found in the form, as its offer's button does.
    mutating func useFound() {
        guard let found, let settings = found.settings, let preset = found.preset else { return }
        use(settings, preset)
    }

    private mutating func use(_ settings: API.PresetSettings, _ preset: API.Preset) {
        kind = Kind(rawValue: settings.type) ?? .imap
        if kind == .imap {
            host = settings.host ?? ""
            port = String(settings.port ?? 993)
            security = settings.starttls == true ? .starttls : .tls
            smtpHost = settings.smtpHost ?? ""
            smtpPort = String(settings.smtpPort ?? 587)
            smtpSecurity = settings.smtpStarttls == false ? .tls : .starttls
        }
        trash = settings.trash
        applied = preset
    }

    /// A type picked by hand: what a preset filled in is no longer its.
    mutating func pick(_ picked: Kind) {
        kind = picked
        applied = nil
        trash = nil
    }

    /// Whether the address is at a provider MailMind can't log in to yet (Microsoft's): nothing is asked of it.
    var unsupported: Bool { editing == nil && found?.supported == false }

    /// What the form says of the address's provider before the password: its own words (an app password, the Bridge,
    /// a sign-in MailMind can't do yet).
    var note: String? { editing == nil ? found?.note : nil }

    /// The page where the provider's password is made, shown where it is typed, once its settings are in the form.
    var link: API.Link? {
        guard editing == nil, let found, found.preset != nil, found.preset == applied else { return nil }
        return found.link
    }

    /// What the form says of the preset the server found (the page's `PresetOffer`).
    enum PresetWords: Equatable {
        /// A provider's settings, offered where they aren't in the form: its words and the button's.
        case offer(String, button: String)
        /// The provider's settings in the form, said to fit (a domain whose mail its servers take) or filled in (its
        /// own address).
        case fits(String)
    }

    var presetWords: PresetWords? {
        guard editing == nil, let found, let name = found.name, found.settings != nil, !unsupported else { return nil }
        let using = found.preset != nil && applied == found.preset
        if found.sure {
            return using ? .fits(String(localized: "\(name) address: \(name)’s settings are filled in.")) : nil
        }
        let whose = String(localized: "\(name) takes \(found.domain ?? "")’s mail")
        if using { return .fits(String(localized: "\(whose): \(name)’s settings fit it.")) }
        return .offer(String(localized: "\(whose): use \(name)’s settings?"), button: String(localized: "Use \(name)"))
    }

    /// What the password field shows before one is typed: an App Password's shape for Gmail.
    var passwordPrompt: String {
        if editing != nil { return String(localized: "unchanged — leave blank to keep") }
        return type == .gmail ? "abcd efgh ijkl mnop" : String(localized: "paste the password or app password")
    }

    /// Whether a password can be typed: always for a new account, and for one whose password is in the Keychain.
    var typesPassword: Bool { type != .proton && source == .keychain }

    /// Where an existing account reads its password from, when it isn't the Keychain: shown, not changed here.
    var keptElsewhere: String? {
        guard type != .proton else { return nil }
        switch source {
        case .keychain: return nil
        case .op: return String(localized: "From 1Password: \(opRef)")
        case .env: return String(localized: "From the environment variable \(envVar)")
        }
    }

    /// The credential to send: the Bridge's for Proton; else the password typed, kept in the Keychain; nil when none
    /// is typed (an edit then keeps the one it has).
    var credential: JSONValue? {
        if type == .proton { return .object(["style": .string("proton_bridge")]) }
        guard source == .keychain else { return nil }
        let typed = password.trimmed
        return typed.isEmpty ? nil : .object(["style": .string("keychain"), "value": .string(typed)])
    }

    /// What the form checks before it sends: nil when it may be saved.
    var problem: String? {
        if editing == nil, !address.contains("@") { return String(localized: "Type the account’s email address.") }
        if unsupported { return note ?? String(localized: "MailMind can’t log in to this account yet.") }
        if type == .imap, host.trimmed.isEmpty { return String(localized: "Type the incoming server’s name.") }
        if editing == nil, credential == nil { return String(localized: "Type the account’s password.") }
        return nil
    }

    private var port993: Int { Int(port.trimmed) ?? 993 }
    private var port587: Int { Int(smtpPort.trimmed) ?? 587 }

    /// An Other IMAP account's servers, as `AccountBody` and `AccountDraft` name them: nil for every other type, whose
    /// servers its type gives. An outgoing server left blank is the server's to fill in from the incoming one, and an
    /// edited account's outgoing port and security the file doesn't hold are sent only once changed.
    private var servers: (host: JSONValue?, port: JSONValue?, starttls: Bool?, smtpHost: JSONValue?, smtpPort: JSONValue?,
                          smtpStarttls: Bool?)
    {
        guard type == .imap else { return (nil, nil, nil, nil, nil, nil) }
        return (.string(host.trimmed), .number(Double(port993)), security == .starttls,
                smtpHost.trimmed.isEmpty ? nil : .string(smtpHost.trimmed),
                smtpPort == outgoingUnsaved.port ? nil : .number(Double(port587)),
                smtpSecurity == outgoingUnsaved.security ? nil : smtpSecurity == .starttls)
    }

    /// The body `/api/accounts/add` or `/api/accounts/edit` takes: an Other IMAP account's servers with it, a preset's
    /// Trash when one names it, and the credential only when one is given.
    var body: API.AccountBody {
        let s = servers
        return API.AccountBody(alias: alias.trimmed, credential: credential, email: address, host: s.host, port: s.port,
                               signature: signature.trimmed, smtpHost: s.smtpHost, smtpPort: s.smtpPort,
                               smtpStarttls: s.smtpStarttls, starttls: s.starttls,
                               trash: editing == nil ? trash : nil, type: .string(type.rawValue))
    }

    /// What `/api/accounts/test` is asked: the form as it stands when it gives a credential; the account as it is
    /// saved when editing without one; nil, with nothing to test, when adding without one. A Proton account logs in with
    /// the Bridge's password, which only a saved account is tested with: the one edited, and a new one not until it is
    /// added (`testsOnceAdded`).
    var test: API.TryBody? {
        if type == .proton { return editing.map { API.TryBody(email: $0) } }
        if let credential {
            let s = servers
            return API.TryBody(draft: API.AccountDraft(credential: credential, email: address, host: s.host, port: s.port,
                                                       smtpHost: s.smtpHost, smtpPort: s.smtpPort,
                                                       smtpStarttls: s.smtpStarttls, starttls: s.starttls,
                                                       type: .string(type.rawValue)))
        }
        return editing.map { API.TryBody(email: $0) }
    }

    /// Why Test can't run yet, said where it would: a new Proton account is tested once it is added.
    var testsOnceAdded: String? {
        editing == nil && type == .proton ? String(localized: "Add the account, then test it.") : nil
    }

    /// Let go of what was typed.
    mutating func forgetPassword() {
        password = ""
    }
}

/// An edited account's outgoing port and security where its file holds none: as the form first showed them.
struct OutgoingUnsaved: Equatable {
    var port: String?
    var security: AccountDraft.Security?
}

extension AccountDraft: CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
    var description: String {
        "AccountDraft(\(address), \(type.rawValue), password: \(password.isEmpty ? "none" : "typed"))"
    }

    var debugDescription: String { description }

    /// What `dump` and a debugger show: every field but the password, which shows only whether one was typed.
    var customMirror: Mirror {
        Mirror(self, children: [
            "editing": editing as Any, "alias": alias, "signature": signature, "email": email, "kind": kind as Any,
            "host": host, "port": port, "security": security, "smtpHost": smtpHost, "smtpPort": smtpPort,
            "smtpSecurity": smtpSecurity, "trash": trash as Any, "source": source,
            "password": password.isEmpty ? "none" : "typed", "opRef": opRef, "envVar": envVar,
        ], displayStyle: .struct)
    }
}

/// What a test of an account came to (`testConnection`): connected, or what went wrong in plain words with what to do
/// next, the page that helps, and the class of the trouble in small type, for the owner.
enum Tested: Equatable, Sendable {
    case running
    case connected
    case failed(String, link: API.Link? = nil, detail: String? = nil)

    var text: String {
        switch self {
        case .running: String(localized: "Testing…")
        case .connected: String(localized: "Connected")
        case .failed(let why, _, _): why
        }
    }

    static func of(_ answer: API.AccountsTestAnswer) -> Tested {
        switch answer {
        case .loggedIn(let answer):
            return answer.connect ? .connected : .failed(String(localized: "MailMind couldn’t log in."))
        case .couldntLogIn(let out):
            let says = out.says.isEmpty ? String(localized: "MailMind couldn’t log in.") : out.says
            return .failed(says, link: out.link, detail: out.error.isEmpty || out.error == says ? nil : out.error)
        }
    }

    static func of(_ error: any Error) -> Tested {
        if let problem = error as? Problem, problem.isRefusal {
            return .failed(problem.detail ?? String(localized: "MailMind couldn’t log in."))
        }
        return .failed(String(localized: "The test didn’t finish. Try again."))
    }
}

fileprivate extension String {
    var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
}
#endif
