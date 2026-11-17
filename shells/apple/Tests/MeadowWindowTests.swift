import AppKit
import SwiftUI
import XCTest

/// Meadow's window (#44): the traffic lights on the top bar, the content under the title bar, the pane's bar's height
/// with and without a message, and what meets the window's top in a window of its own: the welcome and Help.
@MainActor
final class MeadowWindowTests: XCTestCase {
    func testTheTrafficLightsSitOnTheTopBarAndItsControlsTakeClicks() throws {
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 1000, height: 700),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable],
                              backing: .buffered, defer: false)
        let content = HitView(frame: CGRect(x: 0, y: 0, width: 1000, height: 700))
        window.contentView = content
        WindowChromeView.dress(window)
        WindowChromeView.placeLights(in: window, barHeight: ShellLayout.topBar)
        XCTAssertEqual(window.titleVisibility, .hidden)
        XCTAssertTrue(window.titlebarAppearsTransparent)
        XCTAssertTrue(window.styleMask.contains(.fullSizeContentView))
        XCTAssertEqual(content.frame.height, window.frame.height, "the content runs under the title bar")
        let close = try XCTUnwrap(window.standardWindowButton(.closeButton))
        let zoom = try XCTUnwrap(window.standardWindowButton(.zoomButton))
        let closeFrame = close.convert(close.bounds, to: nil)
        let height = try XCTUnwrap(window.contentView?.superview).bounds.height
        XCTAssertEqual(closeFrame.minX, WindowLights.left)
        XCTAssertEqual((height - closeFrame.midY).rounded(), ShellLayout.topBar / 2, "centred on the top bar")
        XCTAssertLessThan(zoom.convert(zoom.bounds, to: nil).maxX, WindowLights.room,
                          "the lights end before the bar's first button")
        // A press on the top bar past the lights reaches the window's content, not the title bar's view.
        let frameView = try XCTUnwrap(window.contentView?.superview)
        let hit = frameView.hitTest(NSPoint(x: 500, y: height - 28))
        XCTAssertTrue(hit === content || hit?.isDescendant(of: content) == true, "hit \(String(describing: hit))")
        let onClose = frameView.hitTest(NSPoint(x: closeFrame.midX, y: closeFrame.midY))
        XCTAssertTrue(onClose === close || onClose?.isDescendant(of: close) == true,
                      "the close button still takes clicks")
    }

    func testThePanesBarIsTallerOverAMessage() {
        // The mockup's `.pbar .grp` pads a message's bar to 70; the brief's and an empty pane's stay 52.
        XCTAssertEqual(MeadowPaneBar.height(message: true), 70)
        XCTAssertEqual(MeadowPaneBar.height(message: false), 52)
    }

    // MARK: What meets the window's top

    /// The first run's welcome over the mail window, as it opens with no account and at the size it opens at
    /// (MailMindApp's `defaultSize`): the scene runs to the window's top with the traffic lights over its sky (no band
    /// of paper over it), the sun or the moon still in it, the headline stands just below the lights' row, and a press
    /// on the sky moves the window.
    func testTheWelcomesSceneRunsToTheWindowsTopUnderTheLights() throws {
        for light in MeadowLight.allCases {
            let shown = try ShownInAWindow("window-welcome-\(light.rawValue)", light: light, size: Self.mailWindow) {
                Welcome(status: Self.firstRun(), kept: [])
                    .background { WindowChrome() }
            }
            defer { shown.close() }
            // The scene spans the window, and the wider it is the more of its sky is cropped away: at this width the
            // sun (a disc about 60 points across, at the window's top right) or the moon's crescent still shows.
            let corner = CGRect(x: 960, y: 0, width: 200, height: 80)
            switch light {
            case .day:
                XCTAssertGreaterThan(shown.points(in: corner, where: \.isSun), 900, "a third of the sun or more shows")
            case .night:
                XCTAssertGreaterThan(shown.points(in: corner, where: \.isMoon), 300, "half the moon or more shows")
            case .dusk:
                break
            }
            guard light == .day else { continue }
            // The Day sky is blue where paper is cream: along the window's top past the lights, it is sky.
            let edge = stride(from: 120.0, through: 520.0, by: 20.0).map { shown.color(x: $0, y: 3) }
            XCTAssertGreaterThan(edge.filter(\.isSky).count, edge.count / 2, "the scene reaches the window's top")
            // The headline's first ink is below the lights' row and close under it.
            let lightsEnd = shown.lightsEnd
            XCTAssertEqual(lightsEnd, 36, accuracy: 4, "the lights centred on the top bar's 56 points")
            let ink = try XCTUnwrap(shown.firstInkRow(from: lightsEnd, xs: 90...400), "the headline drew")
            XCTAssertGreaterThan(ink, lightsEnd + 12, "the headline clears the traffic lights")
            XCTAssertLessThanOrEqual(ink, lightsEnd + 26, "no wide band of sky over the headline")
            // The sky past the lights, where nothing else takes the press, moves the window.
            let top = shown.hit(x: 500, y: 12)
            XCTAssertTrue(top is WindowDragView, "a press at the top lands on \(String(describing: top))")
            XCTAssertTrue(shown.hit(x: 700, y: 120) is WindowDragView, "the sky lower down too")
        }
    }

    /// MailMind Help: its paper runs to the window's top, the traffic lights on it, and its bar moves the window.
    func testHelpsPaperRunsToTheWindowsTop() throws {
        let size = CGSize(width: HelpWindow.size.width, height: HelpWindow.size.height + Self.titlebarRoom)
        for light in MeadowLight.allCases {
            let shown = try ShownInAWindow("window-help-\(light.rawValue)", light: light, size: size) {
                HelpWindow(status: Self.firstRun())
            }
            defer { shown.close() }
            XCTAssertTrue(shown.hit(x: 300, y: 20) is WindowDragView, "its bar moves the window")
        }
    }

    // MARK: Windows SwiftUI's scenes make (#51)

    /// Settings' window as its scene makes it: in the toolbar style a Settings window takes, its title the place
    /// shown, and its title bar set up again by SwiftUI from the view's bars on each update (`sceneBridgingOptions`).
    /// Each place keeps Meadow's head: a clear title bar with no title and no line under it, the traffic lights on
    /// the head as the title changes under them, and the window's title still the place's, for the Window menu.
    func testSettingsKeepsMeadowsHeadAsSwiftUISetsItsTitleBarUp() throws {
        let status = Self.firstRun()
        status.settingsTab = .accounts
        let host = NSHostingController(rootView: SettingsWindow(status: status).meadowLight(.day))
        host.sceneBridgingOptions = .all
        let window = NSWindow(contentRect: CGRect(origin: .zero, size: SettingsWindow.size),
                              styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.toolbarStyle = .preference
        window.isReleasedWhenClosed = false
        window.contentViewController = host
        defer {
            window.contentViewController = nil
            window.close()
        }
        Self.settle()
        for place in [SettingsTab.general, .notifications, .ai, .accounts] {
            status.settingsTab = place
            Self.settle()
            XCTAssertEqual(window.title, place.title, "the window is named for its place")
            XCTAssertEqual(window.titleVisibility, .hidden, "no title over Meadow's head on \(place)")
            XCTAssertTrue(window.titlebarAppearsTransparent, "a clear title bar on \(place)")
            XCTAssertEqual(window.titlebarSeparatorStyle, .none, "no line under the title bar on \(place)")
            XCTAssertTrue(window.styleMask.contains(.fullSizeContentView), "the paper runs under the title bar")
            Self.assertLights(of: window, on: SettingsWindow.headHeight, "on \(place)")
        }
    }

    /// Settings' paper runs from the window's top to its foot: the scene sizes the window from the dialog's size and
    /// the title bar's room over it, and the paper takes that room too, rather than leaving a strip of nothing as
    /// tall as the title bar at the window's foot.
    func testSettingsPaperRunsToTheWindowsFoot() throws {
        let size = CGSize(width: SettingsWindow.size.width, height: SettingsWindow.size.height + Self.titlebarRoom)
        let shown = try ShownInAWindow("window-settings-day", light: .day, size: size) {
            SettingsWindow(status: Self.firstRun())
        }
        defer { shown.close() }
        for x in stride(from: 20.0, through: size.width - 20, by: 100) {
            XCTAssertTrue(shown.color(x: x, y: size.height - 2).isDayPaper, "paper at the foot, \(x) across")
            XCTAssertTrue(shown.color(x: x, y: 2).isDayPaper, "paper at the top, \(x) across")
        }
        XCTAssertTrue(shown.hit(x: 400, y: SettingsWindow.headHeight / 2) is WindowDragView, "the head moves the window")
    }

    /// Help's paper runs to the window's foot too, the window sized as its scene sizes it.
    func testHelpsPaperRunsToTheWindowsFoot() throws {
        let size = CGSize(width: HelpWindow.size.width, height: HelpWindow.size.height + Self.titlebarRoom)
        let shown = try ShownInAWindow("window-help-foot-day", light: .day, size: size) {
            HelpWindow(status: Self.firstRun())
        }
        defer { shown.close() }
        for x in stride(from: 20.0, through: size.width - 20, by: 100) {
            XCTAssertTrue(shown.color(x: x, y: size.height - 2).isDayPaper, "paper at the foot, \(x) across")
        }
    }

    /// A message's own window: a press anywhere on the bar its traffic lights stand on moves the window, as a title
    /// bar does, past the lights and across to its right edge.
    func testAMessageWindowsBarMovesTheWindowWhereverItIsPressed() throws {
        let shown = try ShownInAWindow("window-message-bar-day", light: .day, size: CGSize(width: 820, height: 300)) {
            VStack(spacing: 0) {
                MessageWindowBar()
                Color.clear
            }
            .ignoresSafeArea()
            .meadowWindowChrome(barHeight: MessageWindow.barHeight)
        }
        defer { shown.close() }
        for x in stride(from: 100.0, through: 800, by: 100) {
            for y in [6.0, MessageWindow.barHeight / 2, MessageWindow.barHeight - 2] {
                XCTAssertTrue(shown.hit(x: x, y: y) is WindowDragView,
                              "a press at \(x), \(y) lands on \(String(describing: shown.hit(x: x, y: y)))")
            }
        }
    }

    /// The room a titled window's title bar takes over its content, which a scene adds to the content's size.
    private static var titlebarRoom: CGFloat {
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 400, height: 300),
                              styleMask: [.titled, .closable, .fullSizeContentView], backing: .buffered, defer: true)
        window.isReleasedWhenClosed = false
        return window.frame.height - window.contentLayoutRect.height
    }

    /// Let SwiftUI update, AppKit lay the title bar out, and what waits on the main queue run.
    private static func settle() {
        for _ in 0..<3 { RunLoop.main.run(until: Date().addingTimeInterval(0.15)) }
    }

    /// The traffic lights stand at Meadow's places, centred on a head `bar` points high.
    private static func assertLights(of window: NSWindow, on bar: CGFloat, _ when: String,
                                     file: StaticString = #filePath, line: UInt = #line)
    {
        let kinds: [NSWindow.ButtonType] = [.closeButton, .miniaturizeButton, .zoomButton]
        let height = window.contentView?.superview?.bounds.height ?? window.frame.height
        for (index, kind) in kinds.enumerated() {
            guard let button = window.standardWindowButton(kind) else {
                return XCTFail("no \(kind) light", file: file, line: line)
            }
            let frame = button.convert(button.bounds, to: nil)
            XCTAssertEqual(frame.minX, WindowLights.left + CGFloat(index) * WindowLights.step,
                           "light \(index) at its place \(when)", file: file, line: line)
            XCTAssertEqual(height - frame.midY, bar / 2, accuracy: 1, "light \(index) centred on the head \(when)",
                           file: file, line: line)
        }
    }

    /// The mail window's size as it first opens (MailMindApp's `defaultSize`).
    private static let mailWindow = CGSize(width: 1180, height: 760)

    /// A status with no account yet, AI turned off: the first run's.
    private static func firstRun() -> AppStatus {
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        status.hold(digest: StatusFixtures.digest(accounts: [], aliases: [:], engine: "none", keyFrom: "",
                                                  off: "turned-off"),
                    preferences: StatusFixtures.preferences(aiEnabled: false),
                    now: StatusFixtures.at("2026-11-14T15:00:00Z"))
        return status
    }
}

