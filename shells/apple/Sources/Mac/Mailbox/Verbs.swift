#if os(macOS)
import Foundation

/// What a person can do to mail from the list, the pane's bar, the menus and the keys, as the page's
/// features/mailbox/actions.ts does it: one message or conversation by its own route, several under one undo group as
/// many requests as the server's limit asks, each told in the page's words, a refusal with the server's reason, and an
/// Undo offered where the server says one is possible.
extension Mailbox {
    /// A filing verb's words: what is said when it is done, and what a refusal says it couldn't do (`WORDS`).
    static func words(_ verb: API.FilingVerb) -> (done: String, act: String, undo: String) {
        switch verb {
        case .archive: ("Archived", "archive", String(localized: "Archive"))
        case .trash: ("Trashed", "delete", String(localized: "Delete"))
        case .junk: ("Marked junk", "mark junk", String(localized: "Mark as Junk"))
        }
    }

    // MARK: Filing

    /// Archive, delete or mark junk what is chosen, else what is open.
    func file(_ verb: API.FilingVerb, rows given: [API.FolderRow]? = nil) {
        Task { await fileNow(verb, rows: given) }
    }

    func fileNow(_ verb: API.FilingVerb, rows given: [API.FolderRow]? = nil) async {
        let rows = if let given { given } else { await targets() }
        guard let calls, !rows.isEmpty else { return }
        let words = Self.words(verb)
        let after = nextOpen(leaving: rows)
        if rows.count > 1 {
            let group = Self.undoGroup()
            let said = await inChunks(rows) { chunk in
                try await calls.post(API.Post.BulkFile.self, API.BulkFileBody(
                    rows: chunk.map { API.BulkRow(folder: $0.folder, key: $0.key, uid: $0.uid, whole: $0.isConversation(grouped: self.grouped)) },
                    undoGroup: group, verb: verb))
            }
            finish(said, rows: rows, after: after, said: { "\(words.done) \($0)" }, undoName: words.undo)
            return
        }
        let row = rows[0]
        let body = API.MoveBody(folder: row.folder, key: row.key, uid: row.uid)
        do {
            let filed: Filing
            if row.isConversation(grouped: grouped) && verb != .junk {
                let answer = verb == .archive ? try await calls.post(API.Post.ThreadArchive.self, body)
                                              : try await calls.post(API.Post.ThreadTrash.self, body)
                filed = Filing(count: answer.count, failed: answer.failed, queued: answer.queued > 0, undoable: answer.undoable)
            } else {
                let answer: API.Filed = switch verb {
                case .archive: try await calls.post(API.Post.Archive.self, body)
                case .trash: try await calls.post(API.Post.Trash.self, body)
                case .junk: try await calls.post(API.Post.Junk.self, body)
                }
                filed = Filing(count: 1, failed: 0, queued: answer.status == "queued", undoable: answer.undoable)
            }
            let n = max(filed.count, 1)
            say(Words.filed(words.done, count: filed.count, failed: filed.failed, queued: filed.queued, readOnly: readOnly),
                undo: filed.undoable ? (n > 1 ? "\(words.done) \(n)" : words.done) : nil)
            if filed.undoable { offerUndo(words.undo) }
            moveOn(after)
        } catch {
            say("Couldn’t \(words.act)", detail: (error as? Problem)?.detail)
        }
        readAgain()
    }

    // MARK: Moving

    /// Ask where to move what is chosen, else what is open: only one account's mail moves at once (`askMove`).
    func askMove() {
        Task {
            let rows = await targets()
            guard let first = rows.first else { return }
            if Set(rows.map(\.account)).count > 1 {
                say(String(localized: "Those are in different accounts — mark one account’s mail to move it"))
                return
            }
            let from = Set(rows.map(\.folder))
            moving = MoveAsk(account: first.account, from: from.count == 1 ? first.folder : nil)
        }
    }

    /// The folders a move may go to: the account's own that take filing, but the one the rows are in.
    func destinations(_ ask: MoveAsk) -> [API.RailFolder] {
        folders(of: ask.account).filter { $0.takesFiling && $0.name != ask.from }
    }

    /// Move what is chosen, else what is open, to `folder` of its account (`moveRow`, `moveMarked`).
    func move(to folder: API.RailFolder) {
        moving = nil
        Task { await moveNow(to: folder.name, label: Words.role(folder)) }
    }

