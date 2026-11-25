#if os(macOS)
import AppKit
import Foundation

/// Help › Copy Diagnostics and Report a Problem: what the owner needs to see why MailMind misbehaves on a friend's
/// Mac, as text the friend can read before sending, and nothing of theirs. It holds the app's version and build, this
/// Mac's system and chip, the profile, whether AI is on and with which provider, each account by its number with its
/// kind and its servers, the error classes lately, the core's own account of itself (`/api/debug/health` and
/// `/api/debug/jobs`) and the log's last lines.
///
/// No address, subject, name or secret goes in it. The routes it reads say only ids, counts and an error's class, and
/// the log withholds a value that holds an address; it leaves out what a server wrote in words of its own (the AI's
/// last error, a test's answer); a mail server is named only when it is a provider's or this Mac's (`server`), as a
/// server of a person's own or their work's names them; and the whole text is passed through `redacted` once more,
/// which takes out anything shaped like an address and the name written before one.
enum Diagnostics {
    /// Where a problem is reported: the issues of the repository the releases come from.
    static let issues = URL(string: "https://github.com/lanterno/mailmind/issues/new")!
    /// How many of the log's last lines it carries.
    static let logLines = 40
    /// How much of the log's end it reads to find them.
    static let logBytes = 96 * 1024

    /// This build and this Mac: nothing of a person's.
    struct About: Equatable, Sendable {
        var version: String
        var build: String
        /// The slice this process runs: arm64, or x86_64 (under Rosetta on Apple silicon, it says so).
        var arch: String
        var system: String
        var chip: String
        var model: String

        static var current: About {
            let info = Bundle.main.infoDictionary ?? [:]
            #if arch(arm64)
            var arch = "arm64"
            #else
            var arch = "x86_64"
            #endif
            if Diagnostics.sysctlNumber("sysctl.proc_translated") == 1 { arch += " under Rosetta" }
            return About(version: info["CFBundleShortVersionString"] as? String ?? "?",
                         build: info["CFBundleVersion"] as? String ?? "?",
                         arch: arch,
                         system: ProcessInfo.processInfo.operatingSystemVersionString,
                         chip: Diagnostics.sysctlText("machdep.cpu.brand_string") ?? "?",
                         model: Diagnostics.sysctlText("hw.model") ?? "?")
        }
    }

    /// What the core answered, read for the text: each nil where it couldn't be read.
    struct Read: Sendable {
        var digest: API.Digest?
        var accounts: [API.AccountRow]?
        var health: [API.AccountStanding]?
        var glance: JSONValue?
        var jobs: JSONValue?
    }

    // MARK: The menu's items

    /// Copy the text for this process: its core, if it opened, and its data folder's log.
    @MainActor
    static func copy() async {
        let app = MailApp.shared
        var failure: String?
        if case .failed(let why) = app.stage { failure = why }
        let text = await gather(core: app.core, folder: app.folder, failure: failure)
        AppSeams.pasteboard.clearContents()
        AppSeams.pasteboard.setString(text, forType: .string)
        AppStatus.shared.say(String(localized: "Diagnostics copied, with no addresses or passwords in them"))
    }

    /// Say how to report a problem, copy the text, and open the issues page if the person wants it.
    @MainActor
    static func report() async {
        let alert = NSAlert()
        alert.messageText = String(localized: "Report a problem")
        alert.informativeText = reportWords
        alert.addButton(withTitle: String(localized: "Copy and Open GitHub"))
        alert.addButton(withTitle: String(localized: "Copy Only"))
        alert.addButton(withTitle: String(localized: "Cancel"))
        let answer = alert.runModal()
        guard answer != .alertThirdButtonReturn else { return }
        await copy()
        if answer == .alertFirstButtonReturn { AppSeams.links.open(issues) }
    }

    static let reportWords = String(localized: """
        MailMind copies what it knows about the problem: its version, this Mac, how each account is set up and what \
        went wrong lately, with no addresses, subjects, names or passwords in it. Paste it into a new issue on \
        GitHub, or send it to whoever gave you MailMind.
        """)

