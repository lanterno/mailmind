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
