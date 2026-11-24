import Foundation
import XCTest

/// A message sent for later waits in Drafts until it goes: its row says when, in the page's words for a scheduled
/// send, and the reader offers to cancel the send, which keeps the draft, or to edit it, which cancels it first.
@MainActor
final class ScheduledTests: XCTestCase {
    private static let at = 1_795_000_000

    private static func item(id: Int = 9, account: String = Fixture.ada, subject: String = "Second try",
                             to: String = "lin@example.test", state: String = "queued", scheduled: Bool = true,
                             draft: Int? = 4) -> String
    {
        """
        {"id": \(id), "account": "\(account)", "subject": "\(subject)", "to": ["\(to)"], "cc": [], "bcc_count": 0,
         "state": "\(state)", "attempts": 0, "error": null, "unsure": false, "created_at": "2026-11-18 12:00:00",
         "sent_at": null, "next_attempt_at": \(at), "scheduled": \(scheduled), "retrying": false,
         "draft_id": \(draft.map(String.init) ?? "null")}
        """
    }

    private static func outbox(_ items: [String]) -> String {
        #"{"ok": true, "drafts": [], "items": ["# + items.joined(separator: ", ") + "]}"
    }

    private func composing(_ routes: FakeRoutes) async -> Composing {
        let composing = Composing()
        composing.use(routes, digest: Fixture.digest())
        await composing.readScheduled()
        return composing
    }

    func testOnlyWhatWaitsForATimeAPersonChoseIsScheduled() async {
        let routes = FakeRoutes()
        routes.answer(API.Get.Outbox.path, Self.outbox([
            Self.item(), Self.item(id: 10, subject: "Gone out", state: "sent"),
            Self.item(id: 11, subject: "Trying again", scheduled: false),
        ]))
        let composing = Composing()
        composing.use(routes, digest: Fixture.digest())
        var redrawn = 0
        composing.scheduledChanged = { redrawn += 1 }
        await composing.readScheduled()
        XCTAssertEqual(composing.scheduled.map(\.id), [9])
        XCTAssertEqual(redrawn, 1, "the list's rows are drawn again")
        await composing.readScheduled()
        XCTAssertEqual(redrawn, 1, "nothing changed: they aren't drawn again")
    }

    func testADraftWaitingToGoLaterIsKnownByItsAccountSubjectAndRecipients() async {
        let routes = FakeRoutes()
        routes.answer(API.Get.Outbox.path, Self.outbox([Self.item()]))
        let composing = await composing(routes)
        XCTAssertEqual(composing.scheduledSend(account: Fixture.ada, subject: " Second try ")?.id, 9)
        XCTAssertEqual(composing.scheduledSend(account: Fixture.ada, subject: "Second try", to: "Lin <LIN@example.test>")?.id,
                       9)
        XCTAssertNil(composing.scheduledSend(account: Fixture.ada, subject: "Second try", to: "bo@example.test"),
                     "another draft with the same subject")
        XCTAssertNil(composing.scheduledSend(account: Fixture.work, subject: "Second try"), "another account's")
        XCTAssertNil(composing.scheduledSend(account: Fixture.ada, subject: "Half written"))

        XCTAssertEqual(composing.scheduledWhen(Fixture.row(1, folder: "Drafts", subject: "Second try")),
                       "Scheduled · \(SendTimes.words(Self.at))")
        XCTAssertNil(composing.scheduledWhen(Fixture.row(2, folder: "Drafts", subject: "Half written")))
    }

    func testTheReaderKnowsADraftWaitingToGoLaterByTheDraftTheOutboxNames() async {
        let routes = FakeRoutes()
        routes.answer(API.Get.Outbox.path, Self.outbox([
            Self.item(), Self.item(id: 10, subject: "Second try", draft: 5), Self.item(id: 11, draft: nil),
        ]))
        let composing = await composing(routes)
        XCTAssertEqual(composing.scheduledSend(draftId: 4)?.id, 9)
        XCTAssertEqual(composing.scheduledSend(draftId: 5)?.id, 10, "another draft with the same subject is its own")
        XCTAssertNil(composing.scheduledSend(draftId: 6), "a draft no send goes from")
        XCTAssertNil(composing.scheduledSend(draftId: nil), "a draft MailMind doesn't keep: no send goes from it")
    }

    func testTheWords() {
        XCTAssertEqual(ScheduledWords.row(Self.at), "Scheduled · \(SendTimes.words(Self.at))",
                       "the page's words for a scheduled send")
        XCTAssertTrue(ScheduledWords.line(Self.at).hasPrefix("Scheduled for \(SendTimes.words(Self.at))"))
        XCTAssertEqual(ScheduledWords.plain("(no subject)"), "", "the outbox's word for a subject left empty")
        XCTAssertEqual(ScheduledWords.plain("  Plans \n"), "Plans")
    }

    func testCancelSendKeepsTheDraftAndSaysSo() async throws {
        let routes = FakeRoutes()
        routes.answer(API.Get.Outbox.path, Self.outbox([Self.item()]))
        routes.answer(API.Post.OutboxCancel.path, #"{"ok": true}"#)
        let composing = await composing(routes)
        let item = try XCTUnwrap(composing.scheduled.first)
        routes.answer(API.Get.Outbox.path, Self.outbox([]))

        let cancelled = await composing.cancelScheduled(item)

        XCTAssertTrue(cancelled)
        XCTAssertEqual(routes.calls(to: API.Post.OutboxCancel.path).last?.body["id"], .number(9))
        XCTAssertEqual(composing.notice, ToastWords(text: "Send cancelled — kept in Drafts"), "says where its words are")
        XCTAssertTrue(routes.calls(to: API.Post.Draft.path).isEmpty, "the draft stays in Drafts")
        await eventually("the outbox read again") { composing.scheduled.isEmpty }
    }

    func testEditCancelsTheSendFirstAndOneTooLateOpensNothing() async throws {
        let routes = FakeRoutes()
        routes.answer(API.Get.Outbox.path, Self.outbox([Self.item()]))
        routes.answer(API.Post.OutboxCancel.path) { _ in throw FakeRoutes.problem(409, "It is being sent") }
        let composing = await composing(routes)
        let item = try XCTUnwrap(composing.scheduled.first)
        let copy = MailCopy(key: 1, folder: "Drafts", uid: 1, account: Fixture.ada)

        await composing.editScheduled(item, copy: copy)
        XCTAssertEqual(composing.notice, ToastWords(text: "Too late to cancel", refused: true))
        XCTAssertTrue(routes.calls(to: API.Post.DraftResume.path).isEmpty, "a message on its way isn't opened to change")

        routes.answer(API.Post.OutboxCancel.path, #"{"ok": true}"#)
        await composing.editScheduled(item, copy: copy)
        XCTAssertEqual(routes.calls(to: API.Post.OutboxCancel.path).count, 2)
        XCTAssertEqual(routes.calls(to: API.Post.DraftResume.path).count, 1, "cancelled, it opens as a draft does")
    }
}