    // MARK: Gathering

    /// The text, from `core` when it opened (else `failure`, what kept it from opening) and the log in `folder`.
    static func gather(core: Core?, folder: URL?, failure: String?, about: About = .current,
                       now: Date = AppSeams.clock.now) async -> String
    {
        var read = Read()
        if let core {
            read.digest = try? await core.get(API.Get.Digest.self)
            if let listed = try? await core.get(API.Get.Accounts.self) { read.accounts = listed.accounts }
            if let standing = try? await core.get(API.Get.AccountHealth.self) { read.health = standing.accounts }
            read.glance = json(await core.send("GET", API.Get.DebugHealth.path, Data()))
            read.jobs = json(await core.send("GET", API.Get.DebugJobs.path, Data()))
        }
        let log = folder.flatMap { logTail(at: $0.appending(path: "mailmind.log")) } ?? ""
        return text(about: about, read: read, opened: core != nil, failure: failure, log: log, now: now)
    }

    /// The whole text, every line of it passed through `redacted`.
    static func text(about: About, read: Read, opened: Bool, failure: String?, log: String, now: Date) -> String {
        var lines = [
            "MailMind diagnostics · \(stamp(now))",
            "No addresses, subjects, names or passwords are in this text.",
            "",
            "MailMind \(about.version) (build \(about.build)), \(about.arch)",
            "macOS: \(about.system) · \(about.chip) · \(about.model)",
        ]
        if let failure { lines.append("MailMind didn’t open: \(failure)") }
        if opened {
            if let digest = read.digest {
                lines.append("Profile: \(digest.profile)")
                let ai = digest.ai
                lines.append("AI: \(ai.isOn ? "on" : "off") · \(ai.provider.rawValue)")
            } else {
                lines.append("Profile and AI: couldn’t be read")
            }
        }
        if let accounts = read.accounts {
            lines += ["", "Accounts (\(accounts.count))"]
            lines += Self.accounts(accounts, health: read.health ?? [], now: now).map { "  " + $0 }
        }
        let errors = recentErrors(read: read, log: log, now: now)
        if opened || !errors.isEmpty {
            lines += ["", "Errors lately"]
            lines += errors.isEmpty ? ["  none"] : errors.map { "  " + $0 }
        }
        if opened {
            lines += ["", "Health (/api/debug/health)"]
            var glance = read.glance
            // The jobs are listed once, under their own heading.
            if case .object(var fields)? = glance {
                fields.removeValue(forKey: "jobs")
                glance = .object(fields)
            }
            lines.append("  " + (glance.map(compact) ?? "couldn’t be read"))
            lines += ["", "Jobs (/api/debug/jobs)"]
            if case .object(let fields)? = read.jobs, case .array(let jobs)? = fields["jobs"] {
                lines += jobs.map { "  " + compact($0) }
            } else {
                lines.append("  couldn’t be read")
            }
        }
        lines += ["", "Log (the last lines of mailmind.log)"]
        let logged = logLines(log)
        lines += logged.isEmpty ? ["  none"] : logged.map { "  " + $0 }
        return lines.map(redacted).joined(separator: "\n") + "\n"
    }

    /// Each account by its number: its kind, its incoming and outgoing servers (`server`), where its password is kept
    /// (never the item or variable that names it), and how its syncs stand. Never its address.
    static func accounts(_ rows: [API.AccountRow], health: [API.AccountStanding], now: Date) -> [String] {
        rows.enumerated().map { index, row in
            var parts = ["Account \(index + 1)", row.type]
            if row.disabled { parts.append("turned off") }
            if let host = row.host, !host.isEmpty {
                parts.append("in \(server(host)):\(row.port) \(row.starttls ? "STARTTLS" : "TLS")")
            }
            if let host = row.smtpHost, !host.isEmpty {
                parts.append("out \(server(host)):\(row.smtpPort) \(row.smtpStarttls ? "STARTTLS" : "TLS")")
            }
            parts.append(row.credential.set ? "password: \(row.credential.style ?? "?")" : "no password set")
            if let record = row.email.flatMap({ email in health.first { $0.account == email } }) {
                parts.append(standing(record, now: now))
            }
            return parts.joined(separator: " · ")
        }
    }

