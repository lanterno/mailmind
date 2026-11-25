import AppKit
import XCTest

/// What the app does through its seams with a test's own in them (`TestSeams`). Time first: a test moves the clock,
/// never waits on the Mac's, so a toast's life, Undo Send's window and a moment's grace are each exact, and a short
/// delay is busy until it is run while a long one waits for the test. Then a link opened and a paste, which reach
/// neither the Mac's browser nor its pasteboard.
@MainActor
final class AppSeamsTests: XCTestCase {
    private var seams: TestSeams!

    override func setUp() async throws {
        seams = TestSeams()
    }

    override func tearDown() async throws {
        seams.restore()
        seams = nil
    }

    private var clock: TestClock { seams.clock }

    /// The main window's toast stays its 2.6 seconds, a long delay: settling never ends it, only the clock moved past.
    func testAToastStaysItsLifeAndGoesOnTheMinute() async {
        let composing = Composing()
        composing.say("Draft saved")
        await clock.advance(by: .zero)
        XCTAssertEqual(composing.notice?.text, "Draft saved")
        XCTAssertEqual(clock.pendingShortDelays, [], "a toast's life isn't the app being busy")
        await clock.runShortDelays()
        await clock.advance(by: .milliseconds(2599))
        XCTAssertEqual(composing.notice?.text, "Draft saved", "still there a moment before its time")
        await clock.advance(by: .milliseconds(1))
        XCTAssertNil(composing.notice)
    }

    func testANewToastStartsItsLifeAgain() async {
        let composing = Composing()
        composing.say("Draft saved")
        await clock.advance(by: .seconds(2))
        composing.say("Moved to Archive")
        await clock.advance(by: .seconds(2))
        XCTAssertEqual(composing.notice?.text, "Moved to Archive", "the first's end doesn't take the second")
        await clock.advance(by: .milliseconds(600))
        XCTAssertNil(composing.notice)
    }

    /// The reply box the reader stops offering is let go a quarter of a second later, unless the reader offers it
    /// again in between (a view drawn again): a short delay, which settling runs at once.
    func testAReplyBoxWithdrawnIsLetGoAMomentLaterUnlessOfferedAgain() async {
        let composing = Composing()
        let copy = MailCopy(Fixture.row(7))
        composing.offerInline(copy)
        composing.withdrawInline(copy)
        XCTAssertEqual(composing.inlineSlot, copy, "not at once")
        XCTAssertEqual(clock.pendingShortDelays, [.milliseconds(250)], "busy for that moment")
        await clock.runShortDelays()
        XCTAssertNil(composing.inlineSlot)

        composing.offerInline(copy)
        composing.withdrawInline(copy)
        composing.offerInline(copy)
        await clock.runShortDelays()
        XCTAssertEqual(composing.inlineSlot, copy, "offered again before the moment was up")
    }

    /// Undo Send's window counts down a second at a time. The seconds are beats, never busy, so settling leaves the
    /// message in its window; moving the clock through them counts down, sends, and the float goes 2.4 s later.
    func testUndoSendCountsDownOnTheClockAndOnlyTheTestMovesPastIt() async throws {
        let composing = Composing()
        let routes = FakeRoutes()
        routes.answer(API.Post.Send.path, #"""
            {"ok": true, "outbox_id": 31, "queued": true, "recipients": 1, "scheduled": false, "undo_seconds": 10}
            """#)
        composing.use(routes, digest: Fixture.digest())
        composing.openWindow = { _ in }
        var seed = ComposeSeed()
        seed.mode = .reply
        seed.to = ["bo@example.test"]
        seed.subject = "Re: Plans"
        seed.origKey = 7
        seed.body = "Yes, Thursday."
        let composer = try XCTUnwrap(composing.open(seed))
        composer.send()
        await eventually("the send to be queued") {
            if case .countdown? = composing.float { return true }
            return false
        }
        XCTAssertEqual(composing.float, .countdown(id: 31, left: 10, total: 10))

        await clock.runShortDelays()
        XCTAssertEqual(composing.float, .countdown(id: 31, left: 10, total: 10), "a settle never runs the window out")

        await clock.advance(by: .seconds(3))
        XCTAssertEqual(composing.float, .countdown(id: 31, left: 7, total: 10))
        await clock.advance(by: .seconds(7))
        XCTAssertEqual(composing.float, .done("✓ Sent · 1 recipient", clock: false))
        await clock.advance(by: .milliseconds(2400))
        XCTAssertNil(composing.float)
    }

    // MARK: The other seams

    /// A link clicked in mail goes to the links seam: recorded under a test, never opened in the Mac's browser.
    func testALinkInMailOpensThroughTheSeamNeverTheMacsBrowser() {
        let link = FrameLink(href: "https://example.test/agenda", text: "https://example.test/agenda")
        LinkOpener.open(link, senderDomain: "example.test", in: nil)
        XCTAssertEqual(seams.links.opened, [URL(string: "https://example.test/agenda")!])
    }

    /// Pasting into a message reads the pasteboard seam, a test's own, and cleans a tracked link as it goes in.
    func testAPasteReadsThePasteboardInTheSeam() {
        seams.pasteboard.clearContents()
        seams.pasteboard.setString("https://example.test/a?utm_source=news&id=4", forType: .string)
        let text = BodyTextView(frame: CGRect(x: 0, y: 0, width: 400, height: 100))
        text.paste(nil)
        XCTAssertEqual(text.string, "https://example.test/a?id=4")
    }
}
