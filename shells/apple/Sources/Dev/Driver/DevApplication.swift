#if DEBUG && os(macOS)
import AppKit

/// MailMind Dev's application under the driver, in lane A (docs/test-architecture.md section 10): it never takes the
/// Mac from the person using it. The app is an accessory with its windows far off the screen and see-through
/// (`LaneA`), and it is never activated, so it never has the keyboard or the menu bar. Inside the process it answers
/// as an active app would, from the window the driver made key (`driven`), so AppKit routes keys and validates the
/// menu bar for that window as it does a person's. No method of AppKit's is swizzled: these are overrides.
///
/// Without the driver it is NSApplication as it was: every override asks `DevDriver.on` first.
final class DevApplication: NSApplication {
    /// The window the driver made key through AppKit's own `makeKey()`.
    weak var driven: NSWindow?

    override var isActive: Bool {
        DevDriver.on ? driven != nil : super.isActive
    }

    override var keyWindow: NSWindow? {
        guard DevDriver.on else { return super.keyWindow }
        guard let driven, driven.isVisible else { return nil }
        return driven
    }

    override var mainWindow: NSWindow? {
        DevDriver.on ? keyWindow : super.mainWindow
    }

    // Never in front: whatever asks (a notification's click, a window opened), the person's app keeps the Mac.

    override func activate(ignoringOtherApps flag: Bool) {
        if DevDriver.on { DevDriver.refused("activate the app"); return }
        super.activate(ignoringOtherApps: flag)
    }

    override func activate() {
        if DevDriver.on { DevDriver.refused("activate the app"); return }
        super.activate()
    }

    override func unhide(_ sender: Any?) {
        if DevDriver.on { unhideWithoutActivation(); return }
        super.unhide(sender)
    }

    override func requestUserAttention(_ requestType: NSApplication.RequestUserAttentionType) -> Int {
        if DevDriver.on { return 0 }
        return super.requestUserAttention(requestType)
    }

    /// A window run modally (an alert, a save panel) would come up over the person's screen and wait for a hand: under
    /// the driver it is turned down, and the driver says so.
    override func runModal(for window: NSWindow) -> NSApplication.ModalResponse {
        if DevDriver.on {
            DevDriver.refused("run \(window.title.isEmpty ? "a window" : "“\(window.title)”") modally")
            return .abort
        }
        return super.runModal(for: window)
    }
}

/// Lane A's windows: far off the screen and see-through, as `RealKeys.key` opens a test's, from before they are first
/// drawn. A window is parked as it moves into its first view (`WindowChromeView`), as it becomes key or main, and before
/// the main run loop draws (an observer ahead of Core Animation's), so nothing reaches the screen.
@MainActor
enum LaneA {
    static let away = NSPoint(x: -20_000, y: -20_000)

    /// Set once the app has finished launching.
    private static var launched = false

    /// Every window of the app parked. The app, hidden as it launched, comes back once a window of its own is there,
    /// parked, without being activated.
    static func parkAll() {
        for window in NSApp.windows { park(window) }
        if launched, NSApp.isHidden, NSApp.windows.contains(where: { $0.contentView != nil && $0.canBecomeKey }) {
            NSApp.unhideWithoutActivation()
        }
    }

    /// `window` see-through, and moved far off the screen unless it is off it already (a sheet or a popover's panel
    /// stands off it with its parent).
    static func park(_ window: NSWindow) {
        if window.alphaValue != 0 { window.alphaValue = 0 }
        if window.hasShadow { window.hasShadow = false }
        // AppKit keeps a corner of a window on a screen however far it is sent: see-through, and the person's clicks
        // pass through it. The driver's own clicks are handed to the window in the app, never through the screen.
        if !window.ignoresMouseEvents { window.ignoresMouseEvents = true }
        let screens = NSScreen.screens.map(\.frame)
        if screens.contains(where: { $0.intersects(window.frame) }) {
            window.setFrameOrigin(away)
        }
    }

    /// Whether any of the app's windows could be seen: on a screen and not see-through. The driver answers it, and the
    /// runner fails a run where one ever was.
    static var anyOnScreen: [String] {
        let screens = NSScreen.screens.map(\.frame)
        return NSApp.windows.filter { window in
            window.isVisible && window.alphaValue > 0 && screens.contains(where: { $0.intersects(window.frame) })
        }.map { "\(type(of: $0)) “\($0.title)” at \(NSStringFromRect($0.frame))" }
    }

    private static var watching: [NSObjectProtocol] = []

    /// Parks each window as it comes, from now on.
    static func start() {
        let observer = CFRunLoopObserverCreateWithHandler(
            nil, CFRunLoopActivity.beforeWaiting.rawValue | CFRunLoopActivity.afterWaiting.rawValue, true, 0
        ) { _, _ in
            MainActor.assumeIsolated { parkAll() }
        }
        CFRunLoopAddObserver(CFRunLoopGetMain(), observer, .commonModes)
        let names: [Notification.Name] = [
            NSWindow.didBecomeKeyNotification, NSWindow.didBecomeMainNotification, NSWindow.didUpdateNotification,
            NSWindow.didMoveNotification, NSWindow.didChangeScreenNotification, NSWindow.didResizeNotification,
        ]
        for name in names {
            watching.append(NotificationCenter.default.addObserver(forName: name, object: nil, queue: nil) { note in
                nonisolated(unsafe) let window = note.object as? NSWindow
                MainActor.assumeIsolated {
                    if let window { park(window) }
                }
            })
        }
        // As the app finishes launching it is hidden, so its first window can't reach the screen before it is parked;
        // it comes back, without being activated, once its window is there and parked (`parkAll`).
        watching.append(NotificationCenter.default.addObserver(
            forName: NSApplication.willFinishLaunchingNotification, object: nil, queue: nil
        ) { _ in
            MainActor.assumeIsolated { NSApp.hide(nil) }
        })
        watching.append(NotificationCenter.default.addObserver(
            forName: NSApplication.didFinishLaunchingNotification, object: nil, queue: nil
        ) { _ in
            MainActor.assumeIsolated {
                launched = true
                parkAll()
            }
        })
    }
}
#endif
