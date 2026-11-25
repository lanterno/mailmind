import AppKit
import Foundation

/// The kit's stand-ins are the app's seams' shape; here they are said to be.
extension TestClock: AppClock {}
extension RecordedLinks: Links {}

/// The app's seams with a test's own in them (`AppSeams`), for one test: a clock that stands still until the test
/// moves it, links kept rather than opened, a pasteboard of the test's own, notifications kept rather than posted, no
/// login item. `restore()` puts back what was there.
@MainActor
final class TestSeams {
    /// A Sunday morning in November 2026, Paris: 15 Nov 2026, 11:46.
    static let start = Date(timeIntervalSince1970: 1_794_739_600)

    let clock: TestClock
    let links = RecordedLinks()
    let pasteboard = NSPasteboard.withUniqueName()
    let notifications = SystemNotifier.Silent()

    private let was: (clock: any AppClock, links: any Links, pasteboard: NSPasteboard, notifications: any Notifier,
                      loginItem: (any LoginItem)?)

    init(now: Date = TestSeams.start) {
        clock = TestClock(now: now)
        was = (AppSeams.clock, AppSeams.links, AppSeams.pasteboard, AppSeams.notifications, AppSeams.loginItem)
        AppSeams.clock = clock
        AppSeams.links = links
        AppSeams.pasteboard = pasteboard
        AppSeams.notifications = notifications
        AppSeams.loginItem = nil
    }

    /// The seams as they were before.
    func restore() {
        AppSeams.clock = was.clock
        AppSeams.links = was.links
        AppSeams.pasteboard = was.pasteboard
        AppSeams.notifications = was.notifications
        AppSeams.loginItem = was.loginItem
        pasteboard.releaseGlobally()
    }
}
