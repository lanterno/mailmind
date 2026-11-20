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
    @Environment(\.openWindow) private var openWindow

    var body: some Commands {
        let door = MailWindowDoor.ofThisApp(openWindow)
        CommandGroup(replacing: .printItem) { PrintItem(tools: tools, messageWindow: messageWindow, door: door) }
        CommandGroup(after: .textEditing) {
            FindInMessageItem(tools: tools, messageWindow: messageWindow != nil, window: messageWindow, door: door)
        }
    }
}

/// File › Print… for the reader in front, while its window is open (`MailWindowDoor`).
private struct PrintItem: View {
    let tools: ReaderTools?
    let messageWindow: MessageWindowVerbs?
    let door: MailWindowDoor

    var body: some View {
        let tools = door.trusts(messageWindow) ? tools : nil
        Button("Print…") { tools?.print() }
            .keyboardShortcut("p", modifiers: .command)
            .disabled(tools == nil)
    }
}

/// Edit › Find in Message… for the reader in front. In the mail window ⌘F searches the mail and this is ⌥⌘F; a
/// message's own window has no search, so there ⌘F finds in its message, as Mail's ⌘F does. Edit stands before the
/// Mailbox menu, whose Search waits there, so ⌘F reaches this first. A view of its own, so a test draws it as
/// AppKit's menu.
struct FindInMessageItem: View {
    let tools: ReaderTools?
    var messageWindow = false
    /// The message window that handed the tools, if one did, and whether a mail window is open: a closed window's
    /// tools find nothing.
    var window: MessageWindowVerbs?
    var door = MailWindowDoor.alwaysOpen

    var body: some View {
        let tools = door.trusts(window) ? tools : nil
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
