import SwiftUI

@main
struct MailMindApp: App {
    @State private var demo = Demo()

    var body: some Scene {
        WindowGroup {
            InboxView(demo: demo)
                .task { await demo.start() }
        }
        #if os(macOS)
        .defaultSize(width: 560, height: 820)
        #endif
    }
}
