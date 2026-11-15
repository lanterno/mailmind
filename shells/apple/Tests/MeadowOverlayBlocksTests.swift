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

    private func key(_ code: UInt16, _ characters: String = "") -> NSEvent {
        NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: 0,
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
