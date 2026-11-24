import Foundation
import XCTest

/// While the AI reads mail that waits for its summary (AI's status says `summarizing`, as after an account's first
/// sync), what needs you isn't known yet: the brief's line over the Inbox, the emptied Needs you, the brief's line over
/// its greeting and its Needs you say "Reading your new mail…", as the page's do, rather than that nothing needs you.
/// Once a round lands (`summarized`) and nothing waits, they count what needs you again. With AI off, they say what
/// they always did.
@MainActor
final class SummarizingTests: XCTestCase {
    private static let landed = CoreEvent(id: "1", name: "summarized", data: #"{"count": 2}"#)
    private static let reading = "Reading your new mail…"

    /// A digest with nothing summarized yet, whose AI says whether it is reading what waits (`summarizing`).
    private static func digest(ai: String = "anthropic", summarizing: Bool, items: [API.DigestItem] = []) -> API.Digest {
        var digest = DeskFixture.digest(ai: ai, items: items)
        digest.ai.summarizing = summarizing
        digest.ai.summariesWaiting = summarizing ? 2 : 0
        return digest
    }

    /// Every account's Inbox: two messages, neither summarized, so none needs you yet.
    private func server(ai: String = "anthropic", summarizing: Bool) -> FakeServer {
        let server = Fixture.server(rows: [Fixture.row(1, seen: false), Fixture.row(2)])
        server.on("GET", API.Get.Digest.path, Self.digest(ai: ai, summarizing: summarizing))
        return server
    }

    /// The round landed and nothing waits: the first message needs you, in the Inbox's list and in the digest.
    private func summarize(_ server: FakeServer, needs: Bool = true) {
        let rows = [Fixture.row(1, seen: false, needs: needs, action: needs ? "Reply by Friday" : "",
                                summary: "Asks about Friday."),
                    Fixture.row(2, summary: "For your information.")]
        server.on("GET", API.Get.Inboxes.path) { asked in
            FakeServer.ok(API.InboxesListing(folder: "INBOX", grouped: asked.query["group"] != nil, hasMore: false,
                                             items: rows, offset: 0, ok: true, total: rows.count))
        }
        server.on("GET", API.Get.Digest.path, Self.digest(summarizing: false, items: [
            DeskFixture.item(1, from: "grace.hopper@example.test", name: "Grace Hopper", subject: "Message 1",
                             needs: needs, action: needs ? "Reply by Friday" : ""),
            DeskFixture.item(2, from: "grace.hopper@example.test", name: "Grace Hopper", subject: "Message 2",
                             needs: false, importance: 2),
        ]))
    }

    /// The window on every account's Inbox, at its Needs you tab, with the desk keeping the rail's tallies.
    private func opened(_ server: FakeServer) async -> (MailApp, Desk) {
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        let desk = Desk()
        desk.use(app, routes: server.wire)
        await desk.brief.keepTallied()?.value
        app.place = .allInboxes
        app.openList()
        await until { app.mailbox.facts.read == 2 && app.list?.firstRowsAt != nil }
        XCTAssertEqual(app.mailbox.tab, .needs)
        XCTAssertEqual(desk.brief.needs.count, 0)
        return (app, desk)
    }

    private func line(_ desk: Desk, _ app: MailApp) -> BriefLine? {
        MailboxTable.briefLine(desk.brief, mailbox: app.mailbox)
    }

    func testWhileMailIsReadTheBriefsLineAndTheEmptiedNeedsYouSaySoThenCountWhatNeedsYou() async throws {
        let server = server(summarizing: true)
        let (app, desk) = await opened(server)

        var line = try XCTUnwrap(line(desk, app))
        XCTAssertEqual(line.gist, Self.reading, "not that nothing needs you")
        XCTAssertTrue(line.tally.hasPrefix(Self.reading + " · "), line.tally)
        XCTAssertFalse(line.tally.contains("0 need you"), line.tally)
        XCTAssertEqual(PaneEmptyWords.of(app).title, Self.reading, "the pane beside the emptied Needs you")
        XCTAssertEqual(desk.brief.line(unread: 1, now: Date()), Self.reading + " · 1 unread", "the brief's top line")

        summarize(server)
        app.heard(Self.landed)
        await desk.heard(Self.landed)

        await until { app.mailbox.facts.needs == 1 && desk.brief.needs.count == 1 }
        line = try XCTUnwrap(self.line(desk, app))
        XCTAssertEqual(line.gist, "Grace needs you.")
        XCTAssertTrue(line.tally.hasPrefix("1 need you · "), line.tally)
        XCTAssertEqual(desk.brief.line(unread: 1, now: Date()), "1 needs you · 1 unread")
        withExtendedLifetime(desk) {}
    }

    /// The round landed and none of it needs you: the words say so once it is read, the mailbox's as the brief's.
    func testOnceReadWithNothingThatNeedsYouTheyCallItQuiet() async throws {
        let server = server(summarizing: true)
        let (app, desk) = await opened(server)
        XCTAssertEqual(PaneEmptyWords.of(app).title, Self.reading)

        summarize(server, needs: false)
        app.heard(Self.landed)
        await desk.heard(Self.landed)

        await until { PaneEmptyWords.of(app).title == "Nothing needs you." }
        let line = try XCTUnwrap(line(desk, app))
        XCTAssertEqual(line.gist, "Nothing needs you. The rest can wait.")
        XCTAssertTrue(line.tally.hasPrefix("0 need you · "), line.tally)
        XCTAssertEqual(desk.brief.line(unread: 1, now: Date()), "1 unread")
        withExtendedLifetime(desk) {}
    }

    /// AI off: nothing reads the mail, so the words are what they always were, whatever the status carries.
    func testWithAIOffTheWordsStayAsTheyWere() async throws {
        let server = server(ai: "none", summarizing: true)
        let (app, desk) = await opened(server)

        let line = try XCTUnwrap(line(desk, app))
        XCTAssertEqual(line.gist, "Nothing needs you. The rest can wait.")
        XCTAssertTrue(line.tally.hasPrefix("0 need you · "), line.tally)
        XCTAssertEqual(PaneEmptyWords.of(app).title, "Nothing needs you.")
        XCTAssertEqual(desk.brief.line(unread: 1, now: Date()), "1 unread")
        XCTAssertFalse(desk.brief.summarizing)
        XCTAssertFalse(app.mailbox.summarizing)
        XCTAssertEqual(desk.brief.calm, "Nothing needs you right now.")
        withExtendedLifetime(desk) {}
    }

    /// The brief's Needs you and its narrative, and the emptied Needs you tab, read the same status the line does.
    func testTheBriefsNeedsYouAndTheEmptiedTabSayReadingUntilTheRoundLands() async {
        let server = server(summarizing: true)
        let (app, desk) = await opened(server)
        XCTAssertTrue(desk.brief.summarizing, "the brief's narrative shows the AI reading")
        XCTAssertTrue(app.mailbox.summarizing, "the emptied Needs you tab says it is reading")
        XCTAssertEqual(desk.brief.calm, Self.reading)

        summarize(server, needs: false)
        app.heard(Self.landed)
        await desk.heard(Self.landed)

        await until { !desk.brief.summarizing && !app.mailbox.summarizing }
        XCTAssertEqual(desk.brief.calm, "Nothing needs you right now.")
        withExtendedLifetime(desk) {}
    }

    func testTheCountWordsSayReadingForNoneWhileMailIsRead() {
        XCTAssertEqual(BriefWords.needsNote(0, reading: true), Self.reading)
        XCTAssertEqual(BriefWords.needsNote(0), "Nothing needs you")
        XCTAssertEqual(BriefWords.needsNote(2, reading: true), "2 need you", "a count known is said")
        XCTAssertEqual(BriefWords.update(unread: 0, needs: 0, reading: true), Self.reading)
        XCTAssertEqual(BriefWords.update(unread: 0, needs: 0), "Nothing unread")
        XCTAssertEqual(PaneEmptyWords.of(needsDone: true, hasMail: false, reading: true).title, Self.reading)
        XCTAssertEqual(PaneEmptyWords.of(needsDone: true, hasMail: false).title, "Nothing needs you.")
    }

    /// The status says it only while AI is on, as the server does.
    func testOnlyAnAIThatIsOnReads() {
        var on = Self.digest(summarizing: true).ai
        XCTAssertTrue(on.isSummarizing)
        on.summarizing = nil
        XCTAssertFalse(on.isSummarizing)
        XCTAssertFalse(Self.digest(ai: "none", summarizing: true).ai.isSummarizing)
    }
}
