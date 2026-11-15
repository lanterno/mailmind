import AppKit
import XCTest

/// Meadow's overlays' building blocks, by their rules: where a dialog and a popover stand, a key hint's caps, and a
/// popover menu's keys.
@MainActor
final class MeadowOverlayBlocksTests: XCTestCase {
    func testADialogStandsCentredAtItsShareOfTheHeight() {
        let palette = MeadowDialogPlace.palette.frame(in: CGSize(width: 1440, height: 900))
        XCTAssertEqual(palette.width, 700)
        XCTAssertEqual(palette.x, 370)
        XCTAssertEqual(palette.y, 99)
        let narrow = MeadowDialogPlace.keys.frame(in: CGSize(width: 600, height: 700))
        XCTAssertEqual(narrow.width, 568, "16 in from each side at least")
        XCTAssertEqual(narrow.x, 16)
        XCTAssertEqual(narrow.y, 63)
    }

    func testAPopoverHangsUnderItsButtonWithinTheWindow() {
        let window = CGSize(width: 1440, height: 900)
        let pop = CGSize(width: 260, height: 300)
        let snooze = CGRect(x: 811, y: 76, width: 90, height: 36)
        XCTAssertEqual(MeadowPopPlacement.origin(of: pop, under: snooze, in: window), CGPoint(x: 811, y: 118))
        // Near the right edge, it keeps 12 from it.
        let status = CGRect(x: 1300, y: 11, width: 120, height: 34)
        XCTAssertEqual(MeadowPopPlacement.origin(of: pop, under: status, in: window).x, 1440 - 260 - 12)
        // Near the foot, it opens above.
        let low = CGRect(x: 100, y: 800, width: 80, height: 30)
        XCTAssertEqual(MeadowPopPlacement.origin(of: pop, under: low, in: window), CGPoint(x: 100, y: 492))
        // With no button, near the middle.
        XCTAssertEqual(MeadowPopPlacement.origin(of: pop, under: nil, in: window), CGPoint(x: 590, y: 306))
    }

    func testAKeyHintIsACapAKey() {
        XCTAssertEqual(MeadowKeys.caps("g i"), ["G", "I"])
        XCTAssertEqual(MeadowKeys.caps("e"), ["E"])
        XCTAssertEqual(MeadowKeys.caps("⇧⌘M"), ["⇧⌘M"])
        XCTAssertEqual(MeadowKeys.caps(" "), [])
        XCTAssertEqual(MeadowKeys.caps(nil), [])
    }

