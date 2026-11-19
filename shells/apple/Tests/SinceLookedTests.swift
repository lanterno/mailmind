import XCTest

/// The pill for mail that landed away from the list on screen, and its line on what came in since the Inbox was
/// looked at, as the page's NewMail.tsx says them.
@MainActor
final class SinceLookedTests: XCTestCase {
    private let ada = "ada@example.test", bo = "bo@example.test"
    private var routes: DeskRoutes!
    private var since: SinceLooked!
    private var clock = Date(timeIntervalSince1970: 1_800_000_000)

    override func setUp() async throws {
        routes = DeskRoutes()
        since = SinceLooked(link: CoreLink(routes))
        since.isActive = { true }
        since.now = { [unowned self] in clock }
    }

    private func arrived(_ account: String, _ folder: String = "INBOX", _ count: Int = 1) -> SinceLooked.Arrived {
        SinceLooked.Arrived(account: account, folder: folder, count: count)
    }

    func testMailOnTheListOnScreenIsNotSaidAndMailElsewhereIs() {
        since.newMail(arrived(ada), on: .allInboxes, ai: false)
        since.newMail(arrived(ada), on: .folder(account: ada, folder: "INBOX"), ai: false)
        XCTAssertNil(since.words, "the Inbox on screen reads it in place")
        since.newMail(arrived(ada, "INBOX", 2), on: .folder(account: ada, folder: "Archive"), ai: false)
        XCTAssertEqual(since.words, "▾ 2 new — open the Inbox")
        XCTAssertEqual(since.inbox, .folder(account: ada, folder: "INBOX"))
        since.newMail(arrived(bo), on: .desk(.brief), ai: false)
        XCTAssertEqual(since.words, "▾ 3 new — open the Inbox")
        XCTAssertEqual(since.inbox, .allInboxes, "mail to two accounts opens every Inbox")
    }

    func testMailAnUndoPutsBackInAnInboxIsNeverNew() {
        // Answered before the sync finds it: the sync's find is taken off, and only mail beside it is new.
        since.undoing()
        since.undone(account: ada, folder: "INBOX", count: 1)
        since.newMail(arrived(ada), on: .search(Search(q: "paid", account: ada)), ai: false)
        XCTAssertNil(since.words, "the message deleted and undone is no new mail")
        since.undoing()
        since.undone(account: ada, folder: "INBOX", count: 2)
        since.newMail(arrived(ada, "INBOX", 3), on: .documents, ai: false)
        XCTAssertEqual(since.words, "▾ 1 new — open the Inbox", "what came beside it is new")

        // Found before the Undo is answered: its answer takes it back off the pill.
        since.lookedAt(.allInboxes, accounts: [ada, bo])
        since.undoing()
        since.newMail(arrived(bo), on: .documents, ai: false)
        XCTAssertNotNil(since.words)
        since.undone(account: bo, folder: "INBOX", count: 1)
        XCTAssertNil(since.words, "the pill goes with it")

        // Put back somewhere else than an Inbox, or long ago, or by an Undo that failed: mail is new as it comes.
        since.undoing()
        since.undone(account: ada, folder: "Archive", count: 1)
        since.undoing()
        since.undone(account: nil, folder: nil, count: 0)
        since.newMail(arrived(ada), on: .documents, ai: false)
        XCTAssertEqual(since.words, "▾ 1 new — open the Inbox")
        since.lookedAt(.allInboxes, accounts: [ada, bo])
        since.undoing()
        since.undone(account: ada, folder: "INBOX", count: 1)
        clock = clock.addingTimeInterval(SinceLooked.comingBack + 1)
        since.newMail(arrived(ada), on: .documents, ai: false)
        XCTAssertEqual(since.words, "▾ 1 new — open the Inbox", "a sync long after is new mail")
    }

