import SwiftUI

#if os(macOS)
/// What the menu bar's Print and Find in Message act on: the reader in the window in front, while a message is open.
struct ReaderTools {
    let print: () -> Void
    let find: () -> Void
}

extension FocusedValues {
    @Entry var readerTools: ReaderTools?
}

/// File › Print… (⌘P) and Edit › Find in Message… (⌥⌘F, or ⌘F in a message's own window), for the reader in front.
struct ReaderCommands: Commands {
    @FocusedValue(\.readerTools) private var tools
    @FocusedValue(\.messageWindow) private var messageWindow

    var body: some Commands {
        CommandGroup(replacing: .printItem) {
            Button("Print…") { tools?.print() }
                .keyboardShortcut("p", modifiers: .command)
                .disabled(tools == nil)
        }
        CommandGroup(after: .textEditing) { FindInMessageItem(tools: tools, messageWindow: messageWindow != nil) }
    }
}

/// Edit › Find in Message… for the reader in front. In the mail window ⌘F searches the mail and this is ⌥⌘F; a
/// message's own window has no search, so there ⌘F finds in its message, as Mail's ⌘F does. Edit stands before the
/// Mailbox menu, whose Search waits there, so ⌘F reaches this first. A view of its own, so a test draws it as
/// AppKit's menu.
struct FindInMessageItem: View {
    let tools: ReaderTools?
    var messageWindow = false

    var body: some View {
        Button("Find in Message…") { tools?.find() }
            .keyboardShortcut("f", modifiers: messageWindow ? .command : [.option, .command])
            .disabled(tools == nil)
    }
}
#else
/// iPhone and iPad show step 0's list, which has no reader to print or find in yet.
struct ReaderCommands: Commands {
    var body: some Commands { EmptyCommands() }
}
#endif