    private func key(_ code: UInt16, _ characters: String = "", held: NSEvent.ModifierFlags = []) -> NSEvent {
        NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: held, timestamp: 0, windowNumber: 0,
                         context: nil, characters: characters, charactersIgnoringModifiers: characters,
                         isARepeat: false, keyCode: code)!
    }

    func testAMenusKeysStepOverItsItemsAndReturnRunsOne() {
        var ran: [String] = []
        var closed = 0
        let model = MeadowMenuModel()
        model.close = { closed += 1 }
        model.items = [
            MeadowMenuItem(id: "a", title: "A") { ran.append("a") },
            MeadowMenuItem(id: "off", title: "Off", disabled: true) { ran.append("off") },
            MeadowMenuItem(id: "b", title: "B", closes: false) { ran.append("b") },
        ]
        XCTAssertTrue(model.key(key(125)))
        XCTAssertEqual(model.at, 1, "down skips what can't be chosen")
        XCTAssertTrue(model.key(key(36)))
        XCTAssertEqual(ran, ["b"])
        XCTAssertEqual(closed, 0, "an item that opens more keeps the popover")
        XCTAssertTrue(model.key(key(125)))
        XCTAssertEqual(model.at, 0, "and wraps round")
        XCTAssertTrue(model.key(key(76)))
        XCTAssertEqual(ran, ["b", "a"])
        XCTAssertEqual(closed, 1)
        XCTAssertTrue(model.key(key(126)))
        XCTAssertEqual(model.at, 1)
        XCTAssertFalse(model.key(key(0, "e")), "other keys go on to the window")
    }

    private func window() -> NSWindow {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        return window
    }

    /// A view that takes the keyboard, as the list's table does.
    private final class Keyed: NSView {
        override var acceptsFirstResponder: Bool { true }
    }

    func testAPopoverTakesKeysOnlyFromItsOwnWindowAndNoneFromAField() throws {
        let parent = window(), other = window(), panel = window()
        let list = Keyed(frame: NSRect(x: 0, y: 0, width: 100, height: 100))
        let body = NSTextView(frame: NSRect(x: 100, y: 0, width: 100, height: 100))
        let said = NSTextView(frame: NSRect(x: 200, y: 0, width: 100, height: 100))
        said.isEditable = false
        let field = NSTextField(frame: NSRect(x: 300, y: 0, width: 100, height: 22))
        for view in [list, body, said, field] { parent.contentView?.addSubview(view) }
        func verdict(_ code: UInt16, in window: NSWindow?) -> MeadowPopoverKeys.Verdict {
            MeadowPopoverKeys.verdict(keyCode: code, modifiers: [], typedIn: window, hungIn: parent, panel: panel)
        }

        XCTAssertTrue(parent.makeFirstResponder(list))
        XCTAssertEqual(verdict(53, in: parent), .close)
        XCTAssertEqual(verdict(125, in: parent), .offer)
        XCTAssertEqual(verdict(19, in: parent), .offer, "a snooze's digit, typed over the list")
        XCTAssertEqual(verdict(125, in: panel), .offer)
        XCTAssertEqual(verdict(36, in: other), .pass, "another window's Return is its own")
        XCTAssertEqual(verdict(53, in: other), .pass)
        XCTAssertEqual(verdict(36, in: nil), .pass)

        XCTAssertTrue(parent.makeFirstResponder(body))
        XCTAssertEqual(verdict(36, in: parent), .pass, "Return in a message's body is the body's")
        XCTAssertEqual(verdict(19, in: parent), .pass)
        XCTAssertEqual(verdict(124, in: parent), .pass)
        XCTAssertEqual(verdict(53, in: parent), .close, "Esc still closes it")

        XCTAssertTrue(parent.makeFirstResponder(field))
        XCTAssertTrue(MeadowPopoverKeys.typesText(parent.firstResponder), "a field is edited in its editor")
        XCTAssertEqual(verdict(19, in: parent), .pass, "a 2 typed in the search field is the field's")

        XCTAssertTrue(parent.makeFirstResponder(said))
        XCTAssertEqual(verdict(125, in: parent), .offer, "text only to read takes no keys")
        XCTAssertFalse(MeadowPopoverKeys.typesText(nil))
    }

    func testAPopoverTakesNoKeyWithACommandKeyHeld() {
        let parent = window(), panel = window()
        func verdict(_ code: UInt16, _ held: NSEvent.ModifierFlags) -> MeadowPopoverKeys.Verdict {
            MeadowPopoverKeys.verdict(keyCode: code, modifiers: held, typedIn: parent, hungIn: parent, panel: panel)
        }
        XCTAssertEqual(verdict(18, .command), .pass, "⌘1 goes to Inbox, not to the chooser")
        XCTAssertEqual(verdict(36, .command), .pass, "⌘Return sends")
        XCTAssertEqual(verdict(125, .control), .pass)
        XCTAssertEqual(verdict(124, .option), .pass)
        XCTAssertEqual(verdict(53, .command), .pass)
        XCTAssertEqual(verdict(18, .shift), .offer, "⇧ only makes another character")
        XCTAssertEqual(verdict(125, [.function, .numericPad]), .offer, "the arrows carry these of their own")
        XCTAssertEqual(verdict(18, []), .offer)

        var ran: [String] = []
        var heard = 0
        let model = MeadowMenuModel()
        model.items = [MeadowMenuItem(id: "a", title: "A") { ran.append("a") }]
        model.onKey = { _ in
            heard += 1
            return true
        }
        XCTAssertFalse(model.key(key(36, "\r", held: .command)))
        XCTAssertFalse(model.key(key(18, "1", held: .command)))
        XCTAssertFalse(model.key(key(125, held: [.option, .function])))
        XCTAssertEqual(heard, 0, "the menu's own keys never hear them")
        XCTAssertEqual(ran, [])
        XCTAssertTrue(model.key(key(18, "!", held: .shift)))
        XCTAssertEqual(heard, 1)
    }

    func testAPressOnThePopoversButtonIsOnlyItsClosing() {
        let parent = window(), other = window()
        let button = NSView(frame: NSRect(x: 50, y: 40, width: 80, height: 30))
        parent.contentView?.addSubview(button)
        XCTAssertTrue(MeadowPopoverKeys.presses(button, at: NSPoint(x: 60, y: 50), in: parent))
        XCTAssertFalse(MeadowPopoverKeys.presses(button, at: NSPoint(x: 10, y: 10), in: parent),
                       "a press elsewhere closes it and goes on")
        XCTAssertFalse(MeadowPopoverKeys.presses(button, at: NSPoint(x: 60, y: 50), in: other))
        XCTAssertFalse(MeadowPopoverKeys.presses(nil, at: NSPoint(x: 60, y: 50), in: parent))
        let nowhere = NSView(frame: .zero)
        parent.contentView?.addSubview(nowhere)
        XCTAssertFalse(MeadowPopoverKeys.presses(nowhere, at: .zero, in: parent),
                       "the chooser hung in the middle has no button")
        button.removeFromSuperview()
        XCTAssertFalse(MeadowPopoverKeys.presses(button, at: NSPoint(x: 60, y: 50), in: parent))
    }

    func testAMenusOwnKeysComeFirst() {
        let model = MeadowMenuModel()
        model.items = [MeadowMenuItem(id: "a", title: "A") {}]
        var heard: [String] = []
        model.onKey = { event in
            heard.append(event.charactersIgnoringModifiers ?? "")
            return event.charactersIgnoringModifiers == "1"
        }
        XCTAssertTrue(model.key(key(18, "1")))
        XCTAssertTrue(model.key(key(125)))
        XCTAssertEqual(heard.count, 2)
        model.items = []
        XCTAssertFalse(model.key(key(125)), "with no items, the arrows are someone else's")
    }
}
