#if os(macOS)
import Foundation

/// Every list the mailbox shows reads as the window's list rows (`API.FolderRow`), as the page reads every list as its
/// `Row` (`fromFolder`, `fromSearch`): a virtual folder's rows and a search's carry the same facts under other types.
extension API.FolderRow {
    /// A search's hit. It has no time to sort by and no summary; the server's `when` says when.
    init(_ hit: API.SearchHit) {
        self.init(account: hit.account, action: (hit.action ?? "").trimmingCharacters(in: .whitespaces),
                  bucket: hit.bucket, commitmentWorthy: hit.commitmentWorthy, flagged: hit.flagged, folder: hit.folder,
                  fromAddr: hit.fromAddr, fromName: hit.fromName, hasAttach: hit.hasAttach, importance: hit.importance,
                  isJunk: hit.isJunk, key: hit.key, needsYou: hit.needsYou, replyWorthy: hit.replyWorthy,
                  safeToFile: hit.safeToFile, seen: hit.seen, snippet: hit.snippet, subject: hit.subject, summary: "",
                  threadId: hit.threadId, ts: nil, uid: hit.uid, when: hit.when)
    }

    /// A snoozed message: `when` says when it wakes.
    init(_ row: API.SnoozedRow) {
        self.init(account: row.account, action: "", bucket: row.bucket, commitmentWorthy: row.commitmentWorthy,
                  flagged: row.flagged, folder: row.folder, fromAddr: row.fromAddr, fromName: row.fromName,
                  hasAttach: row.hasAttach, importance: row.importance, isJunk: row.isJunk, key: row.key,
                  needsYou: row.needsYou, replyWorthy: row.replyWorthy, safeToFile: row.safeToFile, seen: row.seen,
                  snippet: row.snippet, subject: row.subject, summary: "", threadId: row.threadId, ts: nil, uid: row.uid,
                  when: row.when)
    }

    /// A muted conversation's latest message.
    init(_ row: API.MutedRow) {
        self.init(account: row.account, action: "", bucket: row.bucket, commitmentWorthy: false, count: row.count,
                  flagged: row.flagged, folder: row.folder, fromAddr: row.fromAddr, fromName: row.fromName,
                  hasAttach: row.hasAttach, isJunk: row.isJunk, key: row.key, needsYou: false, replyWorthy: false,
                  safeToFile: false, seen: row.seen, snippet: row.snippet, subject: row.subject, summary: "",
                  threadId: row.threadId, ts: row.ts, uid: row.uid, when: row.when)
    }

    /// A message waiting on someone else: its label says how long (`waiting_label || when`).
    init(_ row: API.WaitingRow) {
        self.init(account: row.account, action: "", bucket: row.bucket, commitmentWorthy: false, count: row.count,
                  flagged: row.flagged, folder: row.folder, fromAddr: row.fromAddr, fromName: row.fromName,
                  hasAttach: row.hasAttach, isJunk: row.isJunk, key: row.key, needsYou: false, replyWorthy: false,
                  safeToFile: false, seen: row.seen, snippet: row.snippet, subject: row.subject, summary: "",
                  threadId: row.threadId, ts: row.ts, uid: row.uid,
                  when: row.waitingLabel.isEmpty ? row.when : row.waitingLabel)
    }

    /// A message kept to reply to later.
    init(_ row: API.ReplyLaterRow) {
        self.init(account: row.account, action: "", bucket: row.bucket, commitmentWorthy: false, count: row.count,
                  flagged: row.flagged, folder: row.folder, fromAddr: row.fromAddr, fromName: row.fromName,
                  hasAttach: row.hasAttach, isJunk: row.isJunk, key: row.key, needsYou: false, replyWorthy: false,
                  safeToFile: false, seen: row.seen, snippet: row.snippet, subject: row.subject, summary: "",
                  threadId: row.threadId, ts: row.ts, uid: row.uid,
                  when: row.waitingLabel.isEmpty ? row.when : row.waitingLabel)
    }

    /// The copy the server names the row by, as an action's body takes it.
    var copy: API.Copy { API.Copy(folder: folder, key: key, uid: uid) }

    /// Whether the row stands for a whole conversation: it files whole (`isConversation`). A draft never does.
    func isConversation(grouped: Bool) -> Bool {
        grouped && (count ?? 1) > 1 && !(threadId ?? "").isEmpty && folder.range(of: "draft", options: .caseInsensitive) == nil
    }

    /// The number the row shows beside its sender: how many its conversation holds, where it stands for more than one.
    var conversationCount: Int? {
        let count = count ?? 1, whole = threadCount ?? count
        return count > 1 && whole > 1 ? whole : nil
    }

    /// The junk tag's word: the bucket's first part, or "junk".
    var junkTag: String? {
        guard isJunk else { return nil }
        let bucket = bucket.flatMap { $0.isEmpty ? nil : $0 } ?? "junk"
        return String(bucket.split(separator: "/").first ?? "junk")
    }

    /// Who sent it, as the row names them: the name, else the address.
    var sender: String {
        [fromName, fromAddr].compactMap { $0 }.first { !$0.isEmpty } ?? ""
    }

    /// The line under the subject: the AI's summary when there is one, else the snippet.
    var gist: String { summary.isEmpty ? snippet : summary }
}

/// A sender's monogram as the page draws it (lib/text.ts): two initials and a hue the address always gets.
enum Monogram {
    /// A hue from the whole wheel (`avatarHue`), in degrees.
    static func hue(_ text: String?) -> Int {
        var hue = 0
        for unit in (text ?? "").utf16 { hue = (hue * 31 + Int(unit)) % 360 }
        return hue
    }

    /// Two initials (`initialsOf`): the capitalised words of the name, or its words, or the address's first letter.
    static func initials(name: String?, address: String?) -> String {
        let name = name ?? ""
        // A Character holds its combining marks, so a run of letters is a word.
        let words = name.contains("@") ? [] : name.split(whereSeparator: { !$0.isLetter }).map(String.init)
        let capitals = words.filter { $0.first.map { String($0) != String($0).lowercased() } ?? false }
        let chosen = capitals.count >= 2 ? capitals : words
        if !chosen.isEmpty {
            return chosen.prefix(2).compactMap { $0.first.map(String.init) }.joined().uppercased()
        }
        let source = address.flatMap { $0.isEmpty ? nil : $0 } ?? name
        return source.first { $0.isLetter || $0.isNumber }.map { String($0).uppercased() } ?? "?"
    }
}

extension MailCopy {
    /// A copy named outside a list: a file in the Document Hub, a sender's recent mail.
    init(key: Int, folder: String, uid: Int, account: String) {
        self.init(API.FolderRow(account: account, action: "", commitmentWorthy: false, flagged: false, folder: folder,
                                hasAttach: false, isJunk: false, key: key, needsYou: false, replyWorthy: false,
                                safeToFile: false, seen: true, snippet: "", subject: "", summary: "", uid: uid, when: ""))
    }
}
#endif
