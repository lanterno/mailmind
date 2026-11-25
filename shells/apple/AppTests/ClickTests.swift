import AppKit
import Carbon.HIToolbox
import XCTest
@testable import MailMind

/// The kit's clicks in a key window of an active app, which the window routes itself: a plain `NSButton` takes one,
/// as it never does from the unhosted tests, whose windows are never key.
@MainActor
final class ClickTests: HostedCase {
    private final class Count: NSObject {
        var clicks = 0
        @objc func clicked(_ sender: Any?) { clicks += 1 }
    }

    func testAPlainButtonTakesAClickInTheKeyWindow() async throws {
        let content = try XCTUnwrap(window.contentView)
        let count = Count()
        let button = NSButton(title: "Press", target: count, action: #selector(Count.clicked(_:)))
        button.frame = NSRect(x: content.bounds.midX - 60, y: content.bounds.midY - 16, width: 120, height: 32)
        content.addSubview(button)
        defer { button.removeFromSuperview() }
        try await settle()
        XCTAssertTrue(RealMouse.view(at: content.convert(NSPoint(x: button.frame.midX, y: button.frame.midY), to: nil),
                                     in: window) === button, "the button is what a click there lands on")
        XCTAssertTrue(NSApp.isActive && window.isKeyWindow, "the window routes the click itself")
        try RealMouse.click(button)
        try await until("the button pressed") { count.clicks == 1 }
        try await settle()
        XCTAssertEqual(count.clicks, 1, "one click, one press")
    }

    /// The search box's ⌘K button, SwiftUI's, opens the palette.
    func testASwiftUIButtonTakesAClickInTheKeyWindow() async throws {
        Find.assistive(true)
        defer { Find.assistive(false) }
        try await settle()
        XCTAssertFalse(mailbox.paletteOpen)
        try RealMouse.click(try Find.role(.button, "Command palette", in: window), in: window)
        try await until("the palette open") { self.mailbox.paletteOpen }
        try await settle()
        try RealKeys.press(UInt16(kVK_Escape), layout: .us, in: NSApp.keyWindow ?? window)
        try await until("the palette closed") { !self.mailbox.paletteOpen }
    }
}
