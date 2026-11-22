import SwiftUI

/// The app. `main.swift` starts it, once a run that isn't a person's has made its preferences its own.
struct MailMindApp: App {
    @State private var demo = Demo()
    #if os(macOS)
    /// The mailto: links macOS hands the app (Mac/Compose/Mailto.swift).
    @NSApplicationDelegateAdaptor(MailMindDelegate.self) private var delegate
    #endif

    init() {
        // Meadow's typefaces, for this process, before the first window sets any text in them.
        MeadowFonts.register()
        #if os(macOS)
        // Quick Look's copies of attachments, decrypted: what a crash left, now, and what is still open, at quit.
        PreviewFiles.sweepNowAndAtQuit()
        // Which window is key, heard from the first: the menus act on it.
        _ = KeyWindow.shared
        #endif
    }

    var body: some Scene {
        // Named, so a notification's click can open the window again once it was closed.
        WindowGroup(id: Root.scene) {
            Root(demo: demo)
        }
        .commands {
            UpdateCommands()
            AcknowledgementsCommands()
            MailboxCommands()
            ReaderCommands()
            DeskCommands()
        }
        #if os(macOS)
        // Meadow's top bar stands where the title bar was, the traffic lights on it (Mac/Meadow/WindowChrome.swift):
        // with SwiftUI's own title bar, its title and its glass are drawn over the top bar.
        .windowStyle(.hiddenTitleBar)
        // Three columns want room; step 0's list keeps the size its measurements were taken at.
        .defaultSize(width: Root.stepZero ? 560 : 1180, height: Root.stepZero ? 820 : 760)
        // The menu bar's writing, and each composer in a window of its own: Mac/Compose.
        .commands { ComposeCommands() }
        // File › Close, for the window AppKit says is key: Mac/Shared/KeyWindow.swift.
        .commands { CloseCommands() }
        // The View menu's rail, focus reading and light: Mac/Meadow.
        .commands { MeadowCommands() }
        #endif
        #if os(macOS)
        WindowGroup("New Message", id: ComposeWindow.scene, for: UUID.self) { $id in
            ComposeWindow(id: id)
                .meadowLit()
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 720, height: 640)
        .restorationBehavior(.disabled)
        .commandsRemoved()
        // A message in a window of its own, as a double-click on its row opens it: Mac/Reader/MessageWindow.swift.
        WindowGroup("Message", id: MessageWindow.scene, for: MailCopy.self) { $copy in
            MessageWindow(copy: copy)
                .meadowLit()
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 820, height: 760)
        .restorationBehavior(.disabled)
        .commandsRemoved()
        #endif

        #if os(macOS)
        AcknowledgementsWindow()
        HostedMailboxScene()
        // Settings (⌘,), MailMind Help, and the status's commands in the menu bar.
        StatusScenes()
        #endif
    }
}
