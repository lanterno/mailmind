import AppKit
import XCTest

/// Meadow's window (#44): the traffic lights on the top bar, the content under the title bar, and the pane's bar's
/// height with and without a message.
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
}

/// A content view that takes every click, as the top bar's buttons do.
private final class HitView: NSView {
    override func hitTest(_ point: NSPoint) -> NSView? {
        frame.contains(point) ? self : nil
    }
}
