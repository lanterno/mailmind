import AppKit
import XCTest

/// A snooze's toast: it says which message it snoozed when that isn't the one on screen, and the toasts stand over
/// the reading pane, clear of the reply box's quick replies.
@MainActor
final class SnoozeToastTests: XCTestCase {
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
