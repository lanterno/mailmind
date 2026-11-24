#if os(macOS)
import AppKit
import Carbon.HIToolbox
import SwiftUI

/// The key a single-key shortcut names, whatever the keyboard's layout, as Apple's apps read one under a layout whose
/// letters aren't Latin: what the key typed where that is ASCII, else the key's own character on the Mac's
/// ASCII-capable layout. Under Arabic the key that types "ي" is d, so d deletes there as it does under ABC. Every
/// single key matches through it: the list's, a message's own window's, the gate's Y and N, the legend's ?, a snooze's
/// and Send later's digits, and g's chords. Only the matching changes: a field still gets what was typed.
@MainActor
enum SingleKey {
    /// The layouts it reads: the one keys are typed on now, and the ASCII-capable one macOS keeps beside it (the one in
    /// use, where it is ASCII-capable). A test names its own and never changes the Mac's.
    static var layouts: () -> (typing: TISInputSource?, ascii: TISInputSource?) = {
        (TISCopyCurrentKeyboardLayoutInputSource()?.takeRetainedValue(),
         TISCopyCurrentASCIICapableKeyboardLayoutInputSource()?.takeRetainedValue())
    }

    /// The character `event`'s key names, ⇧ making it the shifted one (# and ?): what it typed, where that is ASCII or
    /// a function key's (the arrows, Page Up), else the key on the ASCII-capable layout. Nil for a key that types
    /// nothing.
    static func typed(_ event: NSEvent) -> Character? {
        let typed = event.charactersIgnoringModifiers ?? ""
        if let key = plain(typed) { return key }
        return translated(event.keyCode, shift: event.modifierFlags.contains(.shift), on: layouts().ascii)
            ?? typed.first
    }

    /// The same for a SwiftUI key press, which carries no key code: the key that types its characters on the layout
    /// in use, read on the ASCII-capable one.
    static func typed(_ press: KeyPress) -> Character? {
        if let key = plain(press.characters) { return key }
        let shift = press.modifiers.contains(.shift)
        let (typing, ascii) = layouts()
        guard let code = keyCode(typing: press.characters, shift: shift, on: typing) else {
            return press.characters.first
        }
        return translated(code, shift: shift, on: ascii) ?? press.characters.first
    }

    /// `typed` as it is, when it is one ASCII character or a function key's.
    private static func plain(_ typed: String) -> Character? {
        guard typed.count == 1, let key = typed.first,
              key.unicodeScalars.allSatisfy({ $0.value < 0x80 || (0xF700...0xF8FF).contains($0.value) }) else {
            return nil
        }
        return key
    }

    /// The ASCII character key `code` types on `layout`, with ⇧ or without.
    private static func translated(_ code: UInt16, shift: Bool, on layout: TISInputSource?) -> Character? {
        guard let typed = characters(code, shift: shift, on: layout), typed.count == 1, let key = typed.first,
              key.unicodeScalars.allSatisfy({ $0.value > 0x20 && $0.value < 0x7F }) else { return nil }
        return key
    }

    /// The key that types `characters` on `layout`, with ⇧ or without: the first of a full keyboard's codes that does.
    private static func keyCode(typing characters: String, shift: Bool, on layout: TISInputSource?) -> UInt16? {
        (0..<128).map(UInt16.init).first { self.characters($0, shift: shift, on: layout) == characters }
    }

    /// What key `code` types on `layout`, as the layout's own table says, a dead key giving its own character.
    private static func characters(_ code: UInt16, shift: Bool, on layout: TISInputSource?) -> String? {
        guard let layout, let raw = TISGetInputSourceProperty(layout, kTISPropertyUnicodeKeyLayoutData) else {
            return nil
        }
        let data = Unmanaged<CFData>.fromOpaque(raw).takeUnretainedValue() as Data
        return data.withUnsafeBytes { bytes -> String? in
            guard let table = bytes.bindMemory(to: UCKeyboardLayout.self).baseAddress else { return nil }
            var dead: UInt32 = 0
            var units = [UniChar](repeating: 0, count: 4)
            var length = 0
            let held = shift ? UInt32(shiftKey >> 8) & 0xFF : 0
            let status = UCKeyTranslate(table, code, UInt16(kUCKeyActionDown), held, UInt32(LMGetKbdType()),
                                        OptionBits(kUCKeyTranslateNoDeadKeysBit), &dead, units.count, &length, &units)
            guard status == noErr, length > 0 else { return nil }
            return String(utf16CodeUnits: units, count: length)
        }
    }
}

/// Where a window's catcher of single keys stands in its chain of responders: last before the window, between its
/// content view and it. A key nothing in the window took then reaches the catcher before the window's own `keyDown`,
/// which offers a key with no ⌘ to the menu bar as an equivalent: Edit's Emoji & Symbols and Start Dictation, which
/// AppKit adds, hold fn-E and fn-D, and a menu matches them on e and d alone, so those two never went further.
@MainActor
enum BeforeTheWindow {
    /// Stand `catcher` last before `window`, where it isn't already: a content view set again stands before the window
    /// with no catcher, so the catcher goes back in. With no content view it stands after the window.
    static func hang(_ catcher: NSResponder, on window: NSWindow) {
        guard let content = window.contentView else {
            if !holds(catcher, from: window.nextResponder, until: nil) {
                catcher.nextResponder = window.nextResponder
                window.nextResponder = catcher
            }
            return
        }
        guard !holds(catcher, from: content.nextResponder, until: window) else { return }
        // Once after the window, it comes out of there first, so a key never reaches it twice.
        var before: NSResponder = window
        while let next = before.nextResponder {
            if next === catcher {
                before.nextResponder = catcher.nextResponder
                break
            }
            before = next
        }
        catcher.nextResponder = content.nextResponder ?? window
        content.nextResponder = catcher
    }

    private static func holds(_ catcher: NSResponder, from start: NSResponder?, until end: NSResponder?) -> Bool {
        var next = start
        while let responder = next, responder !== end {
            if responder === catcher { return true }
            next = responder.nextResponder
        }
        return false
    }
}
#endif