    /// Search, open a result, delete it from the pane, then z: the message back in the Inbox is no new mail.
    func testTheMailboxsUndoTellsThePillWhatCameBack() async throws {
        let server = OpenedMessageVerbsTests.server()
        server.on("POST", API.Post.Undo.path, API.Undone(account: ada, folder: "INBOX", label: "Delete", ok: true,
                                                         remaining: 0))
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        let desk = Desk()
        desk.use(app, routes: server.wire)
        app.place = .search(Search(q: "paid", account: ada))
        await waiting(for: "the search's rows") { app.list?.copy(at: 0) != nil }
        app.mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
        await app.mailbox.fileNow(.trash, rows: await app.mailbox.paneTargets(standing: false))
        await app.mailbox.undoNow()
        await desk.heard(CoreEvent(id: "1", name: "new-mail",
                                   data: #"{"account": "ada@example.test", "folder": "INBOX", "count": 1, "uid": 7}"#))
        XCTAssertNil(desk.since.words, "no “1 new — open the Inbox” for the message undone")
        withExtendedLifetime(desk) {}
    }

    func testMailBackFromASnoozeIsSaidBesideTheNew() {
        since.newMail(arrived(ada), on: .documents, ai: false)
        since.snoozeWake(SinceLooked.Woke(accounts: [ada], count: 2), on: .documents)
        XCTAssertEqual(since.words, "▾ 1 new, 2 back from snooze — open the Inbox")
        since.snoozeWake(SinceLooked.Woke(accounts: [ada], count: 1), on: .allInboxes)
        XCTAssertEqual(since.pending?.woke, 2, "an Inbox on screen shows what came back itself")
    }

    func testLookingAtTheInboxClearsThePillAndCountsFromThen() {
        since.newMail(arrived(ada), on: .documents, ai: false)
        XCTAssertEqual(since.pending?.since, clock.addingTimeInterval(-SinceLooked.firstLook), "never looked at: six hours")
        since.lookedAt(.folder(account: ada, folder: "Archive"), accounts: [ada, bo])
        XCTAssertNotNil(since.words, "a folder that isn't the Inbox is no look")
        let looked = clock
        since.lookedAt(.folder(account: ada, folder: "INBOX"), accounts: [ada, bo])
        XCTAssertNil(since.words)
        clock = clock.addingTimeInterval(600)
        since.newMail(arrived(ada), on: .documents, ai: false)
        XCTAssertEqual(since.pending?.since, looked)
    }

    func testTheLineIsReadIfWrittenElseWrittenWithTheAIOn() async throws {
        since.newMail(arrived(ada, "INBOX", 3), on: .documents, ai: true)
        routes.answer("GET", "/api/briefing/since", json: #"{"ok": true, "missing": true, "empty": false, "count": 3}"#)
        routes.answer("POST", "/api/briefing/since", json: #"""
            {"ok": true, "cached": false, "count": 3, "empty": false, "headline": "Grace sent the figures.", "items": []}
            """#)
        await since.writeHeadline()
        XCTAssertEqual(since.pending?.headline, "Grace sent the figures.")
        let asked = try XCTUnwrap(routes.sent("GET", "/api/briefing/since").first)
        XCTAssertEqual(asked.query["account"], ada)
        XCTAssertEqual(asked.query["since"], String(Int(clock.addingTimeInterval(-SinceLooked.firstLook).timeIntervalSince1970)))
        XCTAssertEqual(routes.sent("POST", "/api/briefing/since").count, 1)
    }

    func testNoLineIsAskedForWithTheAIOffNobodyHereOrOnlySnoozesBack() async {
        since.newMail(arrived(ada), on: .documents, ai: false)
        await since.writeHeadline()
        since.isActive = { false }
        since.newMail(arrived(ada), on: .documents, ai: true)
        await since.writeHeadline()
        since.lookedAt(.allInboxes, accounts: [ada])
        since.isActive = { true }
        since.snoozeWake(SinceLooked.Woke(accounts: [ada], count: 1), on: .documents)
        await since.writeHeadline()
        XCTAssertTrue(routes.sent.isEmpty, "asking would count as someone here, and spend")
    }

    func testOverTheDaysLimitThePlainCountStands() async {
        since.newMail(arrived(ada), on: .documents, ai: true)
        routes.answer("GET", "/api/briefing/since", json: #"{"ok": true, "missing": true, "empty": false, "count": 1}"#)
        routes.refuse("POST", "/api/briefing/since", status: 402, type: "over-limit", detail: "Today's AI limit is reached.",
                      more: #""over_limit": true"#)
        await since.writeHeadline()
        XCTAssertEqual(since.pending?.headline, "")
        XCTAssertEqual(since.words, "▾ 1 new — open the Inbox")
    }

    /// The desk tells the pill of mail by the list on screen: with the brief beside the Inbox, mail to that Inbox shows
    /// in the list there and isn't said, and going to the brief is a look at it; away from it, the mail is said.
    func testTheDeskSaysOnlyMailThatLandedAwayFromTheListOnScreen() async {
        let server = Fixture.server()
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        let desk = Desk()
        desk.use(app, routes: server.wire)
        let mail = CoreEvent(id: "1", name: "new-mail", data: #"{"account": "ada@example.test", "folder": "INBOX"}"#)
        app.place = .desk(.brief)
        XCTAssertEqual(app.listed, .allInboxes)
        await desk.heard(mail)
        XCTAssertNil(desk.since.words, "the Inbox beside the brief shows it")
        app.place = .folder(account: ada, folder: "Sent")
        await desk.heard(mail)
        XCTAssertEqual(desk.since.words, "▾ 1 new — open the Inbox", "Sent's list doesn't")
    }
}
