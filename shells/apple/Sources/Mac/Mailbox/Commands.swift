import SwiftUI

#if os(macOS)
extension FocusedValues {
    /// The mailbox of the window in front, which the menu bar's commands act on.
    @Entry var mailbox: Mailbox?
}

/// The page's commands (app/commands.ts) in the menu bar, with a Mac's keys: the Message menu acts on what is chosen
/// or open, the Mailbox menu goes places and searches, and View holds how the list is shown (`MailboxViewItems`, in
/// Meadow's View group). Each runs as its key, its button in the pane's bar and its palette entry do. Edit ▸ Undo
/// undoes what the mailbox did last.
struct MailboxCommands: Commands {
    @FocusedValue(\.mailbox) private var mailbox
    @FocusedValue(\.messageWindow) private var window

    var body: some Commands {
        CommandMenu("Message") {
            ComposeMessageItems()
            Divider()
            let verbs = MessageMenuVerbs(window: window, mailbox: mailbox)
            Button("Archive") { verbs.file(.archive) }
                .keyboardShortcut("a", modifiers: [.control, .command])
                .disabled(!verbs.acting)
            // ⌘⌫ deletes text while a field is being typed in, so it deletes mail only from the list or the message.
            Button("Delete") { verbs.file(.trash) }
                .keyboardShortcut(.delete, modifiers: .command)
                .disabled(!verbs.acting || verbs.typing)
            Button("Mark as Junk") { verbs.file(.junk) }
                .keyboardShortcut("j", modifiers: [.shift, .command])
                .disabled(!verbs.acting)
            Button("Move To…") { verbs.askMove() }
                .keyboardShortcut("m", modifiers: [.shift, .command])
                .disabled(!verbs.acting)
            Divider()
            // Each says what it will do to the rows as they stand now, and does that.
            let marks = Marks(verbs.shown)
            Button(marks.unread ? "Mark as Read" : "Mark as Unread") { verbs.setSeen(marks.unread) }
                .keyboardShortcut("u", modifiers: [.shift, .command])
                .disabled(!verbs.acting)
            Button(marks.unstars ? "Unstar" : "Star") { verbs.star(!marks.unstars) }
                .keyboardShortcut("l", modifiers: [.shift, .command])
                .disabled(!verbs.acting)
            Divider()
            DeskMessageItems()
            Divider()
            Button("Next Unread") { mailbox?.moveUnread(by: 1) }
                .keyboardShortcut(.downArrow, modifiers: [.option, .command])
                .disabled(mailbox == nil)
            Button("Previous Unread") { mailbox?.moveUnread(by: -1) }
                .keyboardShortcut(.upArrow, modifiers: [.option, .command])
                .disabled(mailbox == nil)
            Divider()
            let shown = mailbox?.shownRows ?? []
            let sender = shown.count == 1 ? shown[0].fromAddr : nil
            Button("About This Sender") { if let sender { mailbox?.contact = sender } }
                .disabled(sender?.isEmpty ?? true)
            Button("Search Mail from This Sender") {
                if let sender { mailbox?.app?.place = .search(Search(q: "from:\(sender)")) }
            }
            .disabled(sender?.isEmpty ?? true)
        }
        CommandMenu("Mailbox") {
            Group {
                Button("All Inboxes") { mailbox?.app?.place = .allInboxes }
                    .keyboardShortcut("0", modifiers: .command)
                ForEach(Array(Self.roles.enumerated()), id: \.offset) { index, role in
                    Button(role.title) { mailbox?.go(role: role.role) }
                        .keyboardShortcut(KeyEquivalent(Character(String(index + 1))), modifiers: .command)
                }
                Divider()
                ForEach([VirtualFolder.snoozed, .waiting, .replyLater], id: \.self) { virtual in
                    Button(virtual.title) { mailbox?.go(virtual) }
                }
                Button("Documents") { mailbox?.app?.place = .documents }
                Divider()
                Button("Search") { mailbox?.searchFocus = true }
                    .keyboardShortcut("f", modifiers: .command)
                Button("Save Search as View…") {
                    if let q = mailbox?.app?.place?.search?.q { mailbox?.savingView = q }
                }
                .disabled(mailbox?.app?.place?.search == nil || mailbox?.app?.place?.search?.lane != nil
                    || mailbox?.onSavedView == true)
                Button("Command Palette…") { mailbox?.paletteOpen = true }
                    .keyboardShortcut("k", modifiers: .command)
            }
            .disabled(mailbox == nil)
        }
    }

