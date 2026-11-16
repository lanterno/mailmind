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

/// File › Print… (⌘P) and Edit › Find in Message… (⌥⌘F), for the reader in front.
struct ReaderCommands: Commands {
    @FocusedValue(\.readerTools) private var tools

    var body: some Commands {
        CommandGroup(replacing: .printItem) {
            Button("Print…") { tools?.print() }
                .keyboardShortcut("p", modifiers: .command)
                .disabled(tools == nil)
        }
        CommandGroup(after: .textEditing) {
            Button("Find in Message…") { tools?.find() }
                .keyboardShortcut("f", modifiers: [.option, .command])
                .disabled(tools == nil)
        }
    }
}
#else
/// iPhone and iPad show step 0's list, which has no reader to print or find in yet.
struct ReaderCommands: Commands {
    var body: some Commands { EmptyCommands() }
}
#endif
