import AppKit
import XCTest

/// r on a message with a reply already saved to it opens that reply, with its words, never a second, empty one: after
/// MailMind opens again too, when all that remembers it is the server, whether the reply is kept on this Mac (the
/// journal, read only or not yet pushed) or already in the account's Drafts. Each test starts from a Composing of its
/// own, as a restart does, so nothing of a session before is there.
@MainActor
final class SavedReplyTests: XCTestCase {
    private let copy = MailCopy(Fixture.row(7))

    /// A fresh Composing whose message names `saved` as its reply draft, and the draft it reopens.
    private func restarted(_ saved: API.ReplyDraft?, reopens draftId: Int = 41,
                           text: String = "Thursday works.") throws -> (Composing, FakeRoutes) {
        let composing = Composing()
        let routes = FakeRoutes()
        let opened = API.MessageOpened(
            account: Fixture.ada, attachments: [], blockedCssUrls: 0, blockedImages: 0, cc: "", date: "Mon",
            folder: "INBOX", fromAddr: "bo@example.test", fromName: "Bo Brown", hasHtml: false, key: 7,
            messageId: "<m7@example.test>", ok: true, replyDraft: saved, replyTo: "", subject: "Lunch",
            text: "Thursday?", to: Fixture.ada, uid: 7)
        routes.answer(API.Get.MessageBody.path, String(decoding: try JSONEncoder().encode(opened), as: UTF8.self))
        let draft = API.DraftOpened(
            account: Fixture.ada, bcc: [], cc: [], draftId: draftId, files: [], inReplyTo: "<m7@example.test>",
            mode: .reply, notes: API.DraftNotes(bccDropped: 0, inline: 0, plain: false), ok: true, originalFrom: "",
            originalKey: 7, participants: [], references: "<m7@example.test>", replyTo: "", split: "",
            subject: "Re: Lunch", tailText: "\n\nThursday?", text: text, to: ["bo@example.test"], version: 3)
        routes.answer(API.Post.DraftResume.path, String(decoding: try JSONEncoder().encode(draft), as: UTF8.self))
        composing.use(routes, digest: Fixture.digest())
        return (composing, routes)
    }

    private func resumes(_ routes: FakeRoutes) -> [JSONValue] {
        routes.calls(to: API.Post.DraftResume.path).map(\.body)
    }

    func testRAfterARestartOpensTheReplyKeptOnThisMacInTheBox() async throws {
        let (composing, routes) = try restarted(API.ReplyDraft(draftId: 41, inDrafts: nil, more: 0))
        composing.openWindow = { _ in XCTFail("no window: the box") }
        composing.inlineSlot = copy

        let boxed = try await replied(composing, copy)
        XCTAssertTrue(composing.inline?.composer === boxed, "in the box")
        XCTAssertEqual(resumes(routes), [.object(["draft_id": .number(41)])], "opened by its id")
        XCTAssertTrue(boxed.body.hasPrefix("Thursday works."), "with its words")
        XCTAssertEqual(boxed.draftId, 41, "the same draft, so its saves go on to it")
        XCTAssertEqual(boxed.to.map(\.value), ["bo@example.test"])
        XCTAssertFalse(boxed.dirty, "nothing to save until an edit")
        XCTAssertNil(composing.notice, "nothing more to say")
        boxed.close(force: true)

        await composing.quickReply(copy, QuickReply.all[0])
        let quick = try XCTUnwrap(composing.inline?.composer)
        XCTAssertTrue(quick.body.hasPrefix("Yes, that works for me."), "a quick reply's line starts a reply of its own")
        XCTAssertNil(quick.draftId)
        XCTAssertEqual(resumes(routes).count, 1, "and leaves the saved one in Drafts")
        quick.close(force: true)
    }

    func testRAfterARestartOpensTheReplyInDraftsByItsCopyAndSaysHowManyMore() async throws {
        let saved = API.ReplyDraft(draftId: nil, inDrafts: API.Copy(folder: "Drafts", key: 9, uid: 3), more: 1)
        let (composing, routes) = try restarted(saved, reopens: 52)
        composing.openWindow = { _ in XCTFail("no window: the box") }
        composing.inlineSlot = copy

        let boxed = try await replied(composing, copy)
        XCTAssertEqual(resumes(routes), [.object(["folder": .string("Drafts"), "key": .number(9), "uid": .number(3)])])
        XCTAssertEqual(boxed.draftId, 52)
        XCTAssertTrue(boxed.body.hasPrefix("Thursday works."))
        XCTAssertEqual(composing.notice?.text, "There’s 1 more draft reply to this message in Drafts.")
        boxed.close(force: true)
    }

    func testReplyAllAfterARestartOpensTheSavedReplyInItsWindowWithTheNote() async throws {
        let (composing, routes) = try restarted(API.ReplyDraft(draftId: 41, inDrafts: nil, more: 2))
        var windows: [UUID] = []
        composing.openWindow = { windows.append($0) }

        let reply = try await replied(composing, copy, all: true)
        XCTAssertEqual(windows, [reply.id], "in a window")
        XCTAssertEqual(resumes(routes).count, 1)
        XCTAssertEqual(reply.draftId, 41)
        XCTAssertEqual(reply.note, "There are 2 more draft replies to this message in Drafts.")
        reply.close(force: true)
    }

