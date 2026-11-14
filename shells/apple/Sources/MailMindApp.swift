import SwiftUI

@main
struct MailMindApp: App {
    @State private var demo = Demo()

    var body: some Scene {
        WindowGroup {
            Root(demo: demo)
        }
        .commands { UpdateCommands() }
        #if os(macOS)
        .defaultSize(width: 560, height: 820)
        #endif
    }
}
