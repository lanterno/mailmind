import AppKit

/// Keys pressed as a person presses them, past the menu bar as the app has one: the window key, the menu bar holding
/// the Edit menu's items AppKit adds to every app's (Start Dictation on fn-D, Emoji & Symbols on fn-E), and each key
/// posted into the app's queue and sent the way AppKit sends one.
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

    /// A key-down as AppKit hands one over: its characters, its key code, what was held. A function key's (the arrows,
    /// Page Up and Down, Home and End) carries the flags AppKit gives one: fn, and the numeric pad for an arrow.
    static func event(_ typed: String, _ code: UInt16, held: NSEvent.ModifierFlags = [], in window: NSWindow? = nil)
        throws -> NSEvent
    {
        try event(typed, ignoringModifiers: typed, code, held: held, in: window)
    }

    static func event(_ typed: String, ignoringModifiers: String, _ code: UInt16, held: NSEvent.ModifierFlags = [],
                      in window: NSWindow? = nil) throws -> NSEvent
    {
        let scalar = ignoringModifiers.unicodeScalars.first.map { Int($0.value) } ?? 0
        var flags = held
        if (0xF700...0xF8FF).contains(scalar) { flags.insert(.function) }
        if (NSUpArrowFunctionKey...NSRightArrowFunctionKey).contains(scalar) { flags.insert(.numericPad) }
        guard let event = NSEvent.keyEvent(
            with: .keyDown, location: .zero, modifierFlags: flags, timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: window?.windowNumber ?? 0, context: nil, characters: typed,
            charactersIgnoringModifiers: ignoringModifiers, isARepeat: false, keyCode: code)
        else { throw KitError("AppKit made no key-down for key \(code)") }
        return event
    }

    /// A key as the window server hands it to AppKit: the key's code with what is held, its characters worked out by
    /// the Mac's keyboard layout. An `NSEvent` made with `keyEvent(with:…)` has no layout behind it, and AppKit's
    /// menus match it as no keyboard would: ⌘F fires ⇧⌘F's Forward and ⇧⌘F nothing, as a test tool's keys once did.
    static func event(_ code: CGKeyCode, _ held: CGEventFlags) throws -> NSEvent {
        guard let made = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true) else {
            throw KitError("Core Graphics made no key-down for key \(code)")
        }
        made.flags = held
        guard let event = NSEvent(cgEvent: made) else { throw KitError("AppKit read no key-down for key \(code)") }
        return event
    }

    /// A key pressed in `window` as a person presses one. It is posted into the app's queue and taken out as the app's
    /// run takes it, which makes it the app's current event (WebKit expects that), then goes the way AppKit sends a
    /// key-down in the key window: offered first as a key equivalent to the window's views, as one is with ⌘ or ⌃
    /// held or a function key's, then, if nothing took it, through the app (`NSApp.sendEvent`) to the window's first
    /// responder, and back through it should WebKit give the key back. The unhosted tests' app is never active, so
    /// AppKit would skip the first step: the kit takes it for AppKit, since making the app active would take the
    /// keyboard from the Mac's person. The app sends a key to its key window, so where `window` isn't key (another
    /// test left its own key) the key goes to `window` itself. In the hosted tests' active app, with `window` key,
    /// AppKit does it all (`route`).
    static func press(_ typed: String, _ code: UInt16, held: NSEvent.ModifierFlags = [], in window: NSWindow) throws {
        try press(try event(typed, code, held: held, in: window), in: window)
    }

    /// How the last key pressed went: through the app as AppKit sends one, or by the kit's own steps.
    enum Route: Equatable, Sendable {
        /// `NSApp.sendEvent` alone, in an active app with the window key: AppKit offered it to the menu bar and the
        /// window, as it does a person's key.
        case app
        /// The kit offered it to the window's views as a key equivalent, which took it.
        case keyEquivalent
        /// The kit sent it to the window, or through the app to a key window of an app that isn't active.
        case window
    }

    /// How the last key `press` pressed went: the hosted tests hold that theirs went through the app.
    private(set) static var route: Route?

    /// `key` posted, taken out and sent in `window`, as `press` sends one. In an active app whose key window is
    /// `window`, as the hosted tests' is, AppKit routes it whole: `NSApp.sendEvent` offers it to the menu bar and the
    /// key window's views first, then to the first responder.
    static func press(_ key: NSEvent, in window: NSWindow) throws {
        NSApp.postEvent(key, atStart: true)
        guard let taken = NSApp.nextEvent(matching: .keyDown, until: Date(timeIntervalSinceNow: 5), inMode: .default,
                                          dequeue: true)
        else { throw KitError("the key posted never came out of the app's queue") }
        guard NSApp.currentEvent === taken else { throw KitError("the key isn't the app's current event") }
        if NSApp.isActive, NSApp.keyWindow === window {
            route = .app
            NSApp.sendEvent(taken)
            return
        }
        if !taken.modifierFlags.isDisjoint(with: [.command, .control, .function]),
           window.performKeyEquivalent(with: taken)
        {
            route = .keyEquivalent
            return
        }
        route = .window
        if NSApp.keyWindow === window { NSApp.sendEvent(taken) } else { window.sendEvent(taken) }
    }
}
