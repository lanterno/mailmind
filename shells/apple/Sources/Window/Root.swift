import SwiftUI

/// What the app's window holds. On the Mac, the app itself: the core in this process, its accounts, mail and reader.
/// On iPhone and iPad, and on the Mac in step 0's measure modes (`just apple measure` and `smoke`), step 0's list over
/// its fictional mailbox.
struct Root: View {
    /// The window's scene.
    static let scene = "mail"
    let demo: Demo

    var body: some View {
        #if os(macOS)
        if Self.stepZero {
            stepZeroList
        } else {
            MailWindow()
        }
        #else
        stepZeroList
        #endif
    }

    private var stepZeroList: some View {
        InboxView(demo: demo)
            .task { await demo.start() }
    }

    /// Step 0's measure modes measure its list; `home` measures the window on a demo home.
    static var stepZero: Bool {
        let mode = ProcessInfo.processInfo.environment["MAILMIND_MEASURE"]
        return mode != nil && mode != "home"
    }
}
