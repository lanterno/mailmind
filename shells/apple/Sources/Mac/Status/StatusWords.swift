#if os(macOS)
import Foundation

/// What the status says about syncing, each account and AI, in the page's words (features/status/model.ts). No views:
/// each is a plain function of what the routes answered, so the tests hold the words.
enum StatusWords {
    /// The dot beside the status's words.
    enum Dot: Equatable, Sendable {
        case none, ok, busy, error
    }

    /// The pill's one thing that matters about syncing.
    struct Pill: Equatable, Sendable {
        let dot: Dot
        let text: String
    }

    /// How one account stands, as the status menu and Settings say it.
    enum Standing: Equatable, Sendable {
        case unknown, connected, syncing, cantConnect
    }

    /// The part of an address before the @, as the page shortens one.
    static func short(_ address: String) -> String {
        String(address.split(separator: "@", maxSplits: 1, omittingEmptySubsequences: false).first ?? "")
    }

    /// An account's name: its alias, else its address shortened.
    static func name(_ account: String, aliases: [String: String?]) -> String {
        if let alias = aliases[account] ?? nil, !alias.isEmpty { return alias }
        return short(account)
    }

    /// How each account stands: the digest's word where it has one, else what its syncs came to
    /// (`/api/account-health`). The Rust server's digest says nothing of syncing yet, so the record of each account's
    /// syncs is what tells a failing one.
    static func standing(of account: String, digest: API.Digest?, health: [API.AccountStanding]) -> Standing {
        switch digest?.status[account]?.health {
        case "error": return .cantConnect
        case "syncing": return .syncing
        case "ok": return .connected
        default: break
        }
        guard let record = health.first(where: { $0.account == account }) else { return .unknown }
        if let error = record.lastError, !error.isEmpty { return .cantConnect }
        return record.healthy ? .connected : .unknown
    }

    /// The accounts that can't connect, in the digest's order.
    static func bad(_ accounts: [String], digest: API.Digest?, health: [API.AccountStanding]) -> [String] {
        accounts.filter { standing(of: $0, digest: digest, health: health) == .cantConnect }
    }

    /// When mail was last synced: the digest's last sync, else the latest any account's syncs record.
    static func lastSynced(digest: API.Digest?, health: [API.AccountStanding]) -> Date? {
        if let last = digest?.sync.last, let at = Moment.parse(last) { return at }
        return health.compactMap { $0.lastOkTs.flatMap(Moment.parse) }.max()
    }

    /// How far the Inboxes' first syncs have got, over the accounts whose older mail is still being read: how many
    /// messages they have read of how many, and how many Inboxes. Nil when none is.
    static func reading(_ digest: API.Digest) -> (done: Int, total: Int, inboxes: Int)? {
        let reading = digest.accounts.compactMap { account -> (Int, Int)? in
            guard let said = digest.status[account], let done = said.done, let total = said.total else { return nil }
            return (done, total)
        }
        guard !reading.isEmpty else { return nil }
        return (reading.map(\.0).reduce(0, +), reading.map(\.1).reduce(0, +), reading.count)
    }

    /// The pill's words (`pillLabel`): what can't connect, then how far a first sync has got, then what is running,
    /// then when mail was last synced.
    static func pill(digest: API.Digest?, health: [API.AccountStanding], syncing: Bool, reconciling: Bool,
                     now: Date, calendar: Calendar = .current, locale: Locale = .current) -> Pill
    {
        guard let digest else { return Pill(dot: .none, text: String(localized: "Connecting…")) }
        let bad = bad(digest.accounts, digest: digest, health: health)
        if let first = bad.first {
            return Pill(dot: .error, text: bad.count == 1
                ? String(localized: "\(name(first, aliases: digest.aliases)) can’t connect")
                : String(localized: "\(bad.count) accounts can’t connect"))
        }
        if let read = reading(digest) {
            let done = read.done.formatted(.number.locale(locale)), total = read.total.formatted(.number.locale(locale))
            return Pill(dot: .busy, text: read.inboxes == 1
                ? String(localized: "Reading your inbox: \(done) of \(total)")
                : String(localized: "Reading your inboxes: \(done) of \(total)"))
        }
        if reconciling { return Pill(dot: .busy, text: String(localized: "Checking against the server…")) }
        let anySyncing = digest.accounts.contains { standing(of: $0, digest: digest, health: health) == .syncing }
        if syncing || digest.sync.running || anySyncing { return Pill(dot: .busy, text: String(localized: "Syncing…")) }
        if digest.profile == "read-only" { return Pill(dot: .none, text: String(localized: "Read-only")) }
        if let last = lastSynced(digest: digest, health: health) {
            var style = calendar.isDate(last, inSameDayAs: now)
                ? Date.FormatStyle(date: .omitted, time: .shortened)
                : Date.FormatStyle().month(.abbreviated).day()
            style.calendar = calendar
            style.timeZone = calendar.timeZone
            style.locale = locale
            return Pill(dot: .ok, text: String(localized: "Synced \(last.formatted(style))"))
        }
        return Pill(dot: .none, text: String(localized: "Not synced yet"))
    }

