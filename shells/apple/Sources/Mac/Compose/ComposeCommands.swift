#if os(macOS)
import SwiftUI

/// What the window in front can write: a reply to the message on screen, the draft on screen to edit, and the
/// account a new message goes from.
struct MailWriting: Equatable {
    var replyTo: MailCopy?
    var draft: MailCopy?
    var current: String?
}

extension FocusedValues {
    @Entry var mailWriting: MailWriting?
}

/// The menu bar's New Message, in File, with the key Mail gives it.
struct ComposeCommands: Commands {
    @FocusedValue(\.mailWriting) private var writing

    var body: some Commands {
        CommandGroup(replacing: .newItem) {
            Button("New Message") {
                Composing.shared.newMessage(current: writing?.current)
            }
            .keyboardShortcut("n")
        }
    }
}

/// Reply, Reply All, Forward and Edit Draft, at the top of the mailbox's Message menu, each with the key Mail gives it,
/// for what the window in front can write (its `mailWriting`).
struct ComposeMessageItems: View {
    let writing: MailWriting?

    var body: some View {
            Button("Reply") {
                if let to = writing?.replyTo { Task { await Composing.shared.answer(to) } }
            }
            .keyboardShortcut("r")
            .disabled(writing?.replyTo == nil)
            Button("Reply All") {
                if let to = writing?.replyTo { Task { await Composing.shared.reply(to, all: true) } }
            }
            .keyboardShortcut("r", modifiers: [.command, .shift])
            .disabled(writing?.replyTo == nil)
            Button("Forward") {
                if let to = writing?.replyTo { Task { await Composing.shared.forward(to) } }
            }
            .keyboardShortcut("f", modifiers: [.command, .shift])
            .disabled(writing?.replyTo == nil)
            Divider()
            Button("Edit Draft") {
                if let draft = writing?.draft { Task { await Composing.shared.editDraft(draft) } }
            }
            .disabled(writing?.draft == nil)
    }
}
#endif
