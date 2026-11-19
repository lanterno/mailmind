import AppKit
import SwiftUI
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

    /// Items in a scroll of their own are items the keys go over, in their place among the rest.
    func testAMenusScrolledItemsAreAmongTheItemsTheKeysGoOver() {
        let scrolled = ["s1", "s2", "s3"].map { id in MeadowMenuItem(id: id, title: id) {} }
            + [MeadowMenuItem(id: "off", title: "Off", disabled: true) {}]
        let parts: [MeadowMenu.Part] = [
            .title("Model"), .item(MeadowMenuItem(id: "a", title: "A") {}), .scroll(scrolled, maxHeight: 100), .rule,
            .item(MeadowMenuItem(id: "b", title: "B") {}), .view(AnyView(EmptyView())),
        ]
        XCTAssertEqual(MeadowMenu.items(parts).map(\.id), ["a", "s1", "s2", "s3", "b"])
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

    // MARK: A popover's anchor

    func testAPopoverAskedForBeforeItsAnchorIsInAWindowShowsAsTheAnchorArrives() throws {
        let parent = window()
        let anchor = MeadowPopoverAnchorView(frame: NSRect(x: 40, y: 200, width: 30, height: 20))
        let popover = MeadowPopoverAnchor<Text>.Coordinator()
        // h opens the snooze chooser in the same update that makes the anchor it hangs from.
        popover.show(Text("Later today"), under: anchor)
        XCTAssertFalse(popover.isShown, "nowhere to hang it yet")
        parent.contentView?.addSubview(anchor)
        XCTAssertTrue(popover.isShown, "it shows as its anchor joins the window, not at the next update")
        let panel = try XCTUnwrap(parent.childWindows?.first)
        let was = panel.frame.origin
        anchor.setFrameOrigin(NSPoint(x: 140, y: 200))
        XCTAssertEqual(panel.frame.origin.x, was.x + 100, "and follows it as it moves")
        popover.hide()
        XCTAssertFalse(popover.isShown)
        anchor.removeFromSuperview()
        parent.contentView?.addSubview(anchor)
        XCTAssertFalse(popover.isShown, "a popover closed stays closed as its anchor comes back")
    }

    /// A popover from a button by the window's right edge, as the snooze chooser beside a brief card: its paper stands
    /// 12 in from the edge, and nothing of it, its shadow's room included, falls past the window, as nothing of a toast
    /// does. One in the middle of the window is whole.
    func testAPopoverByTheWindowsEdgeStaysInsideTheWindow() throws {
        let parent = window()
        let paper = CGSize(width: 260, height: 120)
        let edge = MeadowPopoverAnchorView(frame: NSRect(x: 370, y: 200, width: 24, height: 24))
        parent.contentView?.addSubview(edge)
        let popover = MeadowPopoverAnchor<AnyView>.Coordinator()
        popover.show(AnyView(Color.white.frame(width: paper.width, height: paper.height)), under: edge)
        let panel = try XCTUnwrap(parent.childWindows?.first)
        XCTAssertTrue(parent.frame.contains(panel.frame), "\(panel.frame) inside \(parent.frame)")
        let holder = try XCTUnwrap(panel.contentView)
        let host = try XCTUnwrap(holder.subviews.first)
        let room = MeadowPopoverAnchor<AnyView>.Coordinator.room
        let drawn = NSRect(x: panel.frame.minX + host.frame.minX + room, y: panel.frame.minY + host.frame.minY + room,
                           width: host.frame.width - 2 * room, height: host.frame.height - 2 * room)
        XCTAssertEqual(drawn.width, paper.width, "the paper whole")
        XCTAssertEqual(drawn.maxX, parent.frame.maxX - 12, "12 in from the window's edge")
        let button = edge.convert(edge.bounds, to: nil)
        XCTAssertEqual(drawn.maxY, parent.frame.minY + button.minY - 6, "6 under its button")
        popover.hide()

        let middle = MeadowPopoverAnchorView(frame: NSRect(x: 60, y: 200, width: 24, height: 24))
        parent.contentView?.addSubview(middle)
        let whole = MeadowPopoverAnchor<AnyView>.Coordinator()
        whole.show(AnyView(Color.white.frame(width: 200, height: 100)), under: middle)
        let uncut = try XCTUnwrap(parent.childWindows?.last)
        XCTAssertEqual(uncut.frame.width, 200 + 2 * room, "nothing cut in the middle")
        XCTAssertEqual(uncut.contentView?.subviews.first?.frame.origin, .zero)
        whole.hide()
    }

    func testAnOpenPopoverKeepsTheListsKeysFromTheListBehindIt() {
        let parent = window()
        let list = Keyed(frame: NSRect(x: 0, y: 0, width: 100, height: 100))
        let anchor = MeadowPopoverAnchorView(frame: NSRect(x: 200, y: 200, width: 30, height: 20))
        parent.contentView?.addSubview(list)
        parent.contentView?.addSubview(anchor)
        XCTAssertTrue(parent.makeFirstResponder(list))
        let popover = MeadowPopoverAnchor<Text>.Coordinator()
        var offered: [String] = [], closed = 0
        popover.keys = { event in
            offered.append(event.charactersIgnoringModifiers ?? "")
            return event.charactersIgnoringModifiers == "1"
        }
        popover.close = { closed += 1 }
        popover.show(Text("Later today"), under: anchor)
        func typed(_ code: UInt16, _ characters: String, held: NSEvent.ModifierFlags = []) -> NSEvent? {
            popover.typed(NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: held, timestamp: 0,
                                           windowNumber: parent.windowNumber, context: nil, characters: characters,
                                           charactersIgnoringModifiers: characters, isARepeat: false, keyCode: code)!)
        }
        XCTAssertNil(typed(38, "j"), "j stays with the chooser, though it takes none: the list's choice holds")
        XCTAssertNil(typed(18, "1"), "1 snoozes the message the chooser opened for")
        XCTAssertEqual(offered, ["j", "1"])
        XCTAssertNotNil(typed(18, "1", held: .command), "⌘1 goes on to the menus")
        XCTAssertNil(typed(53, "\u{1b}"))
        XCTAssertEqual(closed, 1, "Esc closes it")
        popover.hide()
    }
}
