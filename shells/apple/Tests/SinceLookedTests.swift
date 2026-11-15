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
}
