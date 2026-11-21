#if os(macOS)
import Foundation

/// What the mailbox says, in the page's words (features/mailbox/rows.ts, app/App.tsx, lib/emptyWords.ts), so a person
/// reads the same thing in the app and the page.
enum Words {
    /// A folder's name as the rail shows it: its role, or its own name for one of a person's (`roleLabel`). A nested
    /// folder is called by its last part ("Clients", not "Folders/Clients"); the server is still sent its whole name.
    static func role(_ folder: API.RailFolder) -> String {
        switch folder.role {
        case "custom": ownName(folder)
        case "all": String(localized: "All mail")
        default: folder.role.prefix(1).uppercased() + folder.role.dropFirst()
        }
    }

    /// A folder's own name, its last part (`own_name`). The rail kept for when no server answers names each folder
    /// whole, knowing no separator: there its last part by "/" is the one a person gave it.
    static func ownName(_ folder: API.RailFolder) -> String {
        let own = folder.ownName.isEmpty ? folder.name : folder.ownName
        guard own == folder.name, let last = own.split(separator: "/").last, own.contains("/") else { return own }
        return String(last)
    }

    /// What a short line calls a sender ("Reply to Ren…", "Not Ren’s words"): a person by their first name, and an
    /// organisation, or a name it can't tell, whole, as "Reply to Parcel…" would cut "Parcel Service" short. Nil with
    /// no name to go by, only an address.
    static func calling(_ name: String?, address: String? = nil) -> String? {
        let name = (name ?? "").trimmingCharacters(in: .whitespacesAndNewlines.union(CharacterSet(charactersIn: "\"'")))
        guard !name.isEmpty, !name.contains("@") else { return nil }
        let words = name.split(whereSeparator: \.isWhitespace).map(String.init)
        guard words.count > 1, isPerson(words, address: address) else { return name }
        return words[0]
    }

