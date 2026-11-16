import SwiftUI

#if os(macOS)
extension FocusedValues {
    /// The mailbox of the window in front, which the menu bar's commands act on.
    @Entry var mailbox: Mailbox?
}

/// The page's commands (app/commands.ts) in the menu bar, with a Mac's keys: the Message menu acts on what is chosen
/// or open, the Mailbox menu goes places and searches, and View holds how the list is shown. Each runs as its key, its
/// button in the pane's bar and its palette entry do. Edit ▸ Undo undoes what the mailbox did last.
struct MailboxCommands: Commands {
    @FocusedValue(\.mailbox) private var mailbox

    var body: some Commands {
        CommandMenu("Message") {
            ComposeMessageItems()
            Divider()
            let acting = mailbox.map { $0.hasTarget && !$0.asking } ?? false
            Button("Archive") { mailbox?.file(.archive) }
                .keyboardShortcut("a", modifiers: [.control, .command])
                .disabled(!acting)
            // ⌘⌫ deletes text while a field is being typed in, so it deletes mail only from the list.
            Button("Delete") { mailbox?.file(.trash) }
                .keyboardShortcut(.delete, modifiers: .command)
                .disabled(!acting || mailbox?.typing == true)
            Button("Mark as Junk") { mailbox?.file(.junk) }
                .keyboardShortcut("j", modifiers: [.shift, .command])
                .disabled(!acting)
            Button("Move To…") { mailbox?.askMove() }
                .keyboardShortcut("m", modifiers: [.shift, .command])
                .disabled(!acting)
            Divider()
            Button(mailbox?.chosenRows.contains { !$0.seen } == true ? "Mark as Read" : "Mark as Unread") {
                guard let mailbox else { return }
                mailbox.setSeen(mailbox.chosenRows.count > 1 ? mailbox.chosenRows.contains { !$0.seen } : nil)
            }
            .keyboardShortcut("u", modifiers: [.shift, .command])
            .disabled(!acting)
            Button("Star") { mailbox?.star() }
                .keyboardShortcut("l", modifiers: [.shift, .command])
                .disabled(!acting)
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
            let sender = mailbox?.chosenRows.count == 1 ? mailbox?.chosenRows.first?.fromAddr : nil
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
        CommandGroup(before: .sidebar) {
            Toggle("Organize by Conversation", isOn: Binding(
                get: { mailbox?.grouped ?? true },
                set: { mailbox?.setGrouped($0) }))
                .disabled(mailbox == nil)
            Picker("Inbox", selection: Binding(get: { mailbox?.tab ?? .needs }, set: { mailbox?.tab = $0 })) {
                ForEach(Array(InboxTab.allCases.enumerated()), id: \.offset) { index, tab in
                    Text(tab.title)
                        .tag(tab)
                        .keyboardShortcut(KeyEquivalent(Character(String(index + 1))), modifiers: [.option, .command])
                }
            }
            .pickerStyle(.inline)
            .disabled(mailbox?.app?.place?.isInbox != true)
            Divider()
        }
    }

    /// The folders the Mailbox menu goes to on the account in front, as g then a letter does.
    private static let roles: [(role: String, title: String)] = [
        ("inbox", "Inbox"), ("sent", "Sent"), ("drafts", "Drafts"), ("archive", "Archive"), ("junk", "Junk"), ("trash", "Trash"),
    ]
}

extension Mailbox {
    /// A sheet is asking something of a person, so the window's verbs wait.
    var asking: Bool {
        moving != nil || paletteOpen || savingView != nil || contact != nil
    }
}
#else
/// iPhone and iPad show step 0's list, which has no mailbox commands yet.
struct MailboxCommands: Commands {
    var body: some Commands { EmptyCommands() }
}
#endif