/// A view in a window dressed as the app's windows are (`.windowStyle(.hiddenTitleBar)` and `WindowChrome`): titled,
/// the content under a clear title bar, so the view meets the title bar's room as it does on screen. The window is
/// drawn with its traffic lights, and written to `TEST_RUNNER_MEADOW_PICTURES`'s folder when one is given.
@MainActor
private struct ShownInAWindow {
    let window: NSWindow
    let frameView: NSView
    let bitmap: NSBitmapImageRep

    init(_ name: String, light: MeadowLight, size: CGSize, @ViewBuilder _ view: () -> some View) throws {
        _ = NSApplication.shared
        window = NSWindow(contentRect: CGRect(origin: .zero, size: size),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.appearance = NSAppearance(named: light.isDark ? .darkAqua : .aqua)
        let host = NSHostingView(rootView: view().meadowLight(light))
        host.sizingOptions = []
        window.contentView = host
        WindowChromeView.dress(window)
        window.setContentSize(size)
        frameView = try XCTUnwrap(host.superview)
        host.layoutSubtreeIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.5))
        host.layoutSubtreeIfNeeded()
        frameView.layoutSubtreeIfNeeded()
        window.displayIfNeeded()
        // From macOS 26 a scroll view under the title bar keeps an edge effect over its top (AppKit's scroll
        // pocket), which shows nothing until the content scrolls under the bar; it draws through portal layers, which
        // a layer's render(in:) can't draw, and with it in the tree nothing is drawn at all. It is set aside while the
        // picture is drawn, and back for the presses.
        let pockets = Self.views(in: host, named: "ScrollPocket").filter { !$0.isHidden }
        for pocket in pockets { pocket.isHidden = true }
        bitmap = try XCTUnwrap(Self.drawn(host, lights: Self.lights(of: window)))
        for pocket in pockets { pocket.isHidden = false }
        if let folder = ProcessInfo.processInfo.environment["MEADOW_PICTURES"].map({ URL(fileURLWithPath: $0) }) {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
                .write(to: folder.appendingPathComponent("\(name).png"))
        }
    }

    /// Every view under `view` whose class's name holds `named`.
    private static func views(in view: NSView, named: String) -> [NSView] {
        (String(describing: type(of: view)).contains(named) ? [view] : []) + view.subviews.flatMap {
            views(in: $0, named: named)
        }
    }

    /// The window's three buttons.
    private static func lights(of window: NSWindow) -> [NSButton] {
        [.closeButton, .miniaturizeButton, .zoomButton].compactMap { window.standardWindowButton($0) }
    }

    /// The content's layers drawn at twice its size, as a window on screen shows them, with the traffic lights, which
    /// AppKit draws in the title bar's own views, over them where they stand.
    private static func drawn(_ host: NSView, lights: [NSButton]) -> NSBitmapImageRep? {
        let scale: CGFloat = 2
        let size = host.bounds.size
        guard let layer = host.layer, let space = CGColorSpace(name: CGColorSpace.sRGB),
              let context = CGContext(data: nil, width: Int(size.width * scale), height: Int(size.height * scale),
                                      bitsPerComponent: 8, bytesPerRow: 0, space: space,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        context.scaleBy(x: scale, y: scale)
        context.saveGState()
        // A layer draws top down; a bitmap's rows go bottom up.
        context.translateBy(x: 0, y: size.height)
        context.scaleBy(x: 1, y: -1)
        layer.render(in: context)
        context.restoreGState()
        for light in lights {
            guard let rep = light.bitmapImageRepForCachingDisplay(in: light.bounds) else { continue }
            light.cacheDisplay(in: light.bounds, to: rep)
            // The window's points run up from its bottom left, as the context's do.
            if let image = rep.cgImage { context.draw(image, in: light.convert(light.bounds, to: nil)) }
        }
        return context.makeImage().map { NSBitmapImageRep(cgImage: $0) }
    }

    func close() {
        window.contentView = nil
        window.close()
    }

    /// How far down the window the traffic lights' row ends, in points.
    var lightsEnd: CGFloat {
        let lights = Self.lights(of: window).map { $0.convert($0.bounds, to: nil) }
        return frameView.bounds.height - (lights.map(\.minY).min() ?? frameView.bounds.height)
    }

    /// What a press at a point, in points from the window's top left, lands on.
    func hit(x: CGFloat, y: CGFloat) -> NSView? {
        frameView.hitTest(NSPoint(x: x, y: frameView.bounds.height - y))
    }

    /// The colour drawn at a point, in points from the window's top left.
    func color(x: CGFloat, y: CGFloat) -> Drawn {
        let scale = CGFloat(bitmap.pixelsWide) / frameView.bounds.width
        let color = bitmap.colorAt(x: Int(x * scale), y: Int(y * scale))?.usingColorSpace(.sRGB)
        return Drawn(red: color?.redComponent ?? 0, green: color?.greenComponent ?? 0, blue: color?.blueComponent ?? 0)
    }

    /// How many of the whole points in `rect`, in points from the window's top left, are drawn as `matches` says.
    func points(in rect: CGRect, where matches: (Drawn) -> Bool) -> Int {
        var count = 0
        for y in stride(from: rect.minY, to: rect.maxY, by: 1) {
            for x in stride(from: rect.minX, to: rect.maxX, by: 1) where matches(color(x: x, y: y)) { count += 1 }
        }
        return count
    }

    /// The first row from `y` down, in points, where any point across `xs` is ink.
    func firstInkRow(from y: CGFloat, xs: ClosedRange<CGFloat>) -> CGFloat? {
        for row in stride(from: y, to: frameView.bounds.height, by: 1) {
            for x in stride(from: xs.lowerBound, through: xs.upperBound, by: 1) where color(x: x, y: row).isInk {
                return row
            }
        }
        return nil
    }

    struct Drawn {
        let red: CGFloat
        let green: CGFloat
        let blue: CGFloat

        /// The Day sky: blue over its red, where the paper is warm.
        var isSky: Bool { blue > red + 0.06 }
        /// The Day sun: bright and warm, where its glow is less bright and the clouds are as blue as they are red.
        var isSun: Bool { red > 0.96 && red > blue + 0.06 }
        /// The Night moon: bright, where the night sky, its clouds and the moon's glow are dark.
        var isMoon: Bool { 0.2126 * red + 0.7152 * green + 0.0722 * blue > 0.7 }
        /// Day's ink, dark where the sky and the paper are light.
        var isInk: Bool { 0.2126 * red + 0.7152 * green + 0.0722 * blue < 0.35 }
        /// Day's paper: light and warm, where nothing drawn is black and the window's own grey is cool.
        var isDayPaper: Bool { 0.2126 * red + 0.7152 * green + 0.0722 * blue > 0.9 && red >= blue }
    }
}

/// A content view that takes every click, as the top bar's buttons do.
private final class HitView: NSView {
    override func hitTest(_ point: NSPoint) -> NSView? {
        frame.contains(point) ? self : nil
    }
}
