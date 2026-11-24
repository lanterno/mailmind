import AppKit
import XCTest

/// Keys pressed as a person presses them, past the menu bar as the app has one: the window key, the menu bar holding
/// the Edit menu's items AppKit adds to every app's (Start Dictation on fn-D, Emoji & Symbols on fn-E), and each key
/// posted into the app's queue and sent the way AppKit sends one (`MailFrameTests`' `press` does the same).
@MainActor
enum RealKeys {
    /// What the Edit menu's two items did, as the app's own would: a test's process has nothing that answers them, and a
    /// menu offers no key to an item nothing answers.
    final class Taken: NSObject {
        private(set) var keys: [String] = []

        @objc func startDictation(_ sender: Any?) { keys.append("d") }
        @objc func orderFrontCharacterPalette(_ sender: Any?) { keys.append("e") }
    }

    /// The Edit menu's items as AppKit adds them, their actions answered by `taken`: a menu matches these on d and e
    /// with nothing held.
    static func editMenu(_ taken: Taken) -> NSMenu {
        let edit = NSMenu(title: "Edit")
        edit.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        let dictation = NSMenuItem(title: "Start Dictation…", action: #selector(Taken.startDictation(_:)),
                                   keyEquivalent: "d")
        let emoji = NSMenuItem(title: "Emoji & Symbols", action: #selector(Taken.orderFrontCharacterPalette(_:)),
                               keyEquivalent: "e")
        for item in [dictation, emoji] {
            item.keyEquivalentModifierMask = .function
            item.target = taken
            edit.addItem(item)
        }
        return edit
    }

    /// The app's menu bar with an Edit menu in it, until `restore` puts the old one back; `taken` says which of its
    /// items a key reached.
    static func withEditMenu() -> (taken: Taken, restore: () -> Void) {
        let app = NSApplication.shared
        let old = app.mainMenu
        let taken = Taken()
        let bar = NSMenu()
        bar.addItem(NSMenuItem(title: "MailMind", action: nil, keyEquivalent: ""))
        let edit = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        edit.submenu = editMenu(taken)
        bar.addItem(edit)
        app.mainMenu = bar
        return (taken, { app.mainMenu = old })
    }

    /// `window` made key, far off the screen and see-through, as a test's window opens.
    static func key(_ window: NSWindow) {
        window.alphaValue = 0
        window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
        window.makeKeyAndOrderFront(nil)
    }

    /// A key pressed in `window`: posted into the app's queue and taken out as the app's run takes it, offered as a
    /// key equivalent to the window's views where ⌘, ⌃ or fn is held, then sent through the app to the window.
    static func press(_ typed: String, _ code: UInt16, held: NSEvent.ModifierFlags = [], in window: NSWindow) throws {
        NSApp.postEvent(try XCTUnwrap(NSEvent.keyEvent(
            with: .keyDown, location: .zero, modifierFlags: held, timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: window.windowNumber, context: nil, characters: typed, charactersIgnoringModifiers: typed,
            isARepeat: false, keyCode: code)), atStart: true)
        let key = try XCTUnwrap(NSApp.nextEvent(matching: .keyDown, until: .distantFuture, inMode: .default,
                                                dequeue: true))
        if !key.modifierFlags.isDisjoint(with: [.command, .control, .function]), window.performKeyEquivalent(with: key) {
            return
        }
        NSApp.sendEvent(key)
    }
}
