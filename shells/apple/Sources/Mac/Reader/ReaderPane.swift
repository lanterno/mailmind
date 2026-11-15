#if os(macOS)
import SwiftUI

/// The reader in the window: the message chosen, or its whole conversation; the toolbar's writing (new message,
/// reply, reply all, forward, edit a draft), the reading settings and the outbox tray; and what a send says once its
/// composer has gone.
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

    /// The account the window is in, which a new message goes from when it can.
    private var current: String? {
        if case .folder(let account, _)? = app.place { return account }
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
        content
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .overlay(alignment: .bottom) { SendFloatView() }
            .toolbar { toolbar }
            .focusedSceneValue(\.mailWriting, MailWriting(replyTo: replyTo, draft: openMessage?.isDraft == true ? openCopy : nil,
                                                          current: current))
            .task {
                composing.openWindow = { id in openWindow(id: ComposeWindow.scene, value: id) }
                composing.mailChanged = { [weak app] in
                    app?.list?.refresh()
                    Task { await app?.readSidebar() }
                }
                if let core = app.core { composing.attach(core) }
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
            ContentUnavailableView("No message selected.", systemImage: "envelope")
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

    @ToolbarContentBuilder private var toolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            Button {
                composing.newMessage(current: current)
            } label: {
                Label("New Message", systemImage: "square.and.pencil")
            }
            .help("New message (⌘N)")
            if let draft = openMessage?.isDraft == true ? openCopy : nil {
                Button {
                    Task { await composing.editDraft(draft) }
                } label: {
                    Label("Edit Draft", systemImage: "pencil")
                }
                .help("Edit draft")
            } else {
                ReplyButtons(replyTo: replyTo)
            }
            ReadingSettingsButton()
            OutboxButton()
        }
    }
}

/// Reply says which of three it is: a draft written ahead, one being written, or just Reply; then Reply All and
/// Forward.
struct ReplyButtons: View {
    let replyTo: MailCopy?

    var body: some View {
        let composing = Composing.shared
        let chip = replyTo.flatMap { composing.chips[$0.key] }
        Group {
            switch chip {
            case .ready?:
                Button {
                    if let replyTo { Task { await composing.answer(replyTo) } }
                } label: {
                    Label("Draft Ready", systemImage: "wand.and.sparkles")
                }
                .help("AI pre-drafted a reply — opens in the composer for review; nothing sends until you do (⌘R)")
            case .drafting?:
                Button {
                    if let replyTo { Task { await composing.replyNow(replyTo) } }
                } label: {
                    Label("Drafting…", systemImage: "wand.and.sparkles")
                }
                .help("AI is drafting a reply in the background — click for a plain reply now")
            case nil:
                Button {
                    if let replyTo { Task { await composing.answer(replyTo) } }
                } label: {
                    Label("Reply", systemImage: "arrowshape.turn.up.left")
                }
                .help("Reply (⌘R)")
            }
            Button {
                if let replyTo { Task { await composing.reply(replyTo, all: true) } }
            } label: {
                Label("Reply All", systemImage: "arrowshape.turn.up.left.2")
            }
            .help("Reply all (⇧⌘R)")
            Button {
                if let replyTo { Task { await composing.forward(replyTo) } }
            } label: {
                Label("Forward", systemImage: "arrowshape.turn.up.right")
            }
            .help("Forward (⇧⌘F)")
        }
        .disabled(replyTo == nil)
    }
}
#endif
