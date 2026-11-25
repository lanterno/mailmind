import AppKit
import Carbon.HIToolbox
import XCTest
@testable import MailMind

/// The real menu bar, as the app's `Commands` build it and AppKit validates it with the mail window key: what the
/// unhosted tests can only build a stand-in of and read through a window that is never key.
@MainActor
final class MenuBarTests: HostedCase {
    func testTheMenuBarHasTheAppsMenus() throws {
        let titles = try XCTUnwrap(NSApp.mainMenu).items.map(\.title)
        for menu in ["File", "Edit", "View", "Mailbox", "Message", "Window", "Help"] {
            XCTAssertTrue(titles.contains(menu), "no \(menu) in the menu bar: \(titles)")
        }
        XCTAssertEqual(try menuItem(["Mailbox", "Inbox"]).title, "Inbox")
        XCTAssertEqual(try menuItem(["File", "New Message"]).title, "New Message")
    }

    /// Message › Archive waits for a row, and Mailbox › Command Palette… is there for the mail window: each as SwiftUI
    /// set it from the key window's focused values, which only a key window hands the menus.
    func testAnItemIsEnabledAsTheKeyWindowSays() async throws {
        XCTAssertTrue(NSApp.keyWindow === window)
        XCTAssertTrue(try menuItem(["Mailbox", "Command Palette…"]).enabled, "the key window's mailbox")
        XCTAssertTrue(try menuItem(["Mailbox", "All Inboxes"]).enabled)
        XCTAssertFalse(try menuItem(["Message", "Archive"]).enabled, "nothing chosen yet")
        XCTAssertFalse(try menuItem(["Message", "Reply"]).enabled, "no message to reply to")
        try await clickFirstRow()
        XCTAssertTrue(try menuItem(["Message", "Archive"]).enabled, "a row chosen")
        XCTAssertTrue(try menuItem(["Message", "Reply"]).enabled, "its message open")
        mailbox.chose([])
        try await settle()
        XCTAssertFalse(try menuItem(["Message", "Archive"]).enabled, "none chosen again")
    }

    /// ⌘K, pressed in the key window, reaches Mailbox › Command Palette… through AppKit's own routing, and Esc closes
    /// the palette with the keyboard back in the list.
    func testCommandKOpensThePaletteThroughTheMenuBar() async throws {
        try RealKeys.press(UInt16(kVK_ANSI_K), held: .command, layout: .us, in: window)
        XCTAssertEqual(RealKeys.route, .app, "the key went through NSApp.sendEvent, not the kit's own steps")
        try await until("the palette open") { self.mailbox.paletteOpen }
        try await settle()
        try RealKeys.press(UInt16(kVK_Escape), layout: .us, in: NSApp.keyWindow ?? window)
        try await until("the palette closed") { !self.mailbox.paletteOpen }
        try await until("the keyboard back in the window") { keyboardIsSomewhere(self.window) == nil }
        XCTAssertTrue(window.isKeyWindow)
    }

    /// ⇧⌘L, Message › Star, written lowercase with ⇧ as SwiftUI writes it: AppKit matches such an item only on a key
    /// the window server made, read under the Mac's layout, and only in its own routing.
    func testShiftCommandLStarsTheRowChosen() async throws {
        try await clickFirstRow()
        let star = try (try? menuItem(["Message", "Star"])) ?? menuItem(["Message", "Unstar"])
        XCTAssertTrue(star.enabled, "\(star.title) is there for the row chosen")
        mailbox.dismissToast()
        let key = try RealKeys.event(CGKeyCode(kVK_ANSI_L), [.maskCommand, .maskShift])
        try RealKeys.press(key, in: window)
        XCTAssertEqual(RealKeys.route, .app)
        try await until("Starred or Unstarred said") { self.mailbox.toast != nil }
        let said = try XCTUnwrap(mailbox.toast)
        XCTAssertFalse(said.refused, said.text)
        XCTAssertTrue(["Starred", "Unstarred"].contains(said.text), said.text)
    }
}