    /// The providers whose mail servers are named: Gmail, iCloud, Yahoo, AOL, Fastmail, and Outlook and Microsoft 365.
    /// Their servers' names say nothing of whose account it is.
    static let providers = [
        "gmail.com", "googlemail.com", "me.com", "icloud.com", "mac.com", "yahoo.com", "aol.com", "fastmail.com",
        "messagingengine.com", "outlook.com", "office365.com", "hotmail.com", "live.com",
    ]
    /// This Mac's own, as Proton Bridge's are.
    static let thisMac = ["127.0.0.1", "localhost", "::1"]

    /// A mail server as the text names it: a provider's (`providers`) or this Mac's as it is; any other as "own
    /// server", since the name of a server of a person's own, their family's or their work's says who they are.
    static func server(_ host: String) -> String {
        let name = host.trimmingCharacters(in: .whitespaces.union(CharacterSet(charactersIn: "."))).lowercased()
        if thisMac.contains(name) { return name }
        if providers.contains(where: { name == $0 || name.hasSuffix("." + $0) }) { return name }
        return "own server"
    }

    /// How an account's syncs stand: its last error's kind and class, and when it last synced.
    static func standing(_ record: API.AccountStanding, now: Date) -> String {
        var said: [String] = []
        if let kind = record.lastErrorKind, !kind.isEmpty {
            let when = record.lastErrorTs.map { " \(StatusWords.ago($0, now: now))" } ?? ""
            said.append("last error \(kind) (\(record.lastError ?? "?"))\(when)")
        }
        if let ok = record.lastOkTs, !ok.isEmpty {
            said.append("synced \(StatusWords.ago(ok, now: now)), \(record.syncCount) syncs")
        } else {
            said.append("never synced")
        }
        return said.joined(separator: " · ")
    }

    /// The error classes lately: each account's last, each job's last, and the log's warnings and errors.
    static func recentErrors(read: Read, log: String, now: Date) -> [String] {
        var said: [String] = []
        let emails = (read.accounts ?? []).map(\.email)
        for record in read.health ?? [] {
            guard let kind = record.lastErrorKind, !kind.isEmpty else { continue }
            let number = emails.firstIndex(of: record.account).map { "account \($0 + 1)" } ?? "an account"
            let when = record.lastErrorTs.map { ", \(StatusWords.ago($0, now: now))" } ?? ""
            said.append("\(number): \(kind) (\(record.lastError ?? "?"))\(when)")
        }
        if case .object(let fields)? = read.jobs, case .array(let jobs)? = fields["jobs"] {
            for case .object(let job) in jobs {
                guard case .string(let error)? = job["last_error"], case .string(let name)? = job["name"] else { continue }
                said.append("job \(name): \(error)")
            }
        }
        var classes: [String: Int] = [:]
        for entry in logEntries(log) where entry.level == "ERROR" || entry.level == "WARNING" {
            if let error = entry.error { classes[error, default: 0] += 1 }
        }
        for (error, count) in classes.sorted(by: { $0.key < $1.key }) {
            said.append("in the log: \(error) × \(count)")
        }
        return said
    }

    // MARK: The log

    /// One line of the log, as `mailmind.log` holds it: a JSON object a line.
    struct Entry: Equatable {
        var ts: String
        var level: String
        var logger: String
        var message: String
        var error: String?
    }

    /// The log's last bytes, from the start of their first whole line.
    static func logTail(at file: URL) -> String? {
        guard let handle = try? FileHandle(forReadingFrom: file) else { return nil }
        defer { try? handle.close() }
        guard let end = try? handle.seekToEnd() else { return nil }
        let start = end > UInt64(logBytes) ? end - UInt64(logBytes) : 0
        try? handle.seek(toOffset: start)
        guard let data = try? handle.readToEnd() else { return nil }
        var text = String(decoding: data, as: UTF8.self)
        if start > 0, let newline = text.firstIndex(of: "\n") { text = String(text[text.index(after: newline)...]) }
        return text
    }