    /// The folders the Mailbox menu goes to on the account in front, as g then a letter does.
    private static let roles: [(role: String, title: String)] = [
        ("inbox", "Inbox"), ("sent", "Sent"), ("drafts", "Drafts"), ("archive", "Archive"), ("junk", "Junk"), ("trash", "Trash"),
    ]
}

/// View's items for the list: conversations or messages, and the Inbox's tabs on ⌥⌘1, 2 and 3. Meadow's View group
/// (`MeadowViewMenu`) holds them: a group placed before the sidebar's was dropped once Meadow replaced that group.
struct MailboxViewItems: View {
    let mailbox: Mailbox?

    var body: some View {
        Toggle("Organize by Conversation", isOn: Binding(
            get: { mailbox?.grouped ?? true },
            set: { mailbox?.setGrouped($0) }))
            .disabled(mailbox == nil)
        Section("Inbox") {
            ForEach(Array(InboxTab.allCases.enumerated()), id: \.offset) { index, tab in
                Toggle(tab.title, isOn: Binding(get: { mailbox?.tab == tab }, set: { on in if on { mailbox?.tab = tab } }))
                    .keyboardShortcut(KeyEquivalent(Character(String(index + 1))), modifiers: [.option, .command])
            }
        }
        .disabled(mailbox?.app?.place?.isInbox != true)
    }
}

/// What the Message menu's verbs act on in the window in front: a message's own window acts on its message alone, the
/// mail window on its rows chosen or the message open, and a window with neither on nothing.
@MainActor
struct MessageMenuVerbs {
    let acting: Bool
    /// A field is being typed in, where ⌘⌫ deletes text.
    let typing: Bool
    /// What they act on, as the window knows it now: what Mark as Read and Star say they will do.
    let shown: [API.FolderRow]
    let file: @MainActor (API.FilingVerb) -> Void
    let askMove: @MainActor () -> Void
    let setSeen: @MainActor (Bool) -> Void
    let star: @MainActor (_ on: Bool) -> Void

    init(window: MessageWindowVerbs?, mailbox: Mailbox?) {
        if let window {
            acting = window.acting
            typing = window.typing
            shown = window.shown
            file = { window.file($0) }
            askMove = { window.askMove() }
            setSeen = { window.setSeen($0) }
            star = { window.star(on: $0) }
        } else {
            acting = mailbox.map { $0.hasTarget && !$0.asking } ?? false
            typing = mailbox?.typing == true
            shown = mailbox?.shownRows ?? []
            file = { mailbox?.file($0) }
            askMove = { mailbox?.askMove() }
            setSeen = { mailbox?.setSeen($0) }
            star = { mailbox?.star(on: $0) }
        }
    }
}

/// What the read and star verbs say they will do to the rows a verb acts on: mark read while any is unread, and
/// unstar one starred message.
struct Marks: Equatable {
    let unread: Bool
    let unstars: Bool

    init(_ rows: [API.FolderRow]) {
        unread = rows.contains { !$0.seen }
        unstars = rows.count == 1 && rows[0].flagged
    }
}

extension Mailbox {
    /// A sheet is asking something of a person, so the window's verbs wait.
    var asking: Bool {
        moving != nil || folderAsk != nil || paletteOpen || savingView != nil || contact != nil
    }
}
#else
/// iPhone and iPad show step 0's list, which has no mailbox commands yet.
struct MailboxCommands: Commands {
    var body: some Commands { EmptyCommands() }
}
#endif
