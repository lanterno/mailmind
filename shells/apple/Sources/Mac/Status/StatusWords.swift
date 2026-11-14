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

    /// The pill's words (`pillLabel`): what can't connect, then what is running, then when mail was last synced.
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
        if reconciling { return Pill(dot: .busy, text: String(localized: "Reconciling…")) }
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

    /// The status menu's line on AI (`AiLine`): off with no key, else the model, what today cost of the limit, a batch
    /// out, and a last call that failed.
    static func ai(_ ai: API.AiStatus) -> (text: String, failing: Bool) {
        if ai.engine == "none" { return (String(localized: "AI is off · no API key"), false) }
        var text = "Claude API · \(ai.model)"
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

    /// What the page promises about AI and privacy (`AI_PRIVACY`).
    static let aiPrivacy = String(localized: """
        With AI on, the text of a message goes to Anthropic only when it is summarized, briefed or drafted; nothing \
        else leaves this machine.
        """)

    /// An account's line in Settings (`HealthLine`): a certificate that changed, a sync error, or when it last synced.
    enum HealthLine: Equatable, Sendable {
        case none
        case certificateChanged(when: String)
        case syncError(kind: String, when: String)
        case synced(when: String, count: Int)
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
