import AppKit
import Carbon.HIToolbox

/// A keyboard layout a key is pressed under, named by macOS's own input source identifier. The kit only reads a
/// layout's table: it never selects, enables or changes an input source, so the Mac's own layout stays as it was.
enum KeyboardLayout: String, CaseIterable, Sendable {
    case us = "com.apple.keylayout.US"
    case arabic = "com.apple.keylayout.Arabic"
    case swissFrench = "com.apple.keylayout.SwissFrench"
    case german = "com.apple.keylayout.German"

    /// The layout's input source, as `TISCreateInputSourceList` lists every one installed, enabled or not. It throws
    /// where this Mac has no such layout.
    func source() throws -> TISInputSource {
        try Self.source(rawValue)
    }

    /// The input source whose identifier is `id`, installed whether or not it is enabled.
    static func source(_ id: String) throws -> TISInputSource {
        let filter = [kTISPropertyInputSourceID as String: id] as CFDictionary
        let found = TISCreateInputSourceList(filter, true)?.takeRetainedValue() as? [TISInputSource]
        guard let source = found?.first else { throw KitError("this Mac has no \(id) keyboard layout") }
        return source
    }

    /// What key `code` types under this layout with `held` held, as AppKit fills a key-down's two strings:
    /// `characters` with every modifier, `ignoringModifiers` with only ⇧. With ⌘ held, a layout whose letters aren't
    /// Latin keeps a table of its own for shortcuts (Arabic's ⌘ي is ⌘D), which `ignoringModifiers` reads where the
    /// key alone types no ASCII. A key that types nothing printable (Return, the arrows, Escape) is AppKit's character
    /// for it.
    func characters(_ code: UInt16, held: NSEvent.ModifierFlags = []) throws
        -> (characters: String, ignoringModifiers: String)
    {
        if let fixed = Self.functionKeys[code] { return (fixed, fixed) }
        let data = try table()
        let all = try Self.translate(code, state: Self.state(held), in: data)
        let plain = try Self.translate(code, state: 0, in: data)
        let latin = !plain.isEmpty && plain.unicodeScalars.allSatisfy { $0.value > 0x20 && $0.value < 0x7F }
        let kept: NSEvent.ModifierFlags = held.contains(.command) && !latin ? [.shift, .command] : [.shift]
        return (all, try Self.translate(code, state: Self.state(held.intersection(kept)), in: data))
    }

    /// The layout's `uchr` table: what `UCKeyTranslate` reads.
    private func table() throws -> Data {
        guard let raw = TISGetInputSourceProperty(try source(), kTISPropertyUnicodeKeyLayoutData) else {
            throw KitError("the \(rawValue) layout carries no Unicode key table")
        }
        return Unmanaged<CFData>.fromOpaque(raw).takeUnretainedValue() as Data
    }

    /// Carbon's modifier state as `UCKeyTranslate` takes it: the event modifiers' high byte.
    private static func state(_ held: NSEvent.ModifierFlags) -> UInt32 {
        var state = 0
        if held.contains(.command) { state |= cmdKey }
        if held.contains(.shift) { state |= shiftKey }
        if held.contains(.capsLock) { state |= alphaLock }
        if held.contains(.option) { state |= optionKey }
        if held.contains(.control) { state |= controlKey }
        return UInt32(state >> 8) & 0xFF
    }

    private static func translate(_ code: UInt16, state: UInt32, in data: Data) throws -> String {
        try data.withUnsafeBytes { bytes in
            guard let table = bytes.bindMemory(to: UCKeyboardLayout.self).baseAddress else {
                throw KitError("an empty key table")
            }
            var dead: UInt32 = 0
            var units = [UniChar](repeating: 0, count: 8)
            var length = 0
            let status = UCKeyTranslate(table, code, UInt16(kUCKeyActionDown), state, UInt32(LMGetKbdType()),
                                        OptionBits(kUCKeyTranslateNoDeadKeysBit), &dead, units.count, &length, &units)
            guard status == noErr else { throw KitError("UCKeyTranslate failed on key \(code): \(status)") }
            return String(utf16CodeUnits: units, count: length)
        }
    }

    /// The keys AppKit names by a character of its own, whatever the layout: the ones a layout's table holds as
    /// control characters (Delete is 0x7F in AppKit, 0x08 in the table) or as function keys.
    private static let functionKeys: [UInt16: String] = {
        func key(_ code: Int) -> String { String(Character(UnicodeScalar(UInt32(code))!)) }
        return [
            UInt16(kVK_Return): "\r", UInt16(kVK_Tab): "\t", UInt16(kVK_Space): " ", UInt16(kVK_Delete): "\u{7f}",
            UInt16(kVK_Escape): "\u{1b}", UInt16(kVK_ANSI_KeypadEnter): "\u{3}",
            UInt16(kVK_LeftArrow): key(NSLeftArrowFunctionKey), UInt16(kVK_RightArrow): key(NSRightArrowFunctionKey),
            UInt16(kVK_UpArrow): key(NSUpArrowFunctionKey), UInt16(kVK_DownArrow): key(NSDownArrowFunctionKey),
            UInt16(kVK_Home): key(NSHomeFunctionKey), UInt16(kVK_End): key(NSEndFunctionKey),
            UInt16(kVK_PageUp): key(NSPageUpFunctionKey), UInt16(kVK_PageDown): key(NSPageDownFunctionKey),
            UInt16(kVK_ForwardDelete): key(NSDeleteFunctionKey), UInt16(kVK_Help): key(NSHelpFunctionKey),
            UInt16(kVK_F1): key(NSF1FunctionKey), UInt16(kVK_F2): key(NSF2FunctionKey),
            UInt16(kVK_F3): key(NSF3FunctionKey), UInt16(kVK_F4): key(NSF4FunctionKey),
            UInt16(kVK_F5): key(NSF5FunctionKey), UInt16(kVK_F6): key(NSF6FunctionKey),
            UInt16(kVK_F7): key(NSF7FunctionKey), UInt16(kVK_F8): key(NSF8FunctionKey),
            UInt16(kVK_F9): key(NSF9FunctionKey), UInt16(kVK_F10): key(NSF10FunctionKey),
            UInt16(kVK_F11): key(NSF11FunctionKey), UInt16(kVK_F12): key(NSF12FunctionKey),
        ]
    }()
}

extension RealKeys {
    /// A key-down for key `code` under `layout`, its characters read from the layout's own table.
    static func event(_ code: UInt16, held: NSEvent.ModifierFlags = [], layout: KeyboardLayout,
                      in window: NSWindow? = nil) throws -> NSEvent
    {
        let typed = try layout.characters(code, held: held)
        return try event(typed.characters, ignoringModifiers: typed.ignoringModifiers, code, held: held, in: window)
    }

    /// Key `code` pressed in `window` under `layout`, as `press` presses a key.
    static func press(_ code: UInt16, held: NSEvent.ModifierFlags = [], layout: KeyboardLayout,
                      in window: NSWindow) throws
    {
        try press(try event(code, held: held, layout: layout, in: window), in: window)
    }
}
