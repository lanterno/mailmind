#if DEBUG && os(macOS)
import AppKit

extension TestClock: AppClock {}
extension RecordedLinks: Links {}

/// MailMind Dev's driver (docs/test-architecture.md section 5.2): when `MAILMIND_TEST_DRIVER` names a socket, the app
/// connects out to it and carries out the runner's commands, one JSON line each way (`DriverLink`, `DriverCommands`).
/// `main.swift` starts it before the app does, so the application is `DevApplication` from the first, an accessory
/// whose windows stay off the screen (lane A, section 10), and the seams hold the test's own: the clock standing at
/// `MAILMIND_TEST_NOW`, links recorded, a pasteboard of the run's own, notifications kept (section 6).
@MainActor
enum DevDriver {
    static let variable = "MAILMIND_TEST_DRIVER"

    /// Whether the driver runs in this process.
    nonisolated(unsafe) private(set) static var on = false
    /// The app's clock: the test's, which stands still until the runner moves it.
    private(set) static var clock = TestClock(now: Date(timeIntervalSince1970: 0))
    /// What the app asked to open, kept.
    static let links = RecordedLinks()
    static let notifications = SystemNotifier.Silent()
    /// What lane A turned down (activating the app, a modal window), said with the next answer.
    private(set) static var refusals: [String] = []
    private static var link: DriverLink?

    /// Starts the driver if the environment asks for it; a run that asks but isn't safe ends here, saying why.
    static func startIfAsked(_ environment: [String: String] = ProcessInfo.processInfo.environment) {
        guard let socket = environment[variable], !socket.isEmpty else { return }
        if let why = DriverSafety.refusal(environment, identifier: Bundle.main.bundleIdentifier,
                                          temporary: NSTemporaryDirectory()) {
            FileHandle.standardError.write(Data("MailMind Dev's driver won't start: \(why)\n".utf8))
            exit(78)
        }
        // The application first, before SwiftUI asks for one: AppKit makes the first class asked for.
        _ = DevApplication.shared
        on = true
        NSApp.setActivationPolicy(.accessory)
        LaneA.start()
        let now = environment["MAILMIND_TEST_NOW"].flatMap(Self.date) ?? Date()
        clock = TestClock(now: now)
        AppSeams.clock = clock
        AppSeams.links = links
        AppSeams.pasteboard = NSPasteboard.withUniqueName()
        AppSeams.notifications = notifications
        AppSeams.loginItem = nil
        AppSeams.windowArrived = { LaneA.park($0) }
        CoreWatch.shared.on = true
        // SwiftUI builds its accessibility tree only for an assistive app that asks: the driver is one.
        Find.assistive(true)
        guard let link = DriverLink(path: socket) else {
            FileHandle.standardError.write(Data("MailMind Dev's driver couldn't reach its runner\n".utf8))
            exit(75)
        }
        self.link = link
        link.start()
    }

    /// Lane A turned something down.
    static func refused(_ what: String) {
        refusals.append("lane A turned down: \(what)")
    }

    /// What lane A turned down since the last answer.
    static func takeRefusals() -> [String] {
        defer { refusals = [] }
        return refusals
    }

    /// A time as the runner writes one: ISO 8601, with or without its fraction of a second.
    nonisolated static func date(_ text: String) -> Date? {
        let plain = ISO8601DateFormatter()
        if let date = plain.date(from: text) { return date }
        let fine = ISO8601DateFormatter()
        fine.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fine.date(from: text)
    }

    /// The window the driver acts in, made key through AppKit's own `makeKey()`: the frontmost of the app's windows
    /// that can be key. A window AppKit made key itself (a composer opened) is it while it stays in front.
    static func keyWindow() -> NSWindow? {
        guard let app = NSApp as? DevApplication else { return nil }
        let front = NSApp.orderedWindows.first { $0.isVisible && $0.canBecomeKey && $0.parent == nil }
        let sheet = front?.attachedSheet
        let chosen = sheet ?? front
        if let chosen, app.driven !== chosen {
            app.driven = chosen
            chosen.makeKey()
        }
        if chosen == nil { app.driven = nil }
        return chosen
    }
}
#endif
