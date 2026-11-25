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

    /// A plain `NSButton`, which takes a click through the window's gesture recognizers: only a key window's own
    /// routing reaches them. It stands in a window of its own over the mail window, since SwiftUI's hosting view
    /// takes no AppKit view put into it; that window is key in the active app, as the mail window was.
    func testAPlainButtonTakesAClickInTheKeyWindow() async throws {
        let panel = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 320, height: 160), styleMask: [.titled, .closable],
                             backing: .buffered, defer: false)
        panel.isReleasedWhenClosed = false
        panel.contentView = NSView(frame: NSRect(x: 0, y: 0, width: 320, height: 160))
        let count = Count()
        let button = NSButton(title: "Press", target: count, action: #selector(Count.clicked(_:)))
        button.frame = NSRect(x: 100, y: 64, width: 120, height: 32)
        panel.contentView?.addSubview(button)
        panel.center()
        panel.makeKeyAndOrderFront(nil)
        defer {
            panel.close()
            window.makeKeyAndOrderFront(nil)
        }
        try await until("its window key") { NSApp.isActive && panel.isKeyWindow }
        try await settle()
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