    /// Whether a name of several words is a person's. Never what an organisation's name has: a figure, an ampersand,
    /// a comma ("Mori, Ren" is said whole), a word such as Service, Desk, Bank or The. Then, with an address, a
    /// person's mostly carries their first name (ren@, ren.mori@), or their last with the first's initial beside it
    /// (rmori@, mori.r@), and a role's doesn't (news@, orders@, noreply@). The last name alone isn't a person's sign:
    /// an organisation's address carries the last word of its name as often ("Guild Gazette" <gazette@>), and a
    /// person's name said whole is never wrong. With no address, the name's shape: two to four capitalised words.
    private static func isPerson(_ words: [String], address: String?) -> Bool {
        let folded = words.map {
            $0.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil)
                .trimmingCharacters(in: .punctuationCharacters)
        }
        if words.joined().contains(where: { $0.isNumber || "&+,/|:;()[]!?".contains($0) }) { return false }
        if folded.contains(where: organisationWords.contains) { return false }
        guard let address, let at = address.firstIndex(of: "@") else {
            return words.count <= 4 && words.allSatisfy { word in
                word.first?.isUppercase == true && word.allSatisfy { $0.isLetter || "-'’.".contains($0) }
            }
        }
        let local = address[..<at].folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil)
        if roleAddresses.contains(local.filter(\.isLetter)) || noReply.contains(where: local.hasPrefix) { return false }
        let carried = local.filter(\.isLetter)
        let first = folded[0].filter(\.isLetter), last = folded[folded.count - 1].filter(\.isLetter)
        if first.count >= 2, carried.contains(first) { return true }
        guard last.count >= 2, let initial = first.first else { return false }
        return carried == "\(initial)\(last)" || carried == "\(last)\(initial)"
    }

    /// Words a person's name doesn't have and an organisation's often does, folded.
    private static let organisationWords: Set<String> = [
        "the", "co", "inc", "ltd", "llc", "gmbh", "corp", "plc", "company", "service", "services", "desk", "team",
        "support", "bank", "deals", "shop", "store", "club", "library", "school", "university", "college", "news",
        "newsletter", "weekly", "daily", "bot", "mailer", "notifications", "alerts", "updates", "billing", "help",
        "info", "group", "foundation", "agency", "studio", "cafe", "restaurant", "bakery", "hotel", "airlines",
        "labs", "press", "media", "council", "office", "clinic", "insurance", "payments", "delivery", "online",
    ]

    /// An address's part before the @, letters only, that a role writes from rather than a person.
    private static let roleAddresses: Set<String> = [
        "hello", "hi", "info", "news", "newsletter", "deals", "offers", "orders", "order", "support", "help", "team",
        "contact", "billing", "notifications", "notification", "alerts", "updates", "tracking", "mailer", "admin",
        "service", "sales", "marketing", "receipts", "accounts", "mail", "postmaster", "feedback", "store", "shop",
    ]

    /// How an address that takes no answer starts.
    private static let noReply = ["noreply", "no-reply", "donotreply", "do-not-reply"]

    /// The part of an address before the @ (`shortAddress`).
    static func short(_ address: String) -> String {
        String(address.split(separator: "@", maxSplits: 1, omittingEmptySubsequences: false).first ?? "")
    }

    /// How filing went (`filedWords`): how many, how many stayed, and that it waits for the server when it was queued.
    /// Read only queues nothing a person files: the server turns it down instead.
    static func filed(_ verb: String, count: Int, failed: Int, queued: Bool, to: String? = nil) -> String {
        let n = max(count, 1)
        let later = queued ? " · once the server is back" : ""
        let where_ = to.map { " to \($0)" } ?? ""
        if failed > 0 { return "\(verb) \(n) of \(n + failed)\(where_) — the rest stayed\(later)" }
        return (n > 1 ? "\(verb) \(n) messages\(where_)" : "\(verb)\(where_)") + later
    }

    /// What a list holds, as its head counts it: messages or conversations, results, newsletters or receipts.
    static func count(_ n: Int, more: Bool, noun: String) -> String {
        "\(n)\(more ? "+" : "") \(noun)\(n == 1 && !more ? "" : "s")"
    }

    /// What is said of a sender (`senderStats`): how much they wrote, how often you answered, since when.
    static func sender(count: Int, replied: Int, firstAgo: String?) -> String {
        [count == 1 ? "1 email" : "\(count) emails", "replied \(replied)×", firstAgo.map { "first \($0) ago" } ?? ""]
            .filter { !$0.isEmpty }.joined(separator: " · ")
    }

    /// A file's size in the fewest words (`fileSize`).
    static func size(_ bytes: Int) -> String {
        if bytes <= 0 { return "" }
        if bytes < 1024 { return "\(bytes) B" }
        if bytes < 1_048_576 { return "\(Int((Double(bytes) / 1024).rounded())) KB" }
        return String(format: "%.1f MB", Double(bytes) / 1_048_576)
    }

    // What a place says when there's nothing in it (`EMPTY`).
    static let matches = String(localized: "No matches.")
    static let list = String(localized: "Nothing here.")
    static let needsYou = String(localized: "Nothing needs you.")
    static let unread = String(localized: "Nothing unread.")
    static let feed = String(localized: "Your Feed is clear — no newsletters waiting.")
    static let receipts = String(localized: "No receipts or invoices yet.")
    /// A place emptied as you went through it (the page's digest's words): the paper trail once its last receipt
    /// is filed or deleted, which has had receipts, so none "yet" would be wrong.
    static let left = String(localized: "Nothing left here.")

    /// What an empty lane says: Feed is clear however it came to be; the paper trail has none yet, or none left.
    static func emptyLane(_ lane: Lane?, emptied: Bool) -> String {
        lane == .feed ? feed : emptied ? left : receipts
    }
    static let noFile = String(localized: "No files match.")
    static let noDocuments = String(localized: "No documents found in your synced mail.")
}

extension Place {
    /// The list's title as the page names it (`title` in useMailbox.ts), with the account's folders to name a folder by.
    func title(folders: [API.RailFolder]) -> String {
        switch self {
        case .allInboxes: return String(localized: "Inbox")
        case .folder(_, let folder):
            return folders.first { $0.name == folder }.map(Words.role) ?? folder
        case .virtual(let virtual, _): return virtual.title
        case .search(let search): return search.lane?.title ?? String(localized: "Search")
        case .documents: return String(localized: "Documents")
        case .desk(let screen): return screen.title
        }
    }
}
#endif
