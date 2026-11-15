#if os(macOS)
import SwiftUI

/// The reader in the window: the pane's bar (Mac/Meadow: triage, reply, reply all, forward or edit a draft, the
/// reading settings and focus), the message chosen or its whole conversation, or with none the brief beside the
/// Inbox; and what a send says once its composer has gone.
struct ReaderPane: View {
    let app: MailApp
    @State private var conversation: ConversationModel?
    @Environment(\.openWindow) private var openWindow

    private var composing: Composing { .shared }
    private var prefs: ReadingPrefs { ReadingPrefs(composing.preferences) }

    /// The chosen copy's row in the list, when the list holds it.
    private var row: API.FolderRow? {
        guard let list = app.list, let at = list.chosenPosition else { return nil }
        return list.copy(at: at)
    }

    /// Writing in the mailbox: a sender's card writes to them, the list's r, a, f and c reply, reply to all, forward
    /// and write, and the palette lists them, as the page's commands do.
    private func joinMailbox() {
        let pane = self
        app.mailbox.join("compose") { mailbox in
            mailbox.composeTo = { [weak app] address in
                Composing.shared.newMessage(to: [address], current: Self.current(in: app?.place))
            }
            let writing: [(id: String, label: String, key: Character, run: @MainActor (MailCopy) -> Void)] = [
                ("reply", String(localized: "Reply"), "r", { to in Task { await Composing.shared.answer(to) } }),
                ("reply-all", String(localized: "Reply all"), "a", { to in Task { await Composing.shared.reply(to, all: true) } }),
                ("forward", String(localized: "Forward"), "f", { to in Task { await Composing.shared.forward(to) } }),
            ]
            for verb in writing {
                mailbox.keys[verb.key] = { if let to = pane.replyTo { verb.run(to) } }
            }
            mailbox.keys["c"] = { Composing.shared.newMessage(current: pane.current) }
            mailbox.more.append { _ in
                var out = pane.replyTo.map { to in
                    writing.map { verb in
                        MailCommand(id: verb.id, group: .actions, label: verb.label, hint: String(verb.key)) { verb.run(to) }
                    }
                } ?? []
                out.append(MailCommand(id: "compose", group: .commands, label: String(localized: "Compose new message"),
                                       hint: "c") { Composing.shared.newMessage(current: pane.current) })
                return out
            }
        }
    }

    /// The account the window is in, which a new message goes from when it can.
    private var current: String? { Self.current(in: app.place) }

    static func current(in place: Place?) -> String? {
        if case .folder(let account, _)? = place { return account }
        return nil
    }

    /// The conversation the chosen row opens whole, as the page decides: conversations on, more than one message,
    /// and not in a drafts folder.
    private var thread: Thread? {
        guard prefs.conversations, let row, (row.count ?? 0) > 1, let id = row.threadId, !id.isEmpty,
              row.folder.range(of: "draft", options: .caseInsensitive) == nil else { return nil }
        return Thread(account: row.account, id: id)
    }

    private struct Thread: Hashable {
        let account: String
        let id: String
    }

    private var openCopy: MailCopy? {
        if case .open(let copy, _) = app.reader.stage { return copy }
        return nil
    }

    private var openMessage: API.MessageOpened? {
        if case .open(_, let opened) = app.reader.stage { return opened }
        return nil
    }

    /// What a reply answers: the message, or the conversation's latest that isn't yours.
    private var replyTo: MailCopy? {
        if thread != nil, let conversation { return conversation.replyTo }
        return openMessage?.isDraft == true ? nil : openCopy
    }

    var body: some View {
        let draft = openMessage?.isDraft == true ? openCopy : nil
        VStack(spacing: 0) {
            MeadowPaneBar(app: app, mailbox: app.mailbox, message: app.reader.stage != .empty,
                          writing: PaneWriting(replyTo: replyTo, draft: draft))
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .focusedSceneValue(\.mailWriting, MailWriting(replyTo: replyTo, draft: draft, current: current))
        .task {
            composing.openWindow = { id in openWindow(id: ComposeWindow.scene, value: id) }
            composing.mailChanged = { [weak app] in
                app?.list?.refresh()
                Task { await app?.mailbox.readCounts() }
            }
            if let core = app.core { composing.attach(core) }
            joinMailbox()
        }
        .task(id: thread) {
            guard let thread, let core = app.core else {
                conversation = nil
                return
            }
            let model = ConversationModel(routes: core, account: thread.account, id: thread.id)
            conversation = model
            await model.load()
        }
        .task(id: openCopy) { await dwell() }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            Task { await composing.readPreferences() }
        }
    }

    @ViewBuilder private var content: some View {
        switch app.reader.stage {
        case .empty:
            if case .desk(let screen)? = app.place, screen.besideTheInbox {
                DeskScreen(place: screen)
            } else {
                ContentUnavailableView("No message selected.", systemImage: "envelope")
            }
        case .loading:
            Fetching()
        case .failed(_, let why):
            ContentUnavailableView("This message couldn't be opened", systemImage: "exclamationmark.triangle",
                                   description: Text(why))
        case .open(let copy, let opened):
            if let core = app.core {
                if thread != nil, let conversation {
                    ConversationReader(core: core, model: conversation, row: row, prefs: prefs, mine: composing.mine)
                } else {
                    MessageReader(core: core, copy: copy, opened: opened, row: row, prefs: prefs, mine: composing.mine)
                        .id(copy)
                }
            }
        }
    }

    /// Opened unread, a message is marked read after a moment on it; and a reply written ahead is looked for.
    private func dwell() async {
        guard let copy = openCopy else {
            composing.viewing(nil, wanted: false, skip: true)
            return
        }
        composing.viewing(replyTo ?? copy, wanted: thread == nil && (row?.replyWorthy ?? false),
                          skip: openMessage?.isDraft == true)
        guard thread == nil, row?.seen == false, let core = app.core else { return }
        try? await Task.sleep(for: .milliseconds(1200))
        guard !Task.isCancelled else { return }
        _ = try? await core.post(API.Post.Seen.self, API.SeenBody(folder: copy.folder, key: copy.key, seen: true, uid: copy.uid))
    }
}
#endif
