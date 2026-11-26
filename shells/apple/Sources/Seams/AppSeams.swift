import Foundation
#if os(macOS)
import AppKit
import Carbon.HIToolbox
import ServiceManagement
#endif

/// Every way the app reaches past itself to the system, in one place, as the core's `Seams` does: the clock and its
/// delays, the pasteboard, opening links and files, notifications, login items and the keyboard's layouts. A person's
/// run has the system's own, which do exactly what the app did before the seams; a test, or MailMind Dev under the
/// driver, puts in its own, which record rather than reach the Mac (docs/test-architecture.md section 6).
///
/// A seam is read each time it is used, never kept, so one put in before the work starts holds for all of it.
enum AppSeams {
    private static let lock = NSLock()
    nonisolated(unsafe) private static var heldClock: any AppClock = SystemClock()

    /// What the app reads the time through, and waits through.
    static var clock: any AppClock {
        get { lock.withLock { heldClock } }
        set { lock.withLock { heldClock = newValue } }
    }

    #if os(macOS)
    nonisolated(unsafe) private static var heldNotifications: (any Notifier)?

    /// Where new mail's notifications go: the system's notification center in a person's run, one that never asks and
    /// never posts in any other (`SystemNotifier.forThisProcess`).
    static var notifications: any Notifier {
        get {
            lock.withLock {
                if let held = heldNotifications { return held }
                let made = SystemNotifier.forThisProcess()
                heldNotifications = made
                return made
            }
        }
        set { lock.withLock { heldNotifications = newValue } }
    }

    /// The pasteboard Copy writes to and a paste of pictures reads: the Mac's general one.
    @MainActor static var pasteboard: NSPasteboard = .general

    /// What opens a link, a file or a folder outside the app: the browser and the Mac's other apps, through
    /// `NSWorkspace`.
    @MainActor static var links: any Links = SystemLinks()

    /// What opens MailMind at login: macOS's login item in a person's run, none in any other (`OpenAtLogin`).
    @MainActor static var loginItem: (any LoginItem)? = SystemLoginItem.forThisProcess()

    /// The keyboard layouts a single key is read under (`SingleKey`): the one keys are typed on now, and the
    /// ASCII-capable one macOS keeps beside it. A test names its own and never changes the Mac's.
    @MainActor static var layouts: () -> (typing: TISInputSource?, ascii: TISInputSource?) = {
        (TISCopyCurrentKeyboardLayoutInputSource()?.takeRetainedValue(),
         TISCopyCurrentASCIICapableKeyboardLayoutInputSource()?.takeRetainedValue())
    }

    #if DEBUG
    /// Told of each of the app's windows as its content first moves in, before it is drawn: MailMind Dev's driver
    /// parks it off the screen there (lane A, Sources/Dev). None in any other run, and a Release build has no such seam.
    @MainActor static var windowArrived: ((NSWindow) -> Void)?
    #endif
    #endif
}

#if os(macOS)
/// What opens a link, a file or a folder outside the app.
@MainActor
protocol Links {
    /// Opens `url` in the app the Mac opens it with: a web page in the browser, a file in its app.
    func open(_ url: URL)
    /// Shows `files` selected in a Finder window.
    func reveal(_ files: [URL])
}

/// The Mac's own: `NSWorkspace`.
struct SystemLinks: Links {
    func open(_ url: URL) {
        NSWorkspace.shared.open(url)
    }

    func reveal(_ files: [URL]) {
        NSWorkspace.shared.activateFileViewerSelecting(files)
    }
}

/// macOS's login item for the app, in a person's run only.
enum SystemLoginItem {
    /// `SMAppService.mainApp` in a person's run (`CoreHome.isAPersonsRun`); in any other none, so a demo's or a
    /// test's run never asks macOS, and never adds its own build to the person's login items.
    @MainActor
    static func forThisProcess(_ environment: [String: String] = ProcessInfo.processInfo.environment)
        -> (any LoginItem)?
    {
        CoreHome.isAPersonsRun(environment) ? SMAppService.mainApp : nil
    }
}
#endif