    /// How long ago, in the fewest words (`relWhen`).
    static func ago(_ iso: String, now: Date) -> String {
        guard let at = Moment.parse(iso) else { return "" }
        let seconds = max(0, now.timeIntervalSince(at))
        if seconds < 90 { return String(localized: "just now") }
        if seconds < 5400 { return String(localized: "\(Int((seconds / 60).rounded()))m ago") }
        if seconds < 172_800 { return String(localized: "\(Int((seconds / 3600).rounded()))h ago") }
        return String(localized: "\(Int((seconds / 86400).rounded()))d ago")
    }

    /// Micro-dollars as the page writes money: rounded up to the cent (`usd`).
    static func dollars(_ micros: Int) -> String {
        let cents = (micros + 9999) / 10000
        return "$\((cents / 100).formatted(.number.locale(Locale(identifier: "en_US")))).\(String(format: "%02d", cents % 100))"
    }

    /// The status menu's line on AI (`AiLine`): off and why, else the provider and its model, what today cost of the
    /// limit, a batch out, and a last call that failed.
    static func ai(_ ai: API.AiStatus) -> (text: String, failing: Bool) {
        guard ai.isOn else {
            let off = switch ai.off {
            case .turnedOff?: String(localized: "AI mode is off")
            case .noUrl?: String(localized: "AI is off · no URL for the service")
            case .noModel?: String(localized: "AI is off · no model chosen")
            case .noKey?: String(localized: "AI is off · no \(ai.provider.name) API key")
            case nil: String(localized: "AI is off · no API key")
            }
            return (off, false)
        }
        var text = "\(ai.provider.title) · \(ai.model)"
        if let spent = ai.spentTodayMicros, spent > 0 {
            text += " · \(dollars(spent))"
            if ai.limitMicros > 0 { text += String(localized: " of \(dollars(ai.limitMicros))") }
            text += String(localized: " today")
        }
        if let batch = ai.batchWaiting, batch > 0 { text += String(localized: " · \(batch) out in a batch") }
        let failing = ai.lastOk == false && !(ai.lastError ?? "").isEmpty
        if failing, let error = ai.lastError { text += String(localized: " · last call failed: \(error)") }
        return (text, failing)
    }

    /// An account's line in Settings (`HealthLine`): a certificate that changed, a sync error, or when it last synced.
    enum HealthLine: Equatable, Sendable {
        case none
        case certificateChanged(when: String)
        case syncError(kind: String, when: String)
        case synced(when: String, count: Int)
    }

    /// The background jobs whose last round failed: a job's last error is cleared by a round that goes through.
    static func failing(_ jobs: [API.Job]) -> [String] {
        jobs.filter { !($0.lastError ?? "").isEmpty }.map(\.name)
    }

    /// The status menu's one line on background work that keeps failing, or nil when none is.
    static func failingLine(_ names: [String]) -> String? {
        guard let last = names.last else { return nil }
        let words = names.map(jobWords)
        let named = names.count == 1 ? words[0]
            : words.dropLast().joined(separator: ", ") + String(localized: " and ") + jobWords(last)
        return String(localized: "Some background work isn’t going through: \(named). Help › Copy Diagnostics says more.")
    }

    /// A background job's name, as a person would say what it does. One this build doesn't know keeps its name.
    static func jobWords(_ name: String) -> String {
        switch name {
        case "sync": String(localized: "syncing")
        case "snooze": String(localized: "snoozes and follow-ups")
        case "reconcile": String(localized: "checking mail against the server")
        case "draft-push": String(localized: "saving drafts to the server")
        case "expire": String(localized: "expiry rules")
        case "forget": String(localized: "letting go of old records")
        case "backfill": String(localized: "fetching older messages’ text")
        case "older-mail": String(localized: "reading your older mail")
        case "assess": String(localized: "summaries")
        case "draft-ahead": String(localized: "replies drafted ahead")
        case "brief-ahead": String(localized: "the brief")
        case "review-ahead": String(localized: "the weekly review")
        case "tidy": String(localized: "Tidy")
        default: name
        }
    }

