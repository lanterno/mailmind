import Foundation
import XCTest

/// Quitting with mail still to go: a message in its Undo window is sent first, and a message for later is asked
/// about, since MailMind sends only while it is open.
@MainActor
final class QuittingTests: XCTestCase {
    /// A send as the outbox lists it.
    nonisolated private static func item(_ id: Int, _ state: String, scheduled: Bool = false, retrying: Bool = false) -> String {
        """
        {"id": \(id), "account": "ada@example.test", "to": ["bo@example.test"], "cc": [], "bcc_count": 0,
         "subject": "Dinner", "state": "\(state)", "attempts": 0, "next_attempt_at": 1900000000,
         "scheduled": \(scheduled), "retrying": \(retrying), "unsure": false, "created_at": "2026-11-16 12:00:00"}
        """
    }

    nonisolated private static func outbox(_ items: [String]) -> String {
        #"{"ok": true, "items": [\#(items.joined(separator: ", "))], "drafts": []}"#
    }

    private static func items(_ json: String) -> [API.OutboxItem] {
        StatusFixtures.decode(API.Outbox.self, json).items
    }

    func testThePlanSendsWhatIsInItsUndoWindowAndCountsWhatWaitsForLater() {
        let plan = Quitting.plan(Self.items(Self.outbox([
            Self.item(1, "queued"), Self.item(2, "queued", scheduled: true), Self.item(3, "queued", retrying: true),
            Self.item(4, "sending"), Self.item(5, "sent"), Self.item(6, "error"),
        ])))
        XCTAssertEqual(plan, Quitting.Plan(now: [1], going: [4], later: 2))
    }

    func testWithNothingToGoItQuitsAtOnceAndAsksNothing() async {
        let routes = FakeRoutes()
        routes.answer("/api/outbox", Self.outbox([Self.item(5, "sent")]))
        var asked: [Quitting.Question] = []
        let quit = await Quitting.settle(routes, ask: { asked.append($0); return true }, say: { _ in })
        XCTAssertTrue(quit)
        XCTAssertEqual(asked, [])
        XCTAssertEqual(routes.calls(to: "/api/outbox/sendnow").count, 0)
    }

