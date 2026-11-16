import WebKit
import XCTest

/// Find in Message: a word looked for in each body the reader shows, from the one the last match was in, round to the
/// first; not found said as such; a frame gone, or off the window, is passed over.
@MainActor
final class ReaderFindTests: XCTestCase {
    private var window: NSWindow!

    override func setUp() async throws {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 600, height: 800), styleMask: [.borderless],
                          backing: .buffered, defer: true)
        // Closed by the test, and let go by it: AppKit mustn't release it as well.
        window.isReleasedWhenClosed = false
    }

    override func tearDown() async throws {
        window.close()
        window = nil
    }

    /// A frame on the window with `html` loaded, as the reader's frames are.
    private func frame(_ html: String, at y: CGFloat) async throws -> WKWebView {
        let view = WKWebView(frame: NSRect(x: 0, y: y, width: 600, height: 300))
        window.contentView?.addSubview(view)
        let loaded = Loaded()
        view.navigationDelegate = loaded
        view.loadHTMLString(html, baseURL: nil)
        await until { loaded.done }
        return view
    }

    @MainActor
    private final class Loaded: NSObject, WKNavigationDelegate {
        var done = false
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { done = true }
    }

    func testAWordIsFoundInWhicheverFrameHoldsIt() async throws {
        let first = try await frame("<p>Your order has shipped.</p>", at: 0)
        let second = try await frame("<p>The ticket number is LH-2041.</p>", at: 400)
        let find = ReaderFind()
        find.add(first)
        find.add(second)
        find.add(first)
        XCTAssertEqual(find.shown.count, 2, "a frame joins once")

        find.query = "ticket"
        await find.next()
        XCTAssertEqual(find.found, true)
        find.query = "SHIPPED"
        await find.next()
        XCTAssertEqual(find.found, true, "case doesn't matter")
        find.query = "refund"
        await find.next()
        XCTAssertEqual(find.found, false)
        find.query = "  "
        await find.next()
        XCTAssertNil(find.found, "nothing typed, nothing said")
    }

    func testAFrameOffTheWindowOrGoneIsPassedOver() async throws {
        let shown = try await frame("<p>Gate B12</p>", at: 0)
        let find = ReaderFind()
        find.add(shown)
        let away = WKWebView(frame: NSRect(x: 0, y: 0, width: 100, height: 100))
        find.add(away)
        XCTAssertEqual(find.shown, [shown], "a frame on no window isn't looked in")
        find.remove(shown)
        find.query = "Gate"
        await find.next()
        XCTAssertEqual(find.found, false)
    }

    func testShowingPutsTheCursorInTheFieldAndClosingForgetsTheAnswer() async throws {
        let find = ReaderFind()
        find.show()
        XCTAssertTrue(find.open)
        XCTAssertTrue(find.focusField)
        find.query = "x"
        await find.next()
        XCTAssertEqual(find.found, false)
        find.close()
        XCTAssertFalse(find.open)
        XCTAssertNil(find.found)
    }
}
