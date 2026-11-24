#if os(macOS)
import Foundation

/// What a person can do to mail from the list, the pane's bar, the menus and the keys, as the page's
/// features/mailbox/actions.ts does it: one message or conversation by its own route, several under one undo group as
/// many requests as the server's limit asks, each told in the page's words, a refusal with the server's reason, and an
/// Undo offered where the server says one is possible. Read only's refusal says so, and nothing more is asked once it
/// has said no.
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

    /// The pane's bar and the reader's own buttons: archive, delete or mark junk the message in the pane, never the
    /// rows marked beside it (`fileOpen`).
    func fileOpen(_ verb: API.FilingVerb) {
        Task { await fileNow(verb, rows: await paneTargets(standing: false)) }
    }

    /// The pane's bar's Restore and Move to Inbox: the message in the pane back in its account's Inbox.
    func restoreOpen() {
        Task { restore(await paneTargets()) }
    }

    /// Whether the server took them: what a message's own window closes on.
    @discardableResult
    func fileNow(_ verb: API.FilingVerb, rows given: [API.FolderRow]? = nil) async -> Bool {
        let rows = if let given { given } else { await targets(standing: false) }
        guard let calls else { return false }
        guard !rows.isEmpty else {
            saysNothingChosen()
            return false
        }
        let words = Self.words(verb)
        let after = nextOpen(leaving: rows)
        if rows.count > 1 {
            let group = Self.undoGroup()
            let said = await inChunks(rows) { chunk in
                try await calls.post(API.Post.BulkFile.self, API.BulkFileBody(
                    rows: chunk.map { API.BulkRow(folder: $0.folder, key: $0.key, uid: $0.uid, whole: $0.isConversation(grouped: self.grouped)) },
                    undoGroup: group, verb: verb))
            }
            return finish(said, rows: rows, after: after, said: { "\(words.done) \($0)" }, undoName: words.undo)
        }
        let row = rows[0]
        let body = API.MoveBody(folder: row.folder, key: row.key, uid: row.uid)
        var took = false
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
            say(Words.filed(words.done, count: filed.count, failed: filed.failed, queued: filed.queued),
                undo: filed.undoable ? (n > 1 ? "\(words.done) \(n)" : words.done) : nil)
            if filed.undoable { offerUndo(words.undo) }
            moveOn(after, leaving: rows)
            took = true
        } catch let refusal as Problem where refusal.isReadOnly {
            say(ReadOnlyWords.refused, refused: true)
        } catch {
            say("Couldn’t \(words.act)", detail: (error as? Problem)?.detail)
        }
        readAgain()
        return took
    }

    // MARK: Moving

    /// Ask where to move what is chosen, else what is open: only one account's mail moves at once (`askMove`).
    func askMove() {
        Task {
            let rows = await targets(standing: false)
            guard let first = rows.first else { return saysNothingChosen() }
            if Set(rows.map(\.account)).count > 1 {
                say(String(localized: "Those are in different accounts — mark one account’s mail to move it"), refused: true)
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
        let rows = moving?.rows
        moving = nil
        Task { await moveNow(to: folder.name, label: Words.role(folder), rows: rows) }
    }

    /// Whether the rows being dragged can drop on `folder` of `account`: a folder that takes filing, and rows all of
    /// that account, as a move stays in its account.
    func takesDrop(_ folder: API.RailFolder, of account: String) -> Bool {
        folder.takesFiling && !dragging.isEmpty && dragging.allSatisfy { $0.account == account }
    }

    /// The rows dragged from the list dropped on one of an account's folders: moved there, as Move To moves them.
    /// Whether the folder took them.
    func drop(on folder: API.RailFolder, of account: String) -> Bool {
        guard takesDrop(folder, of: account) else { return false }
        let rows = dragging
        dragging = []
        Task { await moveNow(to: folder.name, label: Words.role(folder), rows: rows) }
        return true
    }

    /// Move `given`, else what is chosen or open, to `folder`; whether the server took them.
    @discardableResult
    func moveNow(to folder: String, label: String, rows given: [API.FolderRow]? = nil) async -> Bool {
        let chosen = if let given { given } else { await targets(standing: false) }
        let rows = chosen.filter { $0.folder != folder }
        guard let calls else { return false }
        guard !chosen.isEmpty else {
            saysNothingChosen()
            return false
        }
        guard !rows.isEmpty else {
            say(String(localized: "Already in \(label)"), refused: true)
            return false
        }
        let after = nextOpen(leaving: rows)
        if rows.count > 1 {
            let group = Self.undoGroup()
            let said = await inChunks(rows) { chunk in
                try await calls.post(API.Post.BulkMove.self, API.BulkMoveBody(
                    rows: chunk.map { API.BulkRow(folder: $0.folder, key: $0.key, uid: $0.uid, whole: $0.isConversation(grouped: self.grouped)) },
                    to: folder, undoGroup: group))
            }
            return finish(said, rows: rows, after: after, said: { "Moved \($0) to \(label)" },
                          undoName: String(localized: "Move"), undoSaid: { "Moved \($0)" }, couldnt: "Couldn’t move")
        }
        let row = rows[0]
        let body = API.MoveToBody(folder: row.folder, key: row.key, to: folder, uid: row.uid)
        var took = false
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
            say(Words.filed("Moved", count: filed.count, failed: filed.failed, queued: filed.queued, to: label),
                undo: filed.undoable ? (n > 1 ? "Moved \(n)" : "Moved") : nil)
            if filed.undoable { offerUndo(String(localized: "Move")) }
            moveOn(after, leaving: rows)
            took = true
        } catch let refusal as Problem where refusal.isReadOnly {
            say(ReadOnlyWords.refused, refused: true)
        } catch {
            let why = (error as? Problem)?.description
            say("Couldn’t move" + (why.map { ": \($0)" } ?? ""))
        }
        readAgain()
        return took
    }

    // MARK: Read, unread and starred

    /// Mark what is chosen read or unread, else toggle the open row's (`seenRow`, `bulk`).
    func setSeen(_ seen: Bool? = nil, rows given: [API.FolderRow]? = nil) {
        Task { await setSeenNow(seen, rows: given) }
    }

    func setSeenNow(_ wanted: Bool?, rows given: [API.FolderRow]? = nil) async {
        let rows = if let given { given } else { await targets() }
        guard let calls else { return }
        guard let first = rows.first else { return saysNothingChosen() }
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
        } catch let refusal as Problem where refusal.isReadOnly {
            say(ReadOnlyWords.refused, refused: true)
        } catch {
            say(String(localized: "Couldn’t update"), detail: (error as? Problem)?.detail)
        }
        readAgain()
    }

    /// Star what is chosen, else star or unstar the open row (`setStar`, `starRow`, `bulk`); `on` when the words
    /// pressed said which.
    func star(on: Bool? = nil, rows given: [API.FolderRow]? = nil) {
        Task { await starNow(on: on, rows: given) }
    }

    func starNow(on wanted: Bool? = nil, rows given: [API.FolderRow]? = nil) async {
        let rows = if let given { given } else { await targets() }
        guard let calls else { return }
        guard let first = rows.first else { return saysNothingChosen() }
        if rows.count > 1 {
            await eachOne(rows) { row in
                _ = try await calls.post(API.Post.Flag.self, API.FlagBody(flag: .flagged, folder: row.folder, key: row.key, on: true, uid: row.uid))
            }
            return
        }
        let on = wanted ?? !first.flagged
        do {
            // A conversation reads as starred when any member is: unstarring it takes each one that is.
            let copies = first.isConversation(grouped: grouped) && !on
                ? try await members(of: first).filter(\.flagged).map { API.Copy(folder: $0.folder, key: $0.key, uid: $0.uid) }
                : [first.copy]
            for copy in copies {
                _ = try await calls.post(API.Post.Flag.self, API.FlagBody(flag: .flagged, folder: copy.folder, key: copy.key, on: on, uid: copy.uid))
            }
            say(on ? String(localized: "Starred") : String(localized: "Unstarred"))
        } catch let refusal as Problem where refusal.isReadOnly {
            say(ReadOnlyWords.refused, refused: true)
        } catch {
            say(String(localized: "Couldn’t star"), detail: (error as? Problem)?.detail)
        }
        readAgain()
    }

    private func members(of row: API.FolderRow) async throws -> [API.ThreadMessage] {
        guard let calls, let thread = row.threadId else { return [] }
        return try await calls.get(API.Get.Thread.self, ["account": row.account, "thread_id": thread]).items
    }

    /// A mark on each row, one request each: how many took, and how many didn't. Read only turns every one down, so
    /// its no ends the rest unasked.
    private func eachOne(_ rows: [API.FolderRow], _ send: (API.FolderRow) async throws -> Void) async {
        var failed = 0
        for row in rows {
            do {
                try await send(row)
            } catch let refusal as Problem where refusal.isReadOnly {
                say(ReadOnlyWords.refused, refused: true)
                readAgain()
                return
            } catch {
                failed += 1
            }
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

    /// The server's undo: its last action reversed, said as the page says it. What it brings back that the pane had
    /// open before the action moved it on opens again (`reopen`).
    func undoNow() async {
        guard let calls else { return }
        dismissToast()
        let back = reopens, window = reopensWindow
        reopens = nil
        reopensWindow = nil
        var undone = false
        let answered = undoing?()
        var answer: API.Undone?
        defer { answered?(answer) }
        do {
            let done = try await calls.post(API.Post.Undo.self, API.NoBody())
            answer = done
            if let failed = done.failed, failed > 0 {
                say("\(failed) couldn’t be restored — press ⌘Z to try again")
                undone = (done.reversed ?? 0) > 0
            } else {
                say("\(done.label.flatMap { $0.isEmpty ? nil : $0 } ?? "Action") undone")
                undone = true
            }
        } catch let problem as Problem where problem.isReadOnly {
            say(ReadOnlyWords.refused, refused: true)
            // Taking it back would reach a server: it stays to be undone, and so does what it reopens.
            reopens = back
            reopensWindow = window
        } catch let problem as Problem where problem.isRefusal {
            say(problem.more["empty"] == .bool(true) ? String(localized: "Nothing to undo")
                                                     : problem.detail ?? String(localized: "Couldn’t undo"),
                refused: true)
        } catch {
            say(String(localized: "Couldn’t undo"))
        }
        readAgain()
        if undone, let back { reopen(back) }
        if undone, let window { reopenWindow(window) }
    }

    /// Open again, in a window of its own, the message whose window closed as the undone verb sent it away: as it is
    /// back, found by its key in the folder it left, since a move's Undo gives the copy a new uid.
    private func reopenWindow(_ copy: MailCopy) {
        guard let opens = opensWindow else { return }
        Task { [weak self] in
            guard let calls = self?.calls else { return }
            var row = await Lists.row(calls, account: copy.account, folder: copy.folder, uid: copy.uid, key: copy.key)
            if row == nil {
                row = await Lists.row(calls, account: copy.account, folder: copy.folder, uid: nil, key: copy.key)
            }
            opens(row.map(MailCopy.init) ?? copy)
        }
    }

    /// Open again the message an undone verb moved the pane on from, as it is back: once the list or the lane has been
    /// read again, chosen in its row there as a click chooses it, else opened from outside them, as `open` opens one.
    /// A move's Undo gives the copy a new uid, so it is found by its key in the folder it left. A message opened or a
    /// row chosen since the Undo stays.
    private func reopen(_ copy: MailCopy) {
        guard let app else { return }
        let (turn, was) = (app.openings, app.selection.copy)
        let still: @MainActor () -> Bool = { [weak app] in app?.openings == turn && app?.selection.copy == was }
        let go: @MainActor () -> Void = { [weak self] in
            guard let self, let app = self.app, still() else { return }
            if let row = self.returned(copy) {
                // Rows still marked keep their marks: it opens beside them, as the pane's verb moved on beside them.
                if app.place == app.listed {
                    if self.marking { self.openBeside(row) } else { self.chose([row]) }
                } else {
                    self.open(MailCopy(row))
                }
                return
            }
            Task { [weak self] in
                guard let calls = self?.calls else { return }
                var row = await Lists.row(calls, account: copy.account, folder: copy.folder, uid: copy.uid,
                                          key: copy.key)
                if row == nil {
                    row = await Lists.row(calls, account: copy.account, folder: copy.folder, uid: nil, key: copy.key)
                }
                guard still() else { return }
                self?.open(row.map(MailCopy.init) ?? copy)
            }
        }
        if let list = app.list {
            let before = list.onceRead
            list.onceRead = { before?(); go() }
        } else if let lane {
            let before = lane.onceLoaded
            lane.onceLoaded = { before?(); go() }
        } else {
            go()
        }
    }

    /// The row the window holds for `copy` back from an Undo: the same copy, else the copy its key has now in the
    /// folder it left.
    private func returned(_ copy: MailCopy) -> API.FolderRow? {
        if let held = heldRow(of: copy) { return held }
        let back: (API.FolderRow) -> Bool = {
            $0.account == copy.account && $0.folder == copy.folder && $0.key == copy.key
        }
        if let list = app?.list, let at = list.position(where: back) { return list.copy(at: at) }
        return lane?.hits.lazy.map(API.FolderRow.init).first(where: back)
    }

    /// Put the server's undo on the window's Edit ▸ Undo, named for what it reverses.
    /// Put what was just done on Edit ▸ Undo, as "Undo `name`": ⌘Z there and the toast's Undo (`undo`) take it back
    /// once between them.
    func offerUndo(_ name: String) {
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
    /// counts all of its rows (`inChunks`). Read only turns every request down, so its no counts the rest as failed
    /// unasked.
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
            } catch let refusal as Problem where refusal.isReadOnly {
                said.failed += rows.count - at
                said.readOnly = true
                break
            } catch {
                said.failed += chunk.count
            }
            at += API.Limits.bulkRows
        }
        return said
    }

    /// Whether any of them went.
    private func finish(_ chunked: Chunked, rows: [API.FolderRow], after: MailCopy??, said: (Int) -> String,
                        undoName: String, undoSaid: ((Int) -> String)? = nil, couldnt: String? = nil) -> Bool
    {
        let n = chunked.total, done = n - chunked.failed
        if chunked.readOnly && done == 0 {
            say(ReadOnlyWords.refused, refused: true)
            readAgain()
            return false
        }
        let text = chunked.failed > 0 ? "\(said(done)), \(chunked.failed) failed"
            : chunked.partial ? "\(said(n)) — some messages stayed" : said(n)
        say(text, undo: chunked.undoable ? undoSaid.map { $0(done) } ?? said(done) : nil)
        if chunked.undoable { offerUndo(undoName) }
        if done > 0 { moveOn(after, leaving: rows) }
        readAgain()
        return done > 0
    }

    /// Where the reader goes once `rows` leave, when what is open is among them (`afterLeaving`): on to the next row,
    /// or the one before, in the list the window is at or the lane; and a message opened from outside them (the brief,
    /// an answer, the gate, the Document Hub) closes, so the place it was opened from shows again. Nil when what is
    /// open stays; .some(nil) when nothing is left to open.
    func nextOpen(leaving rows: [API.FolderRow]) -> MailCopy?? {
        nextOpen(leaving: Set(rows.map(MailCopy.init)))
    }

    func nextOpen(leaving: Set<MailCopy>) -> MailCopy?? {
        guard let open = app?.selection.copy else { return nil }
        guard leaving.contains(open) else { return nil }
        if let app, app.place == app.listed, let list = app.list, let at = list.position(of: open) {
            return Self.next(after: at, leaving: leaving, list.copy(at:))
        }
        if let hits = lane?.hits.map(API.FolderRow.init), let at = hits.firstIndex(where: { MailCopy($0) == open }) {
            return Self.next(after: at, leaving: leaving) { hits.indices.contains($0) ? hits[$0] : nil }
        }
        return .some(nil)
    }

    /// The first row after `at` that isn't leaving, else the first before it, else none.
    private static func next(after at: Int, leaving: Set<MailCopy>,
                             _ row: (Int) -> API.FolderRow?) -> MailCopy?? {
        var position = at + 1
        while let next = row(position) {
            if !leaving.contains(MailCopy(next)) { return .some(MailCopy(next)) }
            position += 1
        }
        position = at - 1
        while position >= 0, let before = row(position) {
            if !leaving.contains(MailCopy(before)) { return .some(MailCopy(before)) }
            position -= 1
        }
        return .some(nil)
    }

    /// The subject of the one message another area's verb acted on, when it isn't the message on screen (a row's
    /// clock, the brief's card), so its words can say which. Nil for the message open, for several, or for one the
    /// window doesn't hold.
    func subjectAway(_ copies: [MailCopy]) -> String? {
        guard copies.count == 1, let copy = copies.first, copy != app?.selection.copy,
              let subject = heldRow(of: copy)?.subject, !subject.isEmpty else { return nil }
        return subject
    }

    /// The conversation another area's verb acts on, when `copies` is one row that stands for a whole conversation
    /// here, as filing reads one: a snooze then names its thread, as the page's names the thread it has open or a
    /// conversation's row, so none of its messages stays behind as a row of its own. Nil for a message, or several.
    func conversation(of copies: [MailCopy]) -> Conversation? {
        guard copies.count == 1, let copy = copies.first, let row = heldRow(of: copy),
              row.isConversation(grouped: grouped), let thread = row.threadId else { return nil }
        return Conversation(account: row.account, thread: thread)
    }

    /// Where the reader goes once `copies` leave the place shown by another area's verb (a snooze, Reply Later's Done):
    /// worked out now, while the list and the lane still hold them, and gone to once the server has taken them, as
    /// File goes.
    func leaving(_ copies: [MailCopy]) -> @MainActor () -> Void {
        let after = nextOpen(leaving: Set(copies))
        return { [weak self] in self?.moveOn(after, leaving: copies) }
    }

    /// Open what comes next, chosen as a click on its row chooses it, or close the message. Where what moved it on
    /// can be undone, its Undo opens the message that left again (`undoNow`). With the message open staying, the rows
    /// that left lose their marks.
    private func moveOn(_ after: MailCopy??, leaving rows: [API.FolderRow]) {
        moveOn(after, leaving: rows.map(MailCopy.init))
    }

    private func moveOn(_ after: MailCopy??, leaving: [MailCopy]) {
        sentAway(leaving)
        guard let after else { return unmark(Set(leaving)) }
        if reopenable, let left = app?.selection.copy { reopens = left }
        // The message in the pane left on its own (the pane's bar) while other rows stay marked: they keep their
        // marks, and what comes next opens beside them.
        let gone = Set(leaving)
        let staying = marking ? chosenRows.filter { !gone.contains(MailCopy($0)) } : []
        if !staying.isEmpty {
            chosenRows = staying
            markedAlone = true
            if let next = after.flatMap(heldRow(of:)) { openBeside(next) } else { app?.open(nil) }
            return
        }
        chose(after.flatMap(heldRow(of:)).map { [$0] } ?? [])
    }

    /// Rows that left the list keep no mark: the others stay marked, and with none left the row open is chosen alone.
    private func unmark(_ leaving: Set<MailCopy>) {
        guard marking, !leaving.isEmpty else { return }
        let left = chosenRows.filter { !leaving.contains(MailCopy($0)) }
        guard left.count != chosenRows.count else { return }
        if left.isEmpty, unheld.isEmpty {
            clearMarks()
        } else {
            chosenRows = left
            markedAlone = true
        }
    }

    /// A verb with nothing chosen or open: said, so a key or a command never does nothing without a word.
    func saysNothingChosen() {
        say(String(localized: "Choose a message first"), refused: true)
    }

    /// Read again what a verb changed: the list in place, the lane, the message open from outside them, the sidebar's
    /// counts, and the brief's tallies (the rail's Needs you and the Inbox's brief row), which count the same mail as
    /// the list. The server has taken the change by now, so the tallies leave out all it filed: a conversation's every
    /// message, where only the row's own left them at once (`sentAway`), and with no event to follow when its moves
    /// reached the server inside the request.
    func readAgain() {
        app?.list?.refresh()
        lane?.refresh()
        rereadOutside()
        rereadsTallies?()
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

/// How filing in chunks went: how many rows, how many stayed, whether any request left some, whether it can be
/// undone, and whether read only turned it down.
struct Chunked: Equatable {
    var total: Int
    var failed = 0
    var partial = false
    var undoable = false
    var readOnly = false
}
#endif
