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
        case gmail, imap, proton, hodhod, gentlemail
        var id: String { rawValue }
        var label: String {
            switch self {
            case .gmail: "Gmail"
            case .imap: String(localized: "Other IMAP")
            case .proton: "Proton"
            case .hodhod: "HodHod"
            case .gentlemail: "GentleMail"
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
    /// What the Edit sheet was opened to fix from the account's Sync card (#51): its password, which is then asked
    /// for, or its server. Nil for a plain edit.
    let fixing: AccountFix?
    /// The type the edited account is saved as; nil when adding one.
    private let savedKind: Kind?
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

    init(editing row: API.AccountRow? = nil, fixing: AccountFix? = nil) {
        editing = row?.email
        self.fixing = row == nil ? nil : fixing
        savedKind = row.map { Kind(rawValue: $0.type) ?? .imap }
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
        guard editing == nil, type != .hodhod, type != .gentlemail else { return nil }
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
        if type != picked, [Kind.hodhod, .gentlemail].contains(type) || picked == .gentlemail { password = "" }
        kind = picked
        applied = nil
        trash = nil
    }

    mutating func importHodHod(_ profile: EncryptedMailboxProfile, address: String) throws {
        guard editing == nil || (editing == address && type == .hodhod) else {
            throw HostedMailboxIssue.unauthorized
        }
        pick(.hodhod)
        email = address
        found = nil
        source = .keychain
        password = String(decoding: try JSONEncoder().encode(profile), as: UTF8.self)
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

    /// Whether the sheet was opened for a new password, which it then asks for: kept is what failed.
    var asksPassword: Bool { fixing == .appPassword || fixing == .password }

    /// What the password field shows before one is typed: an App Password's shape for Gmail. An edit opened for a new
    /// password asks for it, as a new account's field does.
    var passwordPrompt: String {
        if editing != nil, !asksPassword { return String(localized: "unchanged — leave blank to keep") }
        if type == .gmail { return "abcd efgh ijkl mnop" }
        switch fixing {
        case .appPassword?: return String(localized: "paste the new app password")
        case .password?: return String(localized: "type the password")
        default: return String(localized: "paste the password or app password")
        }
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
        if type == .gentlemail, host.trimmed.isEmpty { return String(localized: "Enter the hostname of your GentleMail server.") }
        if type == .imap, host.trimmed.isEmpty { return String(localized: "Type the incoming server’s name.") }
        if editing == nil, credential == nil {
            return type == .hodhod ? String(localized: "Import a HodHod mailbox profile.")
                : String(localized: "Type the account’s password.")
        }
        if asksPassword, typesPassword, credential == nil {
            return fixing == .appPassword ? String(localized: "Paste the new app password.")
                : String(localized: "Type the account’s password.")
        }
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
        if type == .gentlemail { return (.string(host.trimmed), .number(443), nil, nil, nil, nil) }
        guard type == .imap else { return (nil, nil, nil, nil, nil, nil) }
        return (.string(host.trimmed), .number(Double(port993)), security == .starttls,
                smtpHost.trimmed.isEmpty ? nil : .string(smtpHost.trimmed),
                smtpPort == outgoingUnsaved.port ? nil : .number(Double(port587)),
                smtpSecurity == outgoingUnsaved.security ? nil : smtpSecurity == .starttls)
    }

    /// The servers an edited account reads and sends its mail at, as the form holds them: an outgoing server the file
    /// doesn't name is its type's.
    private var readsAt: (host: JSONValue?, port: JSONValue?, starttls: Bool?, smtpHost: JSONValue?,
                          smtpPort: JSONValue?, smtpStarttls: Bool?)
    {
        (.string(host.trimmed), .number(Double(port993)), security == .starttls,
         smtpHost.trimmed.isEmpty ? nil : .string(smtpHost.trimmed), .number(Double(port587)),
         smtpSecurity == .starttls)
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
    /// added (`testsOnceAdded`). An edited account's password is tried where it reads its mail (#51): an Other IMAP
    /// account's servers as typed, and another's as the server listed them, which are its type's unless its file names
    /// others; a type picked anew, its type's.
    var test: API.TryBody? {
        if type == .proton { return editing.map { API.TryBody(email: $0) } }
        if let credential {
            let s = type != .imap && type == savedKind && !host.trimmed.isEmpty ? readsAt : servers
            return API.TryBody(draft: API.AccountDraft(credential: credential, email: address, host: s.host, port: s.port,
                                                       smtpHost: s.smtpHost, smtpPort: s.smtpPort,
                                                       smtpStarttls: s.smtpStarttls, starttls: s.starttls,
                                                       type: .string(type.rawValue)))
        }
        return editing.map { API.TryBody(email: $0) }
    }

    /// When the sheet's Save tries the login (`AccountSave`).
    enum Tries: Equatable {
        /// Never: a plain edit, or an account added from Settings, which its card tests once it shows.
        case never
        /// As typed, before anything is kept.
        case typed
        /// Once kept, as kept.
        case kept
    }

    /// When Save tries the login: from the first run, as typed, but a Proton account, whose Bridge password only a
    /// saved account is tried with; for a password typed to fix the account's login from its Sync card (#51), as
    /// typed, so a password that doesn't log in is never kept; for another fix (its server), once kept.
    func tries(firstRun: Bool) -> Tries {
        if editing == nil {
            guard firstRun else { return .never }
            return type == .proton ? .kept : .typed
        }
        if asksPassword, credential != nil { return .typed }
        return fixing == nil ? .never : .kept
    }

    /// The page the password's row shows (#51): Google's App Passwords under Gmail's steps, or the provider's page
    /// where its password is made (`link`); nil where it shows none.
    var passwordPage: String? {
        switch type {
        case .gmail: keptElsewhere == nil ? Self.appPasswords.absoluteString : nil
        case .imap: keptElsewhere == nil ? link?.url : nil
        case .proton: link?.url
        case .hodhod, .gentlemail: nil
        }
    }

    /// What the sheet says of `tested`, under the password's row, which already says some of it (#51): the page the
    /// row shows isn't offered twice, and under Gmail's steps, a password Gmail turned down is said in one line that
    /// leans on them, where the core's sentence would say the steps' first and last lines again. Its kind shows in
    /// plain words in small type (`Tested.plainWords(of:)`) only where the core's sentence doesn't say it already.
    func inSheet(_ tested: Tested) -> Tested {
        guard case .failed(let why, let link, var detail) = tested else { return tested }
        if Tested.saidInFull(detail) { detail = nil }
        let shown = link != nil && link?.url == passwordPage
        if type == .gmail, shown { return .failed(Self.gmailTurnedDown, link: nil, detail: detail) }
        return .failed(why, link: shown ? nil : link, detail: detail)
    }

    /// What the sheet says under Gmail's steps of a password Gmail turned down.
    static let gmailTurnedDown = String(localized: """
        Gmail turned down the password: paste an App Password made with the steps above.
        """)

    /// What an address being added says when MailMind has an account for it already (`accounts`): both servers would
    /// turn it down, so it isn't sent, and it is said as it is typed.
    func alreadySetUp(among accounts: [String]) -> String? {
        let typed = address.lowercased()
        guard editing == nil, typed.contains("@"), accounts.contains(where: { $0.trimmed.lowercased() == typed }) else {
            return nil
        }
        return String(localized: "\(address) is already set up in MailMind.")
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
            "editing": editing as Any, "fixing": fixing as Any, "alias": alias, "signature": signature, "email": email, "kind": kind as Any,
            "host": host, "port": port, "security": security, "smtpHost": smtpHost, "smtpPort": smtpPort,
            "smtpSecurity": smtpSecurity, "trash": trash as Any, "source": source,
            "password": password.isEmpty ? "none" : "typed", "opRef": opRef, "envVar": envVar,
        ], displayStyle: .struct)
    }
}

/// What a test of an account came to (`testConnection`): connected, or what went wrong in plain words with what to do
/// next, the page that helps, and the class of the trouble (`detail`), which shows only as `plainWords(of:)` says it.
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

    /// It logged in to read the mail, and only the outgoing server turned it down (the class `connections` names
    /// "SMTP …").
    var sendingOnly: Bool {
        guard case .failed(_, _, let detail) = self else { return false }
        return detail?.hasPrefix("SMTP ") == true
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

    /// The kind of sync trouble a test's answer is, as a sync's `last_error_kind` names it, from the class
    /// `connections::quiet` gives it: nil where it logged in, and for a trouble no sync has (the outgoing server's, a
    /// port typed wrong).
    static func kind(of answer: API.AccountsTestAnswer) -> String? {
        guard case .couldntLogIn(let out) = answer else { return nil }
        switch out.error {
        case "AUTHENTICATIONFAILED": return "AUTH"
        case "no password": return "NO_PASSWORD"
        case "connection refused", "host not found": return "NETWORK"
        case "timeout": return "TIMEOUT"
        case "certificate changed": return "TLS_PIN"
        default:
            // Gmail's refusals are said in sentences of their own, which stand for their class too.
            return !out.error.isEmpty && out.error == out.says ? "AUTH" : nil
        }
    }

    /// The kinds of trouble the core's sentence for a test says in full (`login_words`): a password turned down or
    /// missing, a server that didn't answer, a certificate, a secure connection, a port. Their plain words would say
    /// the sentence again; a connection lost, or no outgoing server, has only the core's general sentence, so its
    /// plain words say what it was.
    static func saidInFull(_ detail: String?) -> Bool {
        guard let detail else { return false }
        let kind = detail.hasPrefix("SMTP ") ? String(detail.dropFirst("SMTP ".count)) : detail
        return Self.inFull.contains(kind)
    }

    private static let inFull: Set<String> = [
        "AUTHENTICATIONFAILED", "no password", "connection refused", "host not found", "timeout", "certificate changed",
        "SSLCertVerificationError", "SSLError", "port must be 1–65535",
    ]

    /// The classes of a connection lost part way.
    private static let lost: Set<String> = [
        "abort", "ConnectionResetError", "ConnectionAbortedError", "BrokenPipeError", "SMTPServerDisconnected",
        "SSLEOFError",
    ]

    /// A test's trouble of class `detail` (`connections::quiet`'s, the outgoing server's after "SMTP ") in plain
    /// words, as the Sync card says a trouble of its kind: a password turned down is "The server turned down the
    /// password", never the server's AUTHENTICATIONFAILED. Nil for a class with no words of its own, an error's name,
    /// which only Help › Copy Diagnostics carries.
    static func plainWords(of detail: String?) -> String? {
        guard let detail else { return nil }
        switch detail {
        case "port must be 1–65535":
            return String(localized: "The port isn’t a number from 1 to 65535.")
        case "SMTP port must be 1–65535":
            return String(localized: "The outgoing server’s port isn’t a number from 1 to 65535.")
        default:
            break
        }
        let outgoing = detail.hasPrefix("SMTP ")
        return switch (outgoing ? String(detail.dropFirst("SMTP ".count)) : detail, outgoing) {
        case ("AUTHENTICATIONFAILED", false): String(localized: "The server turned down the password.")
        case ("AUTHENTICATIONFAILED", true): String(localized: "The outgoing server turned down the password.")
        case ("no password", false): String(localized: "MailMind has no password for this account.")
        case ("no password", true): String(localized: "MailMind has no password for the outgoing server.")
        case ("connection refused", false), ("host not found", false):
            String(localized: "MailMind couldn’t reach the server.")
        case ("connection refused", true), ("host not found", true):
            String(localized: "MailMind couldn’t reach the outgoing server.")
        case ("timeout", false): String(localized: "The server didn’t answer in time.")
        case ("timeout", true): String(localized: "The outgoing server didn’t answer in time.")
        case ("certificate changed", false): String(localized: "The server showed a different certificate.")
        case ("certificate changed", true): String(localized: "The outgoing server showed a different certificate.")
        case ("SSLCertVerificationError", false): String(localized: "The server’s certificate isn’t trusted.")
        case ("SSLCertVerificationError", true): String(localized: "The outgoing server’s certificate isn’t trusted.")
        case ("SSLError", false): String(localized: "A secure connection couldn’t be made.")
        case ("SSLError", true): String(localized: "A secure connection to the outgoing server couldn’t be made.")
        // The Python server names a connection that went by its error's name.
        case (let lost, false) where Self.lost.contains(lost): String(localized: "The connection to the server was lost.")
        case (let lost, true) where Self.lost.contains(lost):
            String(localized: "The connection to the outgoing server was lost.")
        case ("NoSmtpServer", _): String(localized: "The account names no outgoing server.")
        default: nil
        }
    }
}

/// What a test adds to an account's Sync card (#51). The card already says what its sync's trouble is, in one
/// sentence, with the page that helps: a test that failed the same way adds nothing to them and says only that it
/// did; one that failed another way says its own sentence, with its page where the card shows none. Never the
/// trouble's class, which Help › Copy Diagnostics carries.
enum TestedOnCard: Equatable {
    case running
    case connected
    /// It failed as the sync did.
    case same
    /// It failed another way, or the card shows no trouble.
    case other(String, link: API.Link?)

    /// What the card says of `tested`, a test whose trouble is of `kind` (`Tested.kind(of:)`), at an account of
    /// `type` whose card shows a sync's trouble of `standing` (nil while there is none) and `page`.
    static func of(_ tested: Tested, kind: String?, standing: String?, type: String?, page: API.Link?) -> TestedOnCard {
        switch tested {
        case .running: return .running
        case .connected: return .connected
        case .failed(let why, let link, _):
            // The same kind of trouble, and the same help: at Gmail, a test with no App Password page is a sign-in
            // or IMAP switched off, which no App Password fixes, and one with it is a password where the card says
            // to sign in. A Proton test points to Bridge's own page, which its card's Bridge password stands in for.
            let sameHelp = type == "proton" || (link == nil) == (page == nil)
            if let standing, kind == standing, sameHelp { return .same }
            return .other(why, link: link != nil && link?.url == page?.url ? nil : link)
        }
    }
}

fileprivate extension String {
    var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
}
#endif
