import AppKit
import SwiftUI
import XCTest

/// A snooze's toast: it says which message it snoozed when that isn't the one on screen, and the toasts stand over
/// the reading pane, clear of the reply box's quick replies.
@MainActor
final class SnoozeToastTests: XCTestCase {
    /// The mockup's window: 1440 by 900, the pane from 600 to 1430.
    private let window = CGRect(x: 0, y: 0, width: 1440, height: 900)
    private let pane = CGRect(x: 600, y: 66, width: 830, height: 824)

    func testTheToastsStandOverThePane() {
        let place = ToastPlace.of(window: window, pane: pane, reply: nil)
        XCTAssertEqual(place.dx, pane.midX - window.midX, "centred over the pane, not the window")
        XCTAssertEqual(place.bottom, ToastPlace.foot)
        let gone = CGRect(x: -40_000, y: 0, width: 480, height: 480)
        XCTAssertEqual(ToastPlace.of(window: window, pane: gone, reply: nil).dx, 0, "a pane put away counts for nothing")
    }

    func testTheToastsStandOverTheReplyBoxAtThePanesFoot() {
        // The reply box closed at the pane's foot, its quick replies along its bottom.
        let reply = CGRect(x: 640, y: 770, width: 750, height: 110)
        let place = ToastPlace.of(window: window, pane: pane, reply: reply)
        XCTAssertEqual(place.bottom, window.maxY - reply.minY + ToastPlace.gap, "over its top, with room between")
        let toastFoot = window.maxY - place.bottom
        XCTAssertLessThan(toastFoot, reply.minY, "the toast's foot stands above the box")

        let scrolledUp = CGRect(x: 640, y: 300, width: 750, height: 110)
        XCTAssertEqual(ToastPlace.of(window: window, pane: pane, reply: scrolledUp).bottom, ToastPlace.foot,
                       "a box higher up is clear of the toasts already")
        let below = CGRect(x: 640, y: 1200, width: 750, height: 110)
        XCTAssertEqual(ToastPlace.of(window: window, pane: pane, reply: below).bottom, ToastPlace.foot,
                       "and one scrolled out of sight below")
    }

    /// A toast centred over the pane that would run past the window's right edge moves in until it stands inside it;
    /// one too wide for the window is made narrower, its words wrapped.
    func testALongToastStaysInsideTheWindow() {
        let dx = pane.midX - window.midX
        XCTAssertEqual(ToastPlace.inside(dx, wide: 400, window: window.width), dx, "room to stand over the pane")
        let wide: CGFloat = 1000
        let moved = ToastPlace.inside(dx, wide: wide, window: window.width)
        XCTAssertEqual(window.midX + moved + wide / 2, window.maxX - ToastPlace.margin, "its right edge inside")
        XCTAssertEqual(ToastPlace.inside(-dx, wide: wide, window: window.width), -moved, "and its left")
        XCTAssertEqual(ToastPlace.inside(dx, wide: 1500, window: window.width), 0, "too wide: centred")
    }

    /// As the window draws them: a toast wider than the room right of the pane's middle stands inside the window,
    /// and one wider than the window is made as wide as it leaves room for.
    func testTheWindowKeepsItsToastsInside() async throws {
        let room = ToastRoom()
        let size = CGSize(width: 1180, height: 600)
        room.set(.pane, CGRect(x: 600, y: 60, width: 570, height: 530))
        for asked in [CGFloat(760), 2000] {
            let toast = ToastFrame()
            let host = NSHostingView(rootView: PlacedToasts(room: room) {
                Color.gray
                    .frame(maxWidth: asked)
                    .frame(height: 44)
                    .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) } action: { toast.frame = $0 }
            }
            .frame(width: size.width, height: size.height))
            host.frame = CGRect(origin: .zero, size: size)
            let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.contentView = host
            for _ in 0..<10 {
                host.layoutSubtreeIfNeeded()
                try await Task.sleep(for: .milliseconds(20))
            }
            let frame = try XCTUnwrap(toast.frame)
            XCTAssertGreaterThanOrEqual(frame.minX, ToastPlace.margin - 0.5, "\(asked): inside on the left")
            XCTAssertLessThanOrEqual(frame.maxX, size.width - ToastPlace.margin + 0.5, "\(asked): inside on the right")
            XCTAssertEqual(frame.width, min(asked, size.width - 2 * ToastPlace.margin), accuracy: 0.5, "\(asked)")
            window.contentView = nil
        }
    }

    func testASnoozeNamesTheMessageOnlyWhenItIsntTheOneOnScreen() async throws {
        let app = MailApp()
        app.mailbox.wire = Fixture.server(rows: Array(Fixture.inbox.prefix(6))).wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.openList()
        let list = try XCTUnwrap(app.list)
        await until { list.copy(at: 2) != nil }
        let open = try XCTUnwrap(list.copy(at: 0)), other = try XCTUnwrap(list.copy(at: 2))
        app.mailbox.chose([open])
        XCTAssertNil(app.mailbox.subjectAway([MailCopy(open)]), "the message on screen needs no naming")
        XCTAssertEqual(app.mailbox.subjectAway([MailCopy(other)]), other.subject, "a row's clock names its message")
        XCTAssertNil(app.mailbox.subjectAway([MailCopy(open), MailCopy(other)]), "several are counted, not named")
    }
}

/// Where a toast drawn in a test stood.
@MainActor
private final class ToastFrame {
    var frame: CGRect?
}
