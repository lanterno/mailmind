import AppKit
import Carbon.HIToolbox
import XCTest

/// The keyboard layouts a test reads single keys on, named by macOS's own identifiers: picked for the test alone,
/// never made the Mac's.
@MainActor
enum KeyLayouts {
    static func named(_ id: String) -> TISInputSource? {
        let filter = [kTISPropertyInputSourceID as String: id] as CFDictionary
        let found = TISCreateInputSourceList(filter, true)?.takeRetainedValue() as? [TISInputSource]
        return found?.first
    }

    /// Read single keys as typed on Arabic, with US as the ASCII-capable layout beside it, until the returned close.
    static func arabic() throws -> () -> Void {
        guard let arabic = named("com.apple.keylayout.Arabic"), let us = named("com.apple.keylayout.US") else {
            throw XCTSkip("this Mac has no Arabic or US layout")
        }
        let was = SingleKey.layouts
        SingleKey.layouts = { (arabic, us) }
        return { SingleKey.layouts = was }
    }

    /// A key as macOS hands it over under Arabic: the Arabic layout's characters, the US key code, what is held.
    static func event(_ typed: String, _ code: UInt16, held: NSEvent.ModifierFlags = [], in window: NSWindow? = nil)
        throws -> NSEvent
    {
        try RealKeys.event(typed, code, held: held, in: window)
    }
}

/// Which key a single-key shortcut is under a layout whose letters aren't Latin: the key's own on the ASCII-capable
/// layout, as Apple's apps read one, and what was typed wherever that is ASCII already.
@MainActor
final class SingleKeyTests: XCTestCase {
    func testAnArabicKeyIsTheKeyItStandsOn() throws {
        let close = try KeyLayouts.arabic()
        defer { close() }
        for (typed, code, key) in [("ي", 2, "d"), ("ث", 14, "e"), ("ت", 38, "j"), ("ن", 40, "k"), ("ق", 15, "r"),
                                   ("س", 1, "s"), ("غ", 16, "y"), ("ر", 45, "n"), ("ل", 5, "g"), ("١", 18, "1")]
            as [(String, UInt16, Character)] {
            XCTAssertEqual(SingleKey.typed(try KeyLayouts.event(typed, code)), key, "\(typed) is \(key)")
        }
        // ⇧ makes the shifted one: Arabic's ؟ is ?, and its # is # already.
        XCTAssertEqual(SingleKey.typed(try KeyLayouts.event("؟", 44, held: .shift)), "?")
        XCTAssertEqual(SingleKey.typed(try KeyLayouts.event("#", 20, held: .shift)), "#")
    }

    func testWhatIsTypedInASCIIIsTakenAsTyped() throws {
        let close = try KeyLayouts.arabic()
        defer { close() }
        // A layout whose letters are Latin elsewhere on the keyboard (AZERTY's a where QWERTY has q) keeps its own.
        XCTAssertEqual(SingleKey.typed(try KeyLayouts.event("a", 12)), "a")
        XCTAssertEqual(SingleKey.typed(try KeyLayouts.event("\u{1b}", 53)), "\u{1b}")
        XCTAssertEqual(SingleKey.typed(try KeyLayouts.event("\u{7f}", 51)), "\u{7f}")
        let down = String(Character(UnicodeScalar(NSDownArrowFunctionKey)!))
        XCTAssertEqual(SingleKey.typed(try KeyLayouts.event(down, 125)), Character(down), "the arrows are as they come")
    }
}
