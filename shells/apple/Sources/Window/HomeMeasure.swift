#if os(macOS)
import AppKit
#endif
import Foundation

/// The window's measure mode, run only when `MAILMIND_MEASURE=home` (on a demo home, `just apple demo --measure` and
/// `--smoke`): it prints how long it took from process start to the first rows of mail on screen, then the app's
/// footprint after three seconds idle, and quits. Its exit code is the smoke check's: 0 once mail showed, 3 after a
/// minute without. With `MAILMIND_PICTURE` naming a folder too (`just apple demo picture`), it draws the window into it
/// instead, light and dark: the Inbox with its first message open, then today's brief. Without them, nothing here runs.
@MainActor
final class HomeMeasure {
    static let shared = HomeMeasure()
    private let measuring = ProcessInfo.processInfo.environment["MAILMIND_MEASURE"] == "home"
    private let pictures = ProcessInfo.processInfo.environment["MAILMIND_PICTURE"].map { URL(filePath: $0) }

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
                #if os(macOS)
                if let pictures {
                    await draw(app, into: pictures)
                    exit(0)
                }
                #endif
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

    #if os(macOS)
    /// The Inbox with its first message open, then today's brief, each light and dark, once the sync has settled: at
    /// the size a first launch opens at, then the Inbox at the narrowest the window goes.
    private func draw(_ app: MailApp, into folder: URL) async {
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        resize(to: NSSize(width: 1180, height: 760))
        try? await Task.sleep(for: .seconds(3))
        app.choose(0)
        try? await Task.sleep(for: .seconds(2))
        await snap("inbox", into: folder)
        resize(to: NSSize(width: 900, height: 760))
        try? await Task.sleep(for: .seconds(1))
        await snap("inbox-narrow", into: folder)
        resize(to: NSSize(width: 1180, height: 760))
        app.place = .desk(.brief)
        try? await Task.sleep(for: .seconds(3))
        await snap("brief", into: folder)
    }

    private func resize(to size: NSSize) {
        mainWindow?.setContentSize(size)
    }

    private var mainWindow: NSWindow? {
        NSApp.windows.first { $0.isVisible && $0.frame.width >= 900 }
    }

    /// The window as it stands, drawn by the app itself, so no screen capture is asked for.
    private func snap(_ name: String, into folder: URL) async {
        for (look, appearance) in [("light", NSAppearance.Name.aqua), ("dark", .darkAqua)] {
            NSApp.appearance = NSAppearance(named: appearance)
            try? await Task.sleep(for: .milliseconds(600))
            guard let frame = mainWindow?.contentView?.superview, let png = await Self.picture(of: frame) else {
                print("picture: no window")
                continue
            }
            let url = folder.appending(path: "\(name)-\(look).png")
            try? png.write(to: url)
            print("picture=\(url.path)")
            fflush(stdout)
        }
        NSApp.appearance = nil
    }

    /// The window drawn by the app itself, laid out first: a window on a screen that's asleep or locked is never
    /// displayed, and a table makes its rows' views only as it lays out.
    private static func picture(of frame: NSView) async -> Data? {
        frame.window?.contentView?.layoutSubtreeIfNeeded()
        frame.layoutSubtreeIfNeeded()
        frame.window?.displayIfNeeded()
        guard let picture = frame.bitmapImageRepForCachingDisplay(in: frame.bounds) else { return nil }
        frame.cacheDisplay(in: frame.bounds, to: picture)
        return picture.representation(using: .png, properties: [:])
    }
    #endif

    private func emit(_ name: String, _ value: Double) {
        print("\(name)=\(String(format: "%.1f", value))")
        fflush(stdout)
    }
}