    /// Move `given`, else what is chosen or open, to `folder`.
    func moveNow(to folder: String, label: String, rows given: [API.FolderRow]? = nil) async {
        let chosen = if let given { given } else { await targets() }
        let rows = chosen.filter { $0.folder != folder }
        guard let calls, !rows.isEmpty else { return }
        let after = nextOpen(leaving: rows)
        if rows.count > 1 {
            let group = Self.undoGroup()
            let said = await inChunks(rows) { chunk in
                try await calls.post(API.Post.BulkMove.self, API.BulkMoveBody(
                    rows: chunk.map { API.BulkRow(folder: $0.folder, key: $0.key, uid: $0.uid, whole: $0.isConversation(grouped: self.grouped)) },
                    to: folder, undoGroup: group))
            }
            finish(said, rows: rows, after: after, said: { "Moved \($0) to \(label)" }, undoName: String(localized: "Move"),
                   undoSaid: { "Moved \($0)" }, couldnt: "Couldn’t move")
            return
        }
        let row = rows[0]
        let body = API.MoveToBody(folder: row.folder, key: row.key, to: folder, uid: row.uid)
        do {
            let filed: Filing
            if row.isConversation(grouped: grouped) {
                let answer = try await calls.post(API.Post.ThreadMove.self, body)
                filed = Filing(count: answer.count, failed: answer.failed, queued: answer.queued > 0, undoable: answer.undoable)
            } else {
                let answer = try await calls.post(API.Post.Move.self, body)
                filed = Filing(count: 1, failed: 0, queued: answer.status == "queued", undoable: answer.undoable)
            }
            let n = max(filed.count, 1)
            say(Words.filed("Moved", count: filed.count, failed: filed.failed, queued: filed.queued, readOnly: readOnly, to: label),
                undo: filed.undoable ? (n > 1 ? "Moved \(n)" : "Moved") : nil)
            if filed.undoable { offerUndo(String(localized: "Move")) }
            moveOn(after)
        } catch {
            let why = (error as? Problem)?.description
            say("Couldn’t move" + (why.map { ": \($0)" } ?? ""))
        }
        readAgain()
    }

    // MARK: Read, unread and starred

    /// Mark what is chosen read or unread, else toggle the open row's (`seenRow`, `bulk`).
    func setSeen(_ seen: Bool? = nil, rows given: [API.FolderRow]? = nil) {
        Task { await setSeenNow(seen, rows: given) }
    }

    func setSeenNow(_ wanted: Bool?, rows given: [API.FolderRow]? = nil) async {
        let rows = if let given { given } else { await targets() }
        guard let calls, let first = rows.first else { return }
        if rows.count > 1 {
            let seen = wanted ?? true
            await eachOne(rows) { row in
                _ = try await calls.post(API.Post.Seen.self, API.SeenBody(folder: row.folder, key: row.key, seen: seen, uid: row.uid))
            }
            return
        }
        let seen = wanted ?? !first.seen
        do {
            // A conversation reads as read when none is unread: marking it read takes each member that isn't.
            let copies = first.isConversation(grouped: grouped) && seen
                ? try await members(of: first).filter { !$0.seen }.map { API.Copy(folder: $0.folder, key: $0.key, uid: $0.uid) }
                : [first.copy]
            for copy in copies {
                _ = try await calls.post(API.Post.Seen.self, API.SeenBody(folder: copy.folder, key: copy.key, seen: seen, uid: copy.uid))
            }
            say(seen ? String(localized: "Marked read") : String(localized: "Marked unread"))
        } catch {
            say(String(localized: "Couldn’t update"), detail: (error as? Problem)?.detail)
        }
        readAgain()
    }

    /// Star what is chosen, else star or unstar the open row (`setStar`, `starRow`, `bulk`).
    func star(rows given: [API.FolderRow]? = nil) {
        Task { await starNow(rows: given) }
    }

    func starNow(rows given: [API.FolderRow]? = nil) async {
        let rows = if let given { given } else { await targets() }
        guard let calls, let first = rows.first else { return }
        if rows.count > 1 {
            await eachOne(rows) { row in
                _ = try await calls.post(API.Post.Flag.self, API.FlagBody(flag: .flagged, folder: row.folder, key: row.key, on: true, uid: row.uid))
            }
            return
        }
        let on = !first.flagged
        do {
            // A conversation reads as starred when any member is: unstarring it takes each one that is.
            let copies = first.isConversation(grouped: grouped) && !on
                ? try await members(of: first).filter(\.flagged).map { API.Copy(folder: $0.folder, key: $0.key, uid: $0.uid) }
                : [first.copy]
            for copy in copies {
                _ = try await calls.post(API.Post.Flag.self, API.FlagBody(flag: .flagged, folder: copy.folder, key: copy.key, on: on, uid: copy.uid))
            }
            say(on ? String(localized: "Starred") : String(localized: "Unstarred"))
        } catch {
            say(String(localized: "Couldn’t star"), detail: (error as? Problem)?.detail)
        }
        readAgain()
    }

    private func members(of row: API.FolderRow) async throws -> [API.ThreadMessage] {
        guard let calls, let thread = row.threadId else { return [] }
        return try await calls.get(API.Get.Thread.self, ["account": row.account, "thread_id": thread]).items
    }

    /// A mark on each row, one request each: how many took, and how many didn't.
    private func eachOne(_ rows: [API.FolderRow], _ send: (API.FolderRow) async throws -> Void) async {
        var failed = 0
        for row in rows {
            do { try await send(row) } catch { failed += 1 }
        }
        say(failed > 0 ? "Updated \(rows.count - failed), \(failed) failed" : "Updated \(rows.count)")
        readAgain()
    }