    /// The lines that read as the log's own, the rest left out: a line that isn't one can't be told safe.
    static func logEntries(_ log: String) -> [Entry] {
        log.split(whereSeparator: \.isNewline).compactMap { line in
            guard case .object(let fields)? = try? JSONDecoder().decode(JSONValue.self, from: Data(line.utf8)),
                  case .string(let message)? = fields["msg"] else { return nil }
            func text(_ name: String) -> String? {
                if case .string(let value)? = fields[name] { return value }
                return nil
            }
            return Entry(ts: text("ts") ?? "", level: text("level") ?? "", logger: text("logger") ?? "",
                         message: message, error: text("error"))
        }
    }

    /// The last `logLines` of the log, each as the terminal shows one: when, how grave, whose, what, and its class.
    static func logLines(_ log: String) -> [String] {
        logEntries(log).suffix(logLines).map { entry in
            "\(entry.ts) \(entry.level) [\(entry.logger)] \(entry.message)" + (entry.error.map { ": \($0)" } ?? "")
        }
    }

    // MARK: Keeping it to what is safe

    /// A name before an address, quoted (`"Ada Lovelace" <ada@example.test>`) or not (up to four capitalised words).
    nonisolated(unsafe) private static let named = #/(?:"[^"\n]*"[ \t]*|(?:\p{Lu}[\p{L}\p{M}'’.\-]*[ \t]+){1,4})<[^<>\s]*(?:@|%40)[^<>\s]*>/#
    /// Anything with an @ in it, or `%40` as a URL writes one.
    nonisolated(unsafe) private static let address = #/[^\s<>"'(),;:\[\]{}]*(?:@|%40)[^\s<>"'(),;:\[\]{}]*/#

    /// `text` with every address taken out, and the name written before one.
    static func redacted(_ text: String) -> String {
        text.replacing(named, with: "<address>").replacing(address, with: "<address>")
    }

    /// `value` as one line of JSON, keys in order, an address taken out of a key as of a value.
    static func compact(_ value: JSONValue) -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        guard let data = try? encoder.encode(scrubbed(value)) else { return "?" }
        return String(decoding: data, as: UTF8.self)
    }

    /// `value` with an address taken out of every key and string.
    static func scrubbed(_ value: JSONValue) -> JSONValue {
        switch value {
        case .string(let text): .string(redacted(text))
        case .array(let items): .array(items.map(scrubbed))
        case .object(let fields):
            .object(Dictionary(fields.map { (redacted($0.key), scrubbed($0.value)) }, uniquingKeysWith: { first, _ in first }))
        default: value
        }
    }

    /// An answer's JSON, or nil when it is a refusal or isn't JSON.
    private static func json(_ answer: CoreAnswer) -> JSONValue? {
        guard (200..<300).contains(answer.status) else { return nil }
        return try? JSONDecoder().decode(JSONValue.self, from: answer.body)
    }

    private static func stamp(_ now: Date) -> String {
        now.formatted(.iso8601)
    }

    // MARK: This Mac

    static func sysctlText(_ name: String) -> String? {
        var size = 0
        guard sysctlbyname(name, nil, &size, nil, 0) == 0, size > 0 else { return nil }
        var bytes = [CChar](repeating: 0, count: size)
        guard sysctlbyname(name, &bytes, &size, nil, 0) == 0 else { return nil }
        let text = String(decoding: bytes.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
        return text.isEmpty ? nil : text
    }

    static func sysctlNumber(_ name: String) -> Int32? {
        var value: Int32 = 0
        var size = MemoryLayout<Int32>.size
        guard sysctlbyname(name, &value, &size, nil, 0) == 0 else { return nil }
        return value
    }
}
#endif