    /// Proton Bridge's own page, for an account Bridge isn't answering for.
    static let protonBridge = API.Link(url: "https://proton.me/mail/bridge", words: String(localized: "Get Proton Bridge"))
    /// Where a Google Account makes the App Password Gmail's IMAP takes.
    static let appPasswords = API.Link(url: AccountDraft.appPasswords.absoluteString,
                                       words: String(localized: "Make an App Password"))

    /// Whether a sync's trouble at an account of `type` is Proton Bridge not running: Bridge listens on this Mac, so
    /// with Bridge closed the connection is refused at once (NETWORK).
    static func bridgeDown(_ kind: String?, type: String?) -> Bool {
        type == "proton" && kind == "NETWORK"
    }

    /// Whether it is Proton Bridge running but not answering in time (TIMEOUT): something took the connection, so
    /// Bridge is open, and still starting, busy with its first sync of a big mailbox, or stuck.
    static func bridgeSlow(_ kind: String?, type: String?) -> Bool {
        type == "proton" && kind == "TIMEOUT"
    }

    /// What a failed sync's kind (`last_error_kind`) means, in a sentence that says what to do next. A kind this
    /// build doesn't know reads as something that went wrong. `type` is the account's: a Proton account's trouble is
    /// its Bridge, and a Gmail account has no server's name or port to check but wants an App Password, each of whose
    /// refusals `error`, the trouble's class, tells apart. A login held back after a refusal fails as `LoginRefused`
    /// whatever Google first said, so Gmail's words for that class hold for each of Google's refusals.
    static func syncProblem(_ kind: String, type: String? = nil, error: String? = nil) -> String {
        if bridgeDown(kind, type: type) {
            return String(localized: """
                Proton Bridge isn’t running, so MailMind can’t reach this account. Start Proton Bridge and wait until \
                it says it is connected, then choose Retry.
                """)
        }
        if bridgeSlow(kind, type: type) {
            return bridgeSlowWords
        }
        switch (type, kind) {
        case ("proton", "AUTH"):
            return String(localized: """
                Proton Bridge didn’t accept the password. Keep the password Bridge shows for this address under Proton \
                Bridge password, not your Proton password.
                """)
        case ("gmail", "AUTH") where error == "LoginNeedsWebSignIn":
            return String(localized: """
                Google wants you to sign in on the web first. Sign in to Gmail in your browser, then choose Retry.
                """)
        case ("gmail", "AUTH") where error == "LoginImapOff":
            return String(localized: """
                IMAP is switched off for this account. Turn it on in Gmail’s settings, or ask your Workspace admin to, \
                then choose Retry.
                """)
        case ("gmail", "AUTH") where error == "LoginNeedsAppPassword":
            return String(localized: """
                Gmail turned down the password: it needs an App Password, not your Google password. Make one, then \
                choose Edit and paste it.
                """)
        case ("gmail", "AUTH"):
            return String(localized: """
                Gmail turned down the login. If you typed your Google password, Gmail needs an App Password instead: \
                make one, then choose Edit and paste it. If you already use one, sign in to Gmail in your browser and \
                check that IMAP is on, then choose Retry.
                """)
        case ("gmail", "NETWORK"):
            return String(localized: "MailMind couldn’t reach Gmail. Check your internet connection, then choose Retry.")
        default:
            break
        }
        return switch kind {
        case "AUTH":
            String(localized: """
                The server turned down the password. Choose Edit and type it again; if your provider uses app \
                passwords, make a new one and use that.
                """)
        case "TIMEOUT":
            String(localized: """
                The server didn’t answer in time. MailMind tries again on its own; if this keeps happening, check \
                your internet connection.
                """)
        case "NETWORK":
            String(localized: """
                MailMind couldn’t reach the server. Check your internet connection, then the server’s name and port \
                under Edit.
                """)
        case "UIDVALIDITY":
            String(localized: """
                The server renumbered this mailbox, so MailMind reads it again from the start. Nothing needs doing.
                """)
        case "TLS_PIN":
            String(localized: """
                The server showed a different certificate, so MailMind sent no password. Trust it only if you know \
                why it changed.
                """)
        case "IMAP":
            String(localized: """
                The server said no to what MailMind asked. Choose Retry; if this keeps happening, choose Help › Copy \
                Diagnostics and send them to whoever gave you MailMind.
                """)
        default:
            String(localized: """
                Something went wrong while syncing. Choose Retry; if this keeps happening, choose Help › Copy \
                Diagnostics and send them to whoever gave you MailMind.
                """)
        }
    }