    // MARK: Undo

    /// Undo the last thing done (`undo`): through Edit ▸ Undo when the window holds it there, so the two stay one.
    func undo() {
        if let undoManager, undoManager.canUndo, undoManager.undoActionName == offered {
            undoManager.undo()
        } else {
            Task { await undoNow() }
        }
    }

    /// The server's undo: its last action reversed, said as the page says it.
    func undoNow() async {
        guard let calls else { return }
        dismissToast()
        do {
            let done = try await calls.post(API.Post.Undo.self, API.NoBody())
            if let failed = done.failed, failed > 0 {
                say("\(failed) couldn’t be restored — press ⌘Z to try again")
            } else {
                say("\(done.label.flatMap { $0.isEmpty ? nil : $0 } ?? "Action") undone")
            }
        } catch let problem as Problem where problem.isRefusal {
            say(problem.more["empty"] == .bool(true) ? String(localized: "Nothing to undo")
                                                     : problem.detail ?? String(localized: "Couldn’t undo"))
        } catch {
            say(String(localized: "Couldn’t undo"))
        }
        readAgain()
    }

    /// Put the server's undo on the window's Edit ▸ Undo, named for what it reverses.
    private func offerUndo(_ name: String) {
        guard let undoManager else { return }
        undoManager.registerUndo(withTarget: self) { mailbox in
            Task { await mailbox.undoNow() }
        }
        undoManager.setActionName(name)
        offered = name
    }

    // MARK: Helpers

    /// One id per bulk action, so one Undo reverses all of it.
    static func undoGroup() -> String {
        "ug-\(UUID().uuidString.lowercased())"
    }

    /// Rows in as many requests as the server's limit on one asks, each saying how its rows went. A request that fails
    /// counts all of its rows (`inChunks`).
    func inChunks(_ rows: [API.FolderRow], _ send: ([API.FolderRow]) async throws -> API.RowsFiled) async -> Chunked {
        var said = Chunked(total: rows.count)
        var at = 0
        while at < rows.count {
            let chunk = Array(rows[at..<min(rows.count, at + API.Limits.bulkRows)])
            do {
                let answer = try await send(chunk)
                said.failed += answer.rowsFailed
                said.partial = said.partial || answer.partial
                said.undoable = said.undoable || answer.undoable
            } catch {
                said.failed += chunk.count
            }
            at += API.Limits.bulkRows
        }
        return said
    }

    private func finish(_ chunked: Chunked, rows: [API.FolderRow], after: MailCopy??, said: (Int) -> String,
                        undoName: String, undoSaid: ((Int) -> String)? = nil, couldnt: String? = nil)
    {
        let n = chunked.total, done = n - chunked.failed
        let text = chunked.failed > 0 ? "\(said(done)), \(chunked.failed) failed"
            : chunked.partial ? "\(said(n)) — some messages stayed" : said(n)
        say(text, undo: chunked.undoable ? undoSaid.map { $0(done) } ?? said(done) : nil)
        if chunked.undoable { offerUndo(undoName) }
        if done > 0 { moveOn(after) }
        readAgain()
    }

    /// Where the reader goes once `rows` leave the list: on to the next row, or the one before, when what is open is
    /// among them (`afterLeaving`). Nil when what is open stays; .some(nil) when nothing is left to open.
    func nextOpen(leaving rows: [API.FolderRow]) -> MailCopy?? {
        guard let list = app?.list, let open = app?.selection.copy else { return nil }
        let leaving = Set(rows.map(MailCopy.init))
        guard leaving.contains(open), let at = list.position(of: open) else { return nil }
        var position = at + 1
        while let row = list.copy(at: position) {
            if !leaving.contains(MailCopy(row)) { return .some(MailCopy(row)) }
            position += 1
        }
        position = at - 1
        while position >= 0, let row = list.copy(at: position) {
            if !leaving.contains(MailCopy(row)) { return .some(MailCopy(row)) }
            position -= 1
        }
        return .some(nil)
    }

    private func moveOn(_ after: MailCopy??) {
        guard let after else { return }
        chosenRows = after.flatMap(row(of:)).map { [$0] } ?? []
        app?.open(after)
    }

    /// Read again what a verb changed: the list in place, and the sidebar's counts.
    func readAgain() {
        app?.list?.refresh()
        lane?.refresh()
        Task { await readCounts() }
    }
}

/// How one filing went, from whichever route took it.
struct Filing {
    var count: Int
    var failed: Int
    var queued: Bool
    var undoable: Bool
}

/// How filing in chunks went: how many rows, how many stayed, whether any request left some, and whether it can be
/// undone.
struct Chunked: Equatable {
    var total: Int
    var failed = 0
    var partial = false
    var undoable = false
}
#endif
