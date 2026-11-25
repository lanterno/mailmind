import AppKit
import SwiftUI
import XCTest

/// Send later's own date and time: while the field has the keyboard, digits, the arrows and Return are the field's.
/// A digit changes the time rather than pick a preset, an arrow steps it, and Return schedules the time typed.
@MainActor
final class SendLaterKeysTests: XCTestCase {
    private var host: FakeHost!
    private var draft: Composer!

    override func setUp() async throws {
        _ = NSApplication.shared
        host = FakeHost()
        var seed = ComposeSeed()
        seed.to = ["bo@example.test"]
        seed.subject = "Plans"
        draft = Composer(seed, host: host)
        draft.setBody("Later", selection: NSRange(location: 5, length: 0))
    }

    override func tearDown() async throws {
        draft.close(force: true)
    }

    private static func views<V: NSView>(_ kind: V.Type, in view: NSView) -> [V] {
        ((view as? V).map { [$0] } ?? []) + view.subviews.flatMap { views(kind, in: $0) }
    }

    func testTheOwnTimeFieldKeepsItsDigitsArrowsAndReturn() async throws {
        let window = NSWindow(contentRect: NSRect(x: -20_000, y: -20_000, width: 400, height: 300), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.alphaValue = 0
        let anchor = NSView(frame: NSRect(x: 20, y: 20, width: 40, height: 20))
        window.contentView = NSView(frame: NSRect(x: 0, y: 0, width: 400, height: 300))
        window.contentView?.addSubview(anchor)
        window.makeKeyAndOrderFront(nil)
        defer {
            window.orderOut(nil)
            window.contentView = nil
        }
        let popover = NSPopover()
        popover.contentViewController = NSHostingController(rootView: LaterChoices(composer: draft).meadowLight(.day))
        popover.show(relativeTo: anchor.bounds, of: anchor, preferredEdge: .maxY)
        defer { popover.close() }
        let shown = try XCTUnwrap(popover.contentViewController?.view.window)
        await until { shown.isKeyWindow || shown.firstResponder !== shown }
        try await Task.sleep(for: .milliseconds(150))

        // The date and time of one's own takes the keyboard, as a click or Tab gives it.
        let picker = try XCTUnwrap(Self.views(NSDatePicker.self, in: shown.contentView!).first, "the own time's field")
        XCTAssertTrue(shown.makeFirstResponder(picker))
        try await Task.sleep(for: .milliseconds(150))

        try RealKeys.press("2", 19, in: shown)
        try RealKeys.press("1", 18, in: shown)
        try await Task.sleep(for: .milliseconds(150))
        XCTAssertTrue(host.sent.isEmpty, "a digit in the field picks no preset: \(host.sent.map(\.after))")
        XCTAssertTrue(shown.firstResponder === picker, "the field still has the keyboard")

        // A time days ahead, as the field's own edits set one, whatever the digits made of it.
        picker.dateValue = Date().addingTimeInterval(3 * 86_400)
        picker.sendAction(picker.action, to: picker.target)
        let before = picker.dateValue
        try RealKeys.press(String(UnicodeScalar(NSUpArrowFunctionKey)!), 126, in: shown)
        try await Task.sleep(for: .milliseconds(150))
        XCTAssertTrue(host.sent.isEmpty)
        XCTAssertGreaterThan(picker.dateValue, before, "↑ steps the field's time")

        // Return schedules the time typed, not the preset the keyboard was last on.
        let typed = Int(picker.dateValue.timeIntervalSince1970)
        try RealKeys.press("\r", 36, in: shown)
        await until { !host.sent.isEmpty }
        XCTAssertEqual(host.sent.first?.after, typed, "the time typed")
    }
}
