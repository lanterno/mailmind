import AppKit
import Carbon.HIToolbox
import XCTest
@testable import MailMind

/// Where the keyboard is, and where a key goes, in the real app with its mail window key: as it opens, after Settings
/// and the palette come and go, and under Arabic, where the Edit menu AppKit adds would take e and d.
@MainActor
final class KeyboardTests: HostedCase {
    func testTheKeyboardIsInTheListAsTheAppOpens() throws {
        XCTAssertTrue(NSApp.isActive)
        XCTAssertTrue(window.isKeyWindow)
        XCTAssertNil(keyboardIsSomewhere(window))
        XCTAssertTrue(window.firstResponder === table, "the list has it, as Mail's does: \(String(describing: window.firstResponder))")
    }

    /// Settings opened with ⌘, and closed with ⌘W hands the keyboard back to the mail window, key again.
    func testTheKeyboardComesBackAfterSettings() async throws {
        try RealKeys.press(UInt16(kVK_ANSI_Comma), held: .command, layout: .us, in: window)
        XCTAssertEqual(RealKeys.route, .app)
        try await until("Settings key") { NSApp.keyWindow != nil && NSApp.keyWindow !== self.window }
        let settings = try XCTUnwrap(NSApp.keyWindow)
        try await settle()
        let seen = "key: \(Self.describe(settings)); windows: \(NSApp.windows.filter(\.isVisible).map(Self.describe))"
        XCTAssertTrue(try menuItem(["File", "Close"]).enabled, "File › Close, for Settings: \(seen)")
        try RealKeys.press(UInt16(kVK_ANSI_W), held: .command, layout: .us, in: settings)
        XCTAssertEqual(RealKeys.route, .app)
        do {
            try await until("Settings closed") { !settings.isVisible }
        } catch {
            throw KitError("\(error): \(seen); key now \(NSApp.keyWindow.map(Self.describe) ?? "none")")
        }
        try await until("the mail window key again") { self.window.isKeyWindow }
        try await until("the keyboard somewhere in it") { keyboardIsSomewhere(self.window) == nil }
    }

    /// The guide, a sheet on the mail window opened from Help › Show Me Around and skipped with Esc, hands the keyboard
    /// back to the window, key again.
    func testTheKeyboardComesBackAfterTheGuidesSheet() async throws {
        try chooseMenuItem(["Help", "Show Me Around"])
        try await until("the guide's sheet key") { NSApp.keyWindow?.sheetParent === self.window }
        try RealKeys.press(UInt16(kVK_Escape), layout: .us, in: try XCTUnwrap(NSApp.keyWindow))
        XCTAssertEqual(RealKeys.route, .app)
        try await until("the guide gone") { !AppStatus.shared.guiding && self.window.attachedSheet == nil }
        try await until("the mail window key again") { self.window.isKeyWindow }
        try await until("the keyboard somewhere in it") { keyboardIsSomewhere(self.window) == nil }
    }

    /// The keys' legend, opened by its SwiftUI button in the top bar and closed with Esc, leaves the keyboard in the
    /// window, and the list's keys working.
    func testTheKeyboardComesBackAfterTheLegend() async throws {
        Find.assistive(true)
        defer { Find.assistive(false) }
        try await settle()
        try RealMouse.click(try Find.role(.button, "Keyboard shortcuts", in: window), in: window)
        try await until("the legend up") { AppStatus.shared.showingKeys }
        try await settle()
        try RealKeys.press(UInt16(kVK_Escape), layout: .us, in: window)
        try await until("the legend gone") { !AppStatus.shared.showingKeys }
        try await until("the keyboard somewhere in the window") { keyboardIsSomewhere(self.window) == nil }
        XCTAssertTrue(window.isKeyWindow)
    }

    /// Under Arabic, e and d in the list archive and delete, as the list's single keys, and never reach Edit's Emoji &
    /// Symbols and Start Dictation, the real items AppKit adds to the Edit menu, which a menu matches on e and d alone.
    func testUnderArabicEAndDReachTheListAndNotTheEditMenu() async throws {
        let close = try readUnderArabic()
        defer { close() }
        let edit = try XCTUnwrap(NSApp.mainMenu?.item(withTitle: "Edit")?.submenu, "no Edit menu")
        let dictation = try XCTUnwrap(edit.items.first { $0.action == NSSelectorFromString("startDictation:") },
                                      "no Start Dictation in Edit: \(edit.items.map(\.title))")
        let emoji = try XCTUnwrap(
            edit.items.first { $0.action == #selector(NSApplication.orderFrontCharacterPalette(_:)) },
            "no Emoji & Symbols in Edit: \(edit.items.map(\.title))")
        // What they would do is said to `taken`, so a key that reached them shows and no palette or dictation opens.
        let taken = RealKeys.Taken()
        let targets = (dictation.target, emoji.target)
        dictation.target = taken
        emoji.target = taken
        defer {
            dictation.target = targets.0
            emoji.target = targets.1
        }

        for (code, said) in [(kVK_ANSI_E, "Archived"), (kVK_ANSI_D, "Trashed")] {
            try await clickFirstRow()
            XCTAssertTrue(window.firstResponder === table)
            mailbox.dismissToast()
            try RealKeys.press(UInt16(code), layout: .arabic, in: window)
            XCTAssertEqual(RealKeys.route, .app)
            try await until("\(said) said") { self.mailbox.toast != nil }
            let toast = try XCTUnwrap(mailbox.toast)
            XCTAssertTrue(toast.text.hasPrefix(said), "\(said): \(toast.text)")
            XCTAssertEqual(taken.keys, [], "the Edit menu took it")
            try await settle()
        }
    }

    /// Single keys read as typed on Arabic, with US as the ASCII-capable layout beside it, until the returned close:
    /// the app's seam, never the Mac's own input source.
    private func readUnderArabic() throws -> () -> Void {
        let arabic = try KeyboardLayout.arabic.source()
        let us = try KeyboardLayout.us.source()
        let was = SingleKey.layouts
        SingleKey.layouts = { (arabic, us) }
        return { SingleKey.layouts = was }
    }
}