    /// What a Proton account whose Bridge is open but didn't answer in time is told.
    static let bridgeSlowWords = String(localized: """
        Proton Bridge didn’t answer in time. If it is still starting or syncing, wait until it says it is connected, \
        then choose Retry; if it seems stuck, quit Bridge and open it again.
        """)

    /// The page that helps with a sync's trouble at an account of `type`, where there is one: Proton Bridge's, or
    /// where Google makes the App Password Gmail wants.
    static func syncHelp(_ kind: String, type: String? = nil, error: String? = nil) -> API.Link? {
        if bridgeDown(kind, type: type) || bridgeSlow(kind, type: type) { return protonBridge }
        let signIn = ["LoginNeedsWebSignIn", "LoginImapOff"].contains(error ?? "")
        return type == "gmail" && kind == "AUTH" && !signIn ? appPasswords : nil
    }

    /// The banner's words for the accounts that can't connect, and the page that helps. Where each is a Proton account
    /// whose Bridge isn't running, it says to start Bridge, and where each is one whose Bridge didn't answer in time,
    /// to wait for it or open it again; where the one is a Gmail account Google turned down, it says what Gmail wants;
    /// otherwise how many, by name. `type` and `name` look an account up; `health` is each account's syncs, which say
    /// the trouble's kind and class.
    static func alert(_ bad: [String], health: [API.AccountStanding], type: (String) -> String?,
                      name: (String) -> String) -> (text: String, link: API.Link?)
    {
        let names = bad.map(name).joined(separator: ", ")
        let troubles = bad.map { account in
            (record: health.first { $0.account == account }, type: type(account))
        }
        if !troubles.isEmpty, troubles.allSatisfy({ bridgeDown($0.record?.lastErrorKind, type: $0.type) }) {
            return (String(localized: "Proton Bridge isn’t running, so \(names) can’t connect. Start Proton Bridge."),
                    protonBridge)
        }
        if !troubles.isEmpty, troubles.allSatisfy({ bridgeSlow($0.record?.lastErrorKind, type: $0.type) }) {
            return (String(localized: """
                Proton Bridge didn’t answer in time, so \(names) can’t connect. If it is still syncing, wait; if it \
                seems stuck, quit Bridge and open it again.
                """), protonBridge)
        }
        if troubles.count == 1, let one = troubles.first, let kind = one.record?.lastErrorKind, one.type == "gmail",
           let link = syncHelp(kind, type: one.type, error: one.record?.lastError)
        {
            // A held login says only LoginRefused, whatever Google said first: only Google's own class is sure.
            return one.record?.lastError == "LoginNeedsAppPassword"
                ? (String(localized: "\(names) can’t connect: Gmail needs an App Password, not your Google password."), link)
                : (String(localized: """
                    \(names) can’t connect: Gmail turned down the login. If you typed your Google password, it needs \
                    an App Password.
                    """), link)
        }
        return (bad.count == 1 ? String(localized: "1 account couldn’t connect (\(names)).")
                    : String(localized: "\(bad.count) accounts couldn’t connect (\(names))."), nil)
    }

    static func healthLine(_ record: API.AccountStanding?, now: Date) -> HealthLine {
        guard let record else { return .none }
        let when = record.lastErrorTs.map { ago($0, now: now) } ?? ""
        if let error = record.lastError, !error.isEmpty, record.lastErrorKind == "TLS_PIN" {
            return .certificateChanged(when: when)
        }
        if let error = record.lastError, !error.isEmpty, let kind = record.lastErrorKind, !kind.isEmpty {
            return .syncError(kind: kind, when: when)
        }
        if let ok = record.lastOkTs, !ok.isEmpty { return .synced(when: ago(ok, now: now), count: record.syncCount) }
        return .none
    }
}

/// A moment as the server writes one: ISO 8601, with or without fractions of a second, with a zone or (as SQLite's
/// own clock writes) without one, read as UTC.
enum Moment {
    static func parse(_ text: String) -> Date? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return nil }
        for options: ISO8601DateFormatter.Options in [
            [.withInternetDateTime, .withFractionalSeconds], [.withInternetDateTime],
        ] {
            let reader = ISO8601DateFormatter()
            reader.formatOptions = options
            if let at = reader.date(from: trimmed) { return at }
        }
        let bare = DateFormatter()
        bare.locale = Locale(identifier: "en_US_POSIX")
        bare.timeZone = TimeZone(identifier: "UTC")
        for format in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd HH:mm:ss"] {
            bare.dateFormat = format
            if let at = bare.date(from: trimmed) { return at }
        }
        return nil
    }
}
#endif