    func testASavedReplyThatHasGoneSinceOpensANewOne() async throws {
        let (composing, routes) = try restarted(API.ReplyDraft(draftId: 41, inDrafts: nil, more: 0))
        routes.answer(API.Post.DraftResume.path) { _ in throw FakeRoutes.problem(404, "That draft is no longer in Drafts.") }
        composing.openWindow = { _ in XCTFail("no window: the box") }
        composing.inlineSlot = copy

        let boxed = try await replied(composing, copy)
        XCTAssertEqual(resumes(routes).count, 1, "asked for")
        XCTAssertNil(boxed.draftId, "gone: a new reply")
        XCTAssertEqual(boxed.subject, "Re: Lunch")
        XCTAssertNil(composing.notice, "nothing to say of it")
        boxed.close(force: true)
    }

    func testASavedReplyThatCantOpenSaysItWaitsInDraftsAndOpensNoEmptyReply() async throws {
        let (composing, routes) = try restarted(API.ReplyDraft(draftId: 41, inDrafts: nil, more: 0))
        composing.openWindow = { _ in XCTFail("no reply opens") }
        composing.inlineSlot = copy

        routes.answer(API.Post.DraftResume.path) { _ in
            throw FakeRoutes.problem(503, "Couldn’t load this draft from the mail server. Try again in a moment.")
        }
        let offline = await composing.reply(copy)
        XCTAssertNil(offline)
        XCTAssertNil(composing.inline, "no empty reply in the box")
        XCTAssertEqual(composing.notice?.text, "A draft reply to this message is waiting in Drafts. "
            + "Couldn’t load this draft from the mail server. Try again in a moment.")
        XCTAssertEqual(composing.notice?.refused, true)

        routes.answer(API.Post.DraftResume.path) { _ in
            throw FakeRoutes.problem(409, "That draft has reached the server. Open it from Drafts.")
        }
        let reached = await composing.reply(copy)
        XCTAssertNil(reached)
        XCTAssertNil(composing.inline)
        XCTAssertEqual(composing.notice?.text, "A draft reply to this message is waiting in Drafts. Open it from there.")
    }

    /// In a session, a reply closed in the box opens there again from what the session kept, asking nothing of the
    /// server; once a send or discard has taken it, the message names none and r opens a new one.
    func testInASessionTheBoxKeepsItsOwnAndANewOneOpensOnceNoneIsSaved() async throws {
        let (composing, routes) = try restarted(nil)
        routes.answer(API.Post.Draft.path,
                      #"{"ok": true, "draft_id": 41, "version": 1, "message_id": "<d@example.test>", "pushed": true}"#)
        composing.openWindow = { _ in XCTFail("no window") }
        composing.inlineSlot = copy
        let boxed = try await replied(composing, copy)
        boxed.setBody("Thursday works.", selection: NSRange(location: 15, length: 0))
        XCTAssertTrue(composing.escapeInBox())
        await eventually("closed, saved") { composing.notice?.text == "Saved to Drafts" }

        let again = try await replied(composing, copy)
        XCTAssertEqual(again.draftId, 41)
        XCTAssertTrue(again.body.hasPrefix("Thursday works."))
        XCTAssertTrue(resumes(routes).isEmpty, "the session's own: nothing asked of the server")
        again.close(force: true)
    }

    /// A reply closed in a window and r at once: the message is read only once the closing save has landed, so it
    /// names that reply, never a second one.
    func testRWaitsForTheReplyJustClosedToBeSaved() async throws {
        let (composing, routes) = try restarted(API.ReplyDraft(draftId: 41, inDrafts: nil, more: 0))
        let landed = Landing()
        routes.answer(API.Post.Draft.path) { _ in
            Thread.sleep(forTimeInterval: 0.3)   // a save that takes a moment
            landed.mark()
            return #"{"ok": true, "draft_id": 41, "version": 1, "message_id": "<d@example.test>", "pushed": true}"#
        }
        let body = try messageBody()
        routes.answer(API.Get.MessageBody.path) { _ in
            landed.read()
            return body
        }
        composing.openWindow = { _ in }
        var seed = ComposeSeed()
        seed.mode = .reply
        seed.origKey = 7
        seed.to = ["bo@example.test"]
        seed.subject = "Re: Lunch"
        let first = try XCTUnwrap(composing.open(seed))
        first.setBody("Thursday works.", selection: NSRange(location: 15, length: 0))
        first.close()

        let again = try await replied(composing, copy)
        XCTAssertEqual(landed.readAfterSave, true, "saved, then read")
        XCTAssertEqual(again.draftId, 41)
        again.close(force: true)
    }

    /// What the message answered as `restarted` set it up.
    private func messageBody() throws -> String {
        let opened = API.MessageOpened(
            account: Fixture.ada, attachments: [], blockedCssUrls: 0, blockedImages: 0, cc: "", date: "Mon",
            folder: "INBOX", fromAddr: "bo@example.test", fromName: "Bo Brown", hasHtml: false, key: 7,
            messageId: "<m7@example.test>", ok: true, replyDraft: API.ReplyDraft(draftId: 41, inDrafts: nil, more: 0),
            replyTo: "", subject: "Lunch", text: "Thursday?", to: Fixture.ada, uid: 7)
        return String(decoding: try JSONEncoder().encode(opened), as: UTF8.self)
    }
}

/// Whether a fake save had landed when the message was read.
private final class Landing: @unchecked Sendable {
    private let lock = NSLock()
    private var saved = false
    private var readAt: Bool?
    func mark() { lock.withLock { saved = true } }
    func read() { lock.withLock { if readAt == nil { readAt = saved } } }
    var readAfterSave: Bool? { lock.withLock { readAt } }
}
