import Foundation

/// The window's measure mode, run only when `MAILMIND_MEASURE=home` (on a demo home, `just apple demo --measure` and
/// `--smoke`): it prints how long it took from process start to the first rows of mail on screen, then the app's
/// footprint after three seconds idle, and quits. Its exit code is the smoke check's: 0 once mail showed, 3 after a
/// minute without. Without it, nothing here runs.
@MainActor
final class HomeMeasure {
    static let shared = HomeMeasure()
    private let measuring = ProcessInfo.processInfo.environment["MAILMIND_MEASURE"] == "home"

    func watch(_ app: MailApp) async {
        guard measuring else { return }
        #if os(macOS)
        // A demo home's mail has no AI verdicts yet, so nothing needs you: the Inbox shows Everything.
        app.mailbox.tab = .all
        #endif
        let started = ContinuousClock.now
        while ContinuousClock.now - started < .seconds(60) {
            if let list = app.list, list.total > 0, list.row(at: 0) != nil {
                emit("first_rows_ms", Date().timeIntervalSince(Measure.processStart()) * 1000)
                print("rows_total=\(list.total)")
                try? await Task.sleep(for: .seconds(3))
                emit("idle_footprint_mb", Measure.footprintMB())
                exit(0)
            }
            if case .failed(let why) = app.stage {
                print("failed: \(why)")
                fflush(stdout)
                exit(3)
            }
            try? await Task.sleep(for: .milliseconds(5))
        }
        print("stuck: no mail in the list after a minute")
        fflush(stdout)
        exit(3)
    }

    private func emit(_ name: String, _ value: Double) {
        print("\(name)=\(String(format: "%.1f", value))")
        fflush(stdout)
    }
}
