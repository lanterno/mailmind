import SwiftUI

@main
struct MailMindApp: App {
    @State private var demo = Demo()

    #if os(macOS)
    init() {
        // Quick Look's copies of attachments, decrypted: what a crash left, now, and what is still open, at quit.
        PreviewFiles.sweepNowAndAtQuit()
    }
    #endif

    var body: some Scene {
        WindowGroup {
            Root(demo: demo)
        }
        .commands {
            UpdateCommands()
            AcknowledgementsCommands()
            MailboxCommands()
        }
        #if os(macOS)
        .defaultSize(width: 560, height: 820)
        #endif

        #if os(macOS)
        AcknowledgementsWindow()
        // Settings (⌘,), MailMind Help, and the status's commands in the menu bar.
        StatusScenes()
        #endif
    }
}