    func testAMessageInItsUndoWindowIsSentNowAndQuittingWaitsForIt() async {
        let routes = FakeRoutes()
        let reads = Reads()
        routes.answer("/api/outbox") { _ in
            // Queued until the send now has been asked, then sending, then sent.
            switch reads.next() {
            case 0: Self.outbox([Self.item(1, "queued")])
            case 1: Self.outbox([Self.item(1, "sending")])
            default: Self.outbox([Self.item(1, "sent")])
            }
        }
        routes.answer("/api/outbox/sendnow", #"{"ok": true}"#)
        var said: [String] = []
        let quit = await Quitting.settle(routes, ask: { _ in XCTFail("nothing to ask"); return false },
                                         say: { said.append($0) }, poll: .milliseconds(10))
        XCTAssertTrue(quit)
        XCTAssertEqual(routes.calls(to: "/api/outbox/sendnow").map(\.body), [.object(["id": .number(1)])])
        XCTAssertEqual(reads.count, 3, "it waited until the message had gone")
        XCTAssertEqual(said, ["Sending before MailMind quits…"])
    }

    func testAMessageForLaterIsAskedAboutFirstAndNoMeansStayWithNothingSent() async {
        let routes = FakeRoutes()
        routes.answer("/api/outbox", Self.outbox([Self.item(1, "queued"), Self.item(2, "queued", scheduled: true)]))
        routes.answer("/api/outbox/sendnow", #"{"ok": true}"#)
        var asked: [Quitting.Question] = []
        let quit = await Quitting.settle(routes, ask: { asked.append($0); return false }, say: { _ in })
        XCTAssertFalse(quit, "Don’t Quit keeps MailMind open")
        XCTAssertEqual(asked, [.later(count: 1)])
        XCTAssertEqual(routes.calls(to: "/api/outbox/sendnow").count, 0,
                       "what was in its Undo window keeps its Undo")
    }

    func testQuitAnywayLeavesAMessageForLaterQueuedForTheNextOpen() async {
        let routes = FakeRoutes()
        routes.answer("/api/outbox", Self.outbox([Self.item(2, "queued", scheduled: true)]))
        let quit = await Quitting.settle(routes, ask: { _ in true }, say: { _ in })
        XCTAssertTrue(quit)
        XCTAssertEqual(routes.calls(to: "/api/outbox/sendnow").count, 0, "a scheduled message keeps its time")
    }

    func testAMessageThatHasntGoneWhenTheWaitRunsOutIsAskedAbout() async {
        let routes = FakeRoutes()
        routes.answer("/api/outbox", Self.outbox([Self.item(1, "sending")]))
        var asked: [Quitting.Question] = []
        let quit = await Quitting.settle(routes, ask: { asked.append($0); return true }, say: { _ in },
                                         wait: .milliseconds(80), poll: .milliseconds(10))
        XCTAssertTrue(quit)
        XCTAssertEqual(asked, [.stillGoing])
    }

    // MARK: What is being written

    /// Routes that take a draft's save, or refuse it, and list `outbox`.
    private func writingRoutes(outbox: [String] = [], saves: Bool = true) -> FakeRoutes {
        let routes = FakeRoutes()
        routes.answer("/api/contacts/known", #"{"ok": true, "thin": false, "bad": [], "items": {}}"#)
        routes.answer("/api/draft") { _ in
            guard saves else { throw FakeRoutes.problem(502, "The server is away") }
            return #"{"ok": true, "draft_id": 41, "version": 1, "message_id": "<d@example.test>", "pushed": true}"#
        }
        routes.answer("/api/outbox", Self.outbox(outbox))
        return routes
    }

    /// The composers' host: a composer holds it unowned, so the test keeps it.
    private var host: FakeHost?

    /// A composer with nothing in it, or with `text` typed into it since its last save.
    private func writing(_ text: String?, _ routes: FakeRoutes) -> Composer {
        let host = host ?? FakeHost(routes: routes)
        self.host = host
        var seed = ComposeSeed()
        if text != nil {
            seed.to = ["bo@example.test"]
            seed.subject = "Plans"
        }
        let composer = Composer(seed, host: host)
        if let text { composer.setBody(text, selection: NSRange(location: (text as NSString).length, length: 0)) }
        addTeardownBlock { @MainActor in composer.close(force: true) }
        return composer
    }

    /// The reader's reply and a compose window, each typed in since its last autosave: both are saved as drafts before
    /// MailMind goes, and stay open in case a question that follows keeps it open.
    func testWhatIsBeingWrittenIsSavedAsADraftBeforeQuitting() async {
        let routes = writingRoutes()
        let reply = writing("Typed just now", routes)
        let window = writing("Half a letter", routes)
        let idle = writing(nil, routes)
        let quit = await Quitting.settle(routes, writing: [reply, window, idle],
                                         ask: { _ in XCTFail("nothing to ask"); return false }, say: { _ in })
        XCTAssertTrue(quit)
        let saved = routes.calls(to: "/api/draft").compactMap { $0.body["text"]?.string }
        XCTAssertEqual(Set(saved), ["Typed just now", "Half a letter"], "one save each, none for a composer with nothing in it")
        XCTAssertFalse(reply.dirty)
        XCTAssertFalse(window.dirty)
        XCTAssertFalse(reply.closed, "open still, as Don’t Quit would leave it")
        XCTAssertEqual(routes.calls(to: "/api/outbox").count, 1, "then the outbox, as before")
    }

    /// A read-only run sends nothing, but what is being written is still kept on this Mac as a draft.
    func testAReadOnlyRunSavesWhatIsBeingWrittenAndLooksAtNoOutbox() async {
        let routes = writingRoutes(outbox: [Self.item(1, "queued", scheduled: true)])
        let reply = writing("Kept here", routes)
        let quit = await Quitting.settle(routes, writing: [reply], readOnly: true,
                                         ask: { _ in XCTFail("nothing to ask"); return false }, say: { _ in })
        XCTAssertTrue(quit)
        XCTAssertEqual(routes.calls(to: "/api/draft").count, 1)
        XCTAssertEqual(routes.calls(to: "/api/outbox").count, 0)
    }

    /// A draft that couldn't be saved is asked about: Don't Quit keeps MailMind open with the words where they were.
    func testWritingThatCouldntBeSavedIsAskedAbout() async {
        let routes = writingRoutes(saves: false)
        let reply = writing("Not lost", routes)
        var asked: [Quitting.Question] = []
        let stay = await Quitting.settle(routes, writing: [reply], ask: { asked.append($0); return false }, say: { _ in })
        XCTAssertFalse(stay)
        XCTAssertEqual(asked, [.unsaved])
        XCTAssertEqual(reply.body, "Not lost")
        XCTAssertFalse(reply.closed)
        XCTAssertEqual(routes.calls(to: "/api/outbox").count, 0, "nothing more until MailMind is quit again")
        let words = Quitting.words(.unsaved)
        XCTAssertEqual(words.text,
                       "A message you’re writing couldn’t be saved as a draft. If you quit now, what you wrote is lost.")
        XCTAssertEqual(words.quit, "Quit Anyway")
    }

    func testTheQuestionSaysItSendsWhenMailMindIsNextOpen() {
        let one = Quitting.words(.later(count: 1))
        XCTAssertEqual(one.title, "Quit anyway?")
        XCTAssertEqual(one.text, "A message is waiting to send later. It sends when MailMind is next open.")
        XCTAssertEqual(one.quit, "Quit Anyway")
        XCTAssertEqual(Quitting.words(.later(count: 3)).text,
                       "3 messages are waiting to send later. They send when MailMind is next open.")
        XCTAssertTrue(Quitting.words(.stillGoing).text.hasSuffix("It sends when MailMind is next open."))
    }
}

/// Counts the outbox's reads, from any thread.
private final class Reads: @unchecked Sendable {
    private let lock = NSLock()
    private var made = 0

    var count: Int { lock.withLock { made } }

    func next() -> Int {
        lock.withLock {
            defer { made += 1 }
            return made
        }
    }
}
