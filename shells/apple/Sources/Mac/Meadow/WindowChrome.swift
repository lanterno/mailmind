#if os(macOS)
import AppKit
import SwiftUI

// The window's frame (#44): no title and no toolbar, Meadow's content running under the title bar, the traffic lights
// at the left of Meadow's top bar and centred on it, and the bar's empty stretches moving the window as a title bar
// does.

/// The room the traffic lights take at the top bar's left, before its first button.
enum WindowLights {
    /// Where the close button starts, and the step from one light to the next.
    static let left: CGFloat = 20
    static let step: CGFloat = 20
    /// The bar's padding before the sidebar button, past the three lights.
    static let room: CGFloat = 84
}

/// A window's root dressed as Meadow's: `WindowChrome` behind it, and SwiftUI's own bars told the same, so that
/// SwiftUI doesn't put them back. SwiftUI sets a window's title bar up again from its bars on each of the view's
/// updates, and the window of a `Settings` scene takes no `.hiddenTitleBar` style: left alone, SwiftUI made its title
/// bar opaque again and showed its title over Meadow's head. `on` is false where a test draws the view with no window
/// of its own.
struct MeadowWindowChrome: ViewModifier {
    var barHeight: CGFloat = ShellLayout.topBar
    var on = true

    func body(content: Content) -> some View {
        content
            .toolbar(removing: .title)
            .toolbarBackgroundVisibility(.hidden, for: .windowToolbar)
            .background { if on { WindowChrome(barHeight: barHeight) } }
    }
}

extension View {
    /// This view as its window's root, dressed as Meadow's with its head `barHeight` high (`MeadowWindowChrome`).
    func meadowWindowChrome(barHeight: CGFloat = ShellLayout.topBar, on: Bool = true) -> some View {
        modifier(MeadowWindowChrome(barHeight: barHeight, on: on))
    }
}

/// Set the window up for Meadow once it is in one, and keep its traffic lights on the top bar as AppKit lays the
/// title bar out again (a resize, full screen, the window becoming key, a new title).
struct WindowChrome: NSViewRepresentable {
    var barHeight: CGFloat = ShellLayout.topBar

    func makeNSView(context: Context) -> WindowChromeView {
        WindowChromeView(barHeight: barHeight)
    }

    func updateNSView(_ view: WindowChromeView, context: Context) {
        view.placeLights()
    }
}

final class WindowChromeView: NSView {
    let barHeight: CGFloat
    private var watching: [NSObjectProtocol] = []

    init(barHeight: CGFloat) {
        self.barHeight = barHeight
        super.init(frame: .zero)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not from a nib") }

    /// Taken out of its window, it stops listening to it.
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        for token in watching { NotificationCenter.default.removeObserver(token) }
        watching = []
        guard let window else { return }
        #if DEBUG
        // Under MailMind Dev's driver, off the screen before it is first drawn (lane A, Sources/Dev).
        AppSeams.windowArrived?(window)
        #endif
        Self.dress(window)
        let names: [Notification.Name] = [
            NSWindow.didResizeNotification, NSWindow.didEndLiveResizeNotification, NSWindow.didBecomeKeyNotification,
            NSWindow.didExitFullScreenNotification, NSWindow.didChangeScreenNotification,
        ]
        for name in names {
            watching.append(NotificationCenter.default.addObserver(forName: name, object: window, queue: .main) {
                [weak self] _ in MainActor.assumeIsolated { self?.placeLights() }
            })
        }
        // A new title (a message's subject, Settings' place) lays the title bar out again, which sets the lights back
        // where AppKit puts them: they go back where they belong each time AppKit moves them. AppKit ignores a light
        // moved while it is still moving it, so they are moved once it is done.
        for view in Self.titlebarViews(of: window) {
            view.postsFrameChangedNotifications = true
            watching.append(NotificationCenter.default.addObserver(
                forName: NSView.frameDidChangeNotification, object: view, queue: .main
            ) { [weak self] _ in MainActor.assumeIsolated { self?.placeLightsSoon() } })
        }
        placeLights()
    }

    private var placing = false

    /// The lights placed once AppKit is done with what it is laying out, once however many frames it changed.
    private func placeLightsSoon() {
        guard !placing else { return }
        placing = true
        DispatchQueue.main.async { [weak self] in
            MainActor.assumeIsolated {
                self?.placing = false
                self?.placeLights()
            }
        }
    }

    /// The title bar's views that AppKit lays out: the three buttons, the bar and its container.
    static func titlebarViews(of window: NSWindow) -> [NSView] {
        let buttons = [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton]
            .compactMap { window.standardWindowButton($0) }
        guard let titlebar = buttons.first?.superview else { return buttons }
        return buttons + [titlebar] + [titlebar.superview].compactMap { $0 }
    }

    /// No title, a clear title bar with the content under it, and no line beneath it.
    static func dress(_ window: NSWindow) {
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.styleMask.insert(.fullSizeContentView)
        window.titlebarSeparatorStyle = .none
    }

    /// Centre the traffic lights on the top bar: the title bar's view is grown to the bar's height, and the buttons set
    /// down in its middle, from the bar's left.
    func placeLights() {
        guard let window, !window.styleMask.contains(.fullScreen) else { return }
        Self.placeLights(in: window, barHeight: barHeight)
    }

    static func placeLights(in window: NSWindow, barHeight: CGFloat) {
        let kinds: [NSWindow.ButtonType] = [.closeButton, .miniaturizeButton, .zoomButton]
        let buttons = kinds.compactMap { window.standardWindowButton($0) }
        guard buttons.count == kinds.count, let titlebar = buttons[0].superview, let container = titlebar.superview,
              let frameView = container.superview else { return }
        var frame = container.frame
        frame.size.height = barHeight
        frame.origin.y = frameView.bounds.height - barHeight
        if container.frame != frame { container.frame = frame }
        if titlebar.frame != container.bounds { titlebar.frame = container.bounds }
        for (index, button) in buttons.enumerated() {
            let origin = NSPoint(x: WindowLights.left + CGFloat(index) * WindowLights.step,
                                 y: ((barHeight - button.frame.height) / 2).rounded())
            if button.frame.origin != origin { button.setFrameOrigin(origin) }
        }
    }
}

/// What lies behind the top bar's controls: a press there moves the window, and a double-click does what the
/// person set double-clicking a title bar to do.
struct WindowDragArea: NSViewRepresentable {
    func makeNSView(context: Context) -> WindowDragView { WindowDragView() }
    func updateNSView(_ view: WindowDragView, context: Context) {}
}

final class WindowDragView: NSView {
    override var mouseDownCanMoveWindow: Bool { true }

    override func mouseDown(with event: NSEvent) {
        guard let window else { return }
        if event.clickCount == 2 {
            switch UserDefaults.standard.string(forKey: "AppleActionOnDoubleClick") {
            case "Minimize": window.performMiniaturize(nil)
            case "None": break
            default: window.performZoom(nil)
            }
            return
        }
        window.performDrag(with: event)
    }
}

/// Says whether the window this stands in is in full screen, as it goes in and comes out: there the traffic lights
/// leave the top bar, and the room kept for them goes too.
struct FullScreenWatch: NSViewRepresentable {
    let changed: @MainActor (Bool) -> Void

    func makeNSView(context: Context) -> FullScreenWatchView {
        FullScreenWatchView(changed: changed)
    }

    func updateNSView(_ view: FullScreenWatchView, context: Context) {
        view.changed = changed
    }
}

final class FullScreenWatchView: NSView {
    var changed: @MainActor (Bool) -> Void
    private var watching: [NSObjectProtocol] = []

    init(changed: @escaping @MainActor (Bool) -> Void) {
        self.changed = changed
        super.init(frame: .zero)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not from a nib") }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        for token in watching { NotificationCenter.default.removeObserver(token) }
        watching = []
        guard let window else { return }
        // Said after the view's update, never during it.
        let now = window.styleMask.contains(.fullScreen)
        DispatchQueue.main.async { [weak self] in self?.changed(now) }
        let turns: [(Notification.Name, Bool)] = [
            (NSWindow.willEnterFullScreenNotification, true), (NSWindow.didExitFullScreenNotification, false),
        ]
        for (name, full) in turns {
            watching.append(NotificationCenter.default.addObserver(forName: name, object: window, queue: .main) {
                [weak self] _ in MainActor.assumeIsolated { self?.changed(full) }
            })
        }
    }
}
#endif
