import Foundation
import XCTest

/// One composer's life, against routes the test answers: what it saves and when, what holds a send, what a fix
/// does, and what it hands over. The page's `machine.test.ts` holds the same.
@MainActor
final class ComposerTests: XCTestCase {
    private var routes: FakeRoutes!
    private var host: FakeHost!
    private var open: [Composer] = []

    override func setUp() async throws {
        routes = FakeRoutes()
        host = FakeHost(routes: routes)
        routes.answer("/api/contacts/known", #"{"ok": true, "thin": false, "bad": [], "items": {}}"#)
        let fake = routes!
        routes.answer("/api/draft") { [unowned fake] _ in
            let n = fake.calls(to: "/api/draft").count   // the version this save makes
            return #"{"ok": true, "draft_id": 41, "version": \#(n), "message_id": "<d@example.test>", "pushed": true}"#
        }
    }

    override func tearDown() async throws {
        for composer in open { composer.close(force: true) }   // their timers stop with them
        open = []
    }

    private func composer(_ seed: ComposeSeed = ComposeSeed(), edited: Bool = false) -> Composer {
        let made = Composer(seed, host: host, edited: edited)
        open.append(made)
        return made
    }

    private func written(_ text: String, to who: [String] = ["bo@example.test"], subject: String = "Plans") -> Composer {
        var seed = ComposeSeed()
        seed.to = who
        seed.subject = subject
        let made = composer(seed)
        made.setBody(text, selection: NSRange(location: (text as NSString).length, length: 0))
        return made
    }

    // MARK: Starting

    func testANewMessageStartsAboveYourSignatureWithNothingToSave() async {
        host.signatures["ada@example.test"] = "Ada"
        let fresh = composer()
        XCTAssertEqual(fresh.from, "ada@example.test")
        XCTAssertEqual(fresh.body, "\n\n-- \nAda")
        XCTAssertTrue(fresh.nothingToSave)
        let saved = await fresh.save().value
        XCTAssertNil(saved, "nothing to save")
        XCTAssertEqual(fresh.notice, "Nothing to save yet")
        XCTAssertTrue(routes.calls(to: "/api/draft").isEmpty, "Drafts isn't littered with an empty message")
    }

    func testAnAccountThatCantSendIsntChosen() {
        var seed = ComposeSeed()
        seed.from = "zed@example.test"
        XCTAssertEqual(composer(seed).from, "ada@example.test")
    }

    func testChangingFromSwapsAnUntouchedSignatureAndKeepsAnEditedOne() {
        host.senders = ["ada@example.test", "ada@work.example.test"]
        host.signatures = ["ada@example.test": "Ada", "ada@work.example.test": "Ada Lovelace, Work"]
        let swapped = composer()
        swapped.setBody("Hi\n\n-- \nAda", selection: NSRange(location: 2, length: 0))
        swapped.setFrom("ada@work.example.test")
        XCTAssertEqual(swapped.body, "Hi\n\n-- \nAda Lovelace, Work")
        let kept = composer()
        kept.setBody("Hi\n\n-- \nAda, of course", selection: NSRange(location: 2, length: 0))
        kept.setFrom("ada@work.example.test")
        XCTAssertEqual(kept.body, "Hi\n\n-- \nAda, of course", "a signature you edited is yours")
    }

    // MARK: Saving

    func testASaveSendsWhatTheServerTakesAndTheNextNamesTheDraft() async throws {
        let draft = written("See you at noon")
        draft.setTyped(.cc, "cy@example.test")
        let first = await draft.save().value
        XCTAssertEqual(first, true)
        XCTAssertEqual(draft.draftId, 41)
        XCTAssertFalse(draft.dirty)
        XCTAssertEqual(draft.notice, "Draft saved")
        XCTAssertEqual(draft.recipients(.cc).map(\.value), ["cy@example.test"], "a save takes what was being typed")
        let body = try XCTUnwrap(routes.calls(to: "/api/draft").first?.body)
        XCTAssertEqual(body["to"]?.strings, ["bo@example.test"])
        XCTAssertEqual(body["cc"]?.strings, ["cy@example.test"])
        XCTAssertEqual(body["subject"]?.string, "Plans")
        XCTAssertEqual(body["text"]?.string, "See you at noon")
        XCTAssertEqual(body["idempotency_key"]?.string, draft.idempotencyKey)
        XCTAssertNil(body["draft_id"], "a first save names no draft")
        draft.setSubject("Plans, again")
        _ = await draft.save().value
        let second = try XCTUnwrap(routes.calls(to: "/api/draft").last?.body)
        XCTAssertEqual(second["draft_id"]?.number, 41)
        XCTAssertEqual(second["base_version"]?.number, 1, "the version it saved over")
    }

    func testOneComposersSavesGoInTurn() async throws {
        let draft = written("First")
        let first = draft.save(silent: true)
        draft.setBody("Second", selection: NSRange(location: 6, length: 0))
        let second = draft.save(silent: true)
        _ = await first.value
        _ = await second.value
        let sent = routes.calls(to: "/api/draft")
        XCTAssertEqual(sent.count, 2)
        XCTAssertNil(sent[0].body["draft_id"])
        XCTAssertEqual(sent[1].body["draft_id"]?.number, 41, "the second waited for the first's draft")
        XCTAssertEqual(sent[1].body["text"]?.string, "Second")
    }

    func testAnEditDuringASaveStaysToSave() async {
        let draft = written("First")
        let saving = draft.save(silent: true)
        draft.setBody("First, and more", selection: NSRange(location: 15, length: 0))
        _ = await saving.value
        XCTAssertTrue(draft.dirty)
    }

    func testADraftChangedInAnotherWindowIsntOverwrittenByAutosave() async {
        routes.answer("/api/draft") { _ in throw FakeRoutes.problem(409, "Changed elsewhere") }
        let draft = written("Mine")
        let saved = await draft.save().value
        XCTAssertEqual(saved, false)
        XCTAssertTrue(draft.conflict)
        XCTAssertEqual(draft.savedLine, "Changed in another window")
        XCTAssertEqual(draft.notice, "This draft changed in another window. Save again to keep this text.")
        let before = routes.calls(to: "/api/draft").count
        draft.autosaveTick()
        XCTAssertEqual(routes.calls(to: "/api/draft").count, before, "autosave leaves it to you")
    }

    func testAFailedSaveSaysSoAndNeverLeavesSaved() async {
        routes.answer("/api/draft") { _ in throw FakeRoutes.problem(502, "The server is away") }
        let draft = written("Mine")
        _ = await draft.save().value
        XCTAssertEqual(draft.savedLine, "Not saved")
        XCTAssertEqual(draft.notice, "Couldn’t save draft: The server is away")
    }

    func testClosingSavesWhatsUnsavedAndKeepsItsWords() async {
        let draft = written("Unsaved words")
        draft.close()
        XCTAssertTrue(draft.closed)
        XCTAssertEqual(host.closedCount, 1)
        let closing = try? XCTUnwrap(host.closings.first)
        XCTAssertEqual(closing?.snapshot.body, "Unsaved words")
        XCTAssertEqual(closing?.snapshot.to, ["bo@example.test"])
        let saved = await closing?.save.value
        XCTAssertEqual(saved, true)
    }

    func testDiscardThrowsAwayTheServersCopyToo() async throws {
        let draft = written("Gone soon")
        _ = await draft.save().value
        await draft.discard()
        XCTAssertTrue(draft.closed)
        let last = try XCTUnwrap(routes.calls(to: "/api/draft").last?.body)
        XCTAssertEqual(last["delete"], .bool(true))
        XCTAssertEqual(last["draft_id"]?.number, 41)
        XCTAssertTrue(host.closings.isEmpty, "nothing left to save")
    }

    // MARK: Sending

    func testNobodyToSendToIsAHardLine() {
        let draft = written("Hello", to: [])
        draft.send()
        XCTAssertEqual(draft.check?.keys, ["norcpt"])
        XCTAssertEqual(draft.check?.hard, true)
        host.clockNow += 5
        draft.confirmGuard()
        XCTAssertFalse(draft.closed, "a hard line has no way past it")
        XCTAssertTrue(host.sent.isEmpty)
    }

    func testABadChipIsNamedAndItsFixPutsItBackToEdit() {
        let draft = written("Hello", to: [])
        draft.commitChip(.to, "bo@")
        draft.send()
        XCTAssertEqual(draft.check?.lines.first?.text, "“bo@” isn’t a valid address.")
        let line = draft.check!.lines[0]
        draft.fix(line.fix!, field: line.field)
        XCTAssertNil(draft.check)
        XCTAssertEqual(draft.typed[.to], "bo@")
        XCTAssertTrue(draft.to.isEmpty)
        XCTAssertEqual(draft.focusRequest, .field(.to))
    }

    func testAConfirmLineWaitsBeforeItCanBeWavedThrough() {
        let draft = written("Hello", subject: "")
        draft.send()
        XCTAssertEqual(draft.check?.keys, ["subject"])
        XCTAssertEqual(draft.check?.armed, false)
        draft.confirmGuard()
        XCTAssertFalse(draft.closed, "too soon: a second Return isn't a decision")
        host.clockNow += SendGuard.armSeconds
        draft.confirmGuard(repeated: true)
        XCTAssertFalse(draft.closed, "a held key never confirms")
        draft.confirmGuard()
        XCTAssertTrue(draft.closed)
        XCTAssertEqual(host.sent.count, 1)
        XCTAssertEqual(host.sent.first?.body.subject, "")
    }

    func testAnEditRetiresTheCheck() {
        let draft = written("Hello", subject: "")
        draft.send()
        XCTAssertNotNil(draft.check)
        draft.setSubject("Now it has one")
        XCTAssertNil(draft.check)
        draft.send()
        XCTAssertTrue(draft.closed)
    }

    func testASendHandsOverItsBodyAndClosesWithNothingToSave() {
        var seed = ComposeSeed()
        seed.mode = .reply
        seed.to = ["bo@example.test"]
        seed.subject = "Re: Plans"
        seed.origKey = 7
        let reply = composer(seed)
        reply.setBody("Yes.", selection: NSRange(location: 4, length: 0))
        reply.send()
        XCTAssertTrue(reply.closed)
        XCTAssertTrue(host.closings.isEmpty, "a sent message isn't saved as it closes")
        let sent = try? XCTUnwrap(host.sent.first)
        XCTAssertEqual(sent?.replyKey, 7)
        XCTAssertEqual(sent?.body.text, "Yes.")
        XCTAssertEqual(sent?.body.mode, .reply)
        XCTAssertEqual(sent?.body.originalKey, 7)
        XCTAssertEqual(sent?.body.idempotencyKey, reply.idempotencyKey)
        XCTAssertEqual(sent?.after, 0)
    }

    func testAPassedTimeHoldsALaterSendAndItsFixPicksAnother() {
        let draft = written("Later")
        draft.send(after: host.now() - 60)
        XCTAssertEqual(draft.check?.lines.map(\.key), ["late"])
        XCTAssertEqual(draft.check?.hard, true)
        let line = draft.check!.lines[0]
        draft.fix(line.fix!, field: nil)
        XCTAssertTrue(draft.laterOpen)
        draft.send(after: host.now() + 3600)
        XCTAssertEqual(host.sent.first?.after, host.now() + 3600)
    }

    func testReplyAllsFixRemovesOnlyThoseCopiedFromTheOriginal() {
        var seed = ComposeSeed()
        seed.mode = .replyall
        seed.to = ["bo@example.test"]
        seed.cc = (1...6).map { "p\($0)@example.test" }
        seed.autoCc = (1...6).map { "p\($0)@example.test" }
        seed.subject = "Re: Plans"
        seed.origKey = 7
        let reply = composer(seed)
        reply.commitChip(.cc, "mine@example.test")
        reply.setBody("Thanks all", selection: NSRange(location: 10, length: 0))
        reply.send()
        let line = reply.check!.lines[0]
        XCTAssertEqual(line.text, "This reply goes to 8 people.")
        XCTAssertEqual(line.fix?.label, "Remove the 6 copied from the original")
        reply.fix(line.fix!, field: nil)
        XCTAssertEqual(reply.recipients(.cc).map(\.value), ["mine@example.test"])
        XCTAssertEqual(reply.mode, .reply)
        XCTAssertEqual(host.announced.last, "Removed 6 people. This reply goes to 2 people.")
    }

    func testAnAddressOneTypoAwayIsSwappedForTheOneYouMeant() async {
        routes.answer("/api/contacts/known",
                      #"{"ok": true, "thin": false, "bad": [], "items": {"jon@example.test": {"known": false, "self": false, "near": "john@example.test"}}}"#)
        let draft = written("Hi", to: ["Jon <jon@example.test>"])
        await eventually("the lookup's answer") { draft.nearMisses().count == 1 }
        draft.send()
        let line = draft.check!.lines[0]
        XCTAssertEqual(line.key, "near")
        draft.fix(line.fix!, field: nil)
        XCTAssertEqual(draft.to.map(\.value), ["Jon <john@example.test>"], "the chip keeps its name and takes the address")
    }

    func testSomeoneNewIsMarkedAndNeverHoldsTheSend() async {
        routes.answer("/api/contacts/known",
                      #"{"ok": true, "thin": false, "bad": [], "items": {"new@example.test": {"known": false, "self": false}}}"#)
        let draft = written("Hi")
        draft.commitChip(.to, "new@example.test")
        await eventually("the lookup's answer") { draft.isNew("new@example.test") }
        XCTAssertFalse(draft.isNew("bo@example.test"), "unasked is unmarked")
        XCTAssertEqual(host.announced.last, "New to you: new@example.test.")
        draft.send()
        XCTAssertTrue(draft.closed, "a soft line alone never stops it")
    }

    // MARK: Snippets and pictures

    func testTabBringsInASnippetThenGoesThroughItsPlaceholders() {
        host.snippets = [API.Snippet(text: "Best, {{name}} at {{place}}", trigger: "sig")]
        let draft = composer()
        draft.setBody("Hi ;sig", selection: NSRange(location: 7, length: 0))
        XCTAssertEqual(draft.offer?.trigger, "sig")
        XCTAssertTrue(draft.tabInBody(back: false))
        XCTAssertEqual(draft.body, "Hi Best, {{name}} at {{place}}")
        XCTAssertEqual(draft.selectionRequest, NSRange(location: 9, length: 8), "on the first placeholder")
        draft.caretMoved(NSRange(location: 9, length: 8))
        XCTAssertTrue(draft.tabInBody(back: false))
        XCTAssertEqual(draft.selectionRequest, NSRange(location: 21, length: 9))
        draft.caretMoved(NSRange(location: 21, length: 9))
        XCTAssertTrue(draft.tabInBody(back: true))
        XCTAssertEqual(draft.selectionRequest, NSRange(location: 9, length: 8), "and back")
        draft.caretMoved(NSRange(location: 30, length: 0))
        XCTAssertFalse(draft.tabInBody(back: false), "past the last it is Tab again")
    }

    func testAPictureGoesIntoTheTextWhereTheCursorIsAndLeavesWithItsFile() async {
        let draft = written("Look: ")
        draft.caretMoved(NSRange(location: 6, length: 0))
        draft.add([.data(name: "cat.png", type: "image/png", Data([1, 2, 3]))], inText: true)
        XCTAssertTrue(draft.body.hasPrefix("Look: ![cat.png](cid:"))
        XCTAssertEqual(draft.reading.map(\.name), ["cat.png"])
        await eventually("the picture read") { draft.reading.isEmpty }
        XCTAssertEqual(draft.attachments.count, 1)
        XCTAssertNotNil(draft.attachments[0].cid)
        XCTAssertEqual(draft.attachments[0].base64, "AQID")
        draft.removeAttachment(at: 0)
        XCTAssertEqual(draft.body, "Look: ", "its mark goes with it")
    }

    func testAFileStillReadingHoldsTheSend() {
        let draft = written("see attached")
        let missing = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("not-there-\(UUID()).pdf")
        draft.add([.file(missing)])
        draft.send()
        XCTAssertEqual(draft.check?.lines.map(\.key), ["reading"])
    }

    func testAPreviewShowsTheServersHTMLWithPicturesAsData() async throws {
        routes.answer("/api/compose/preview") { body in
            XCTAssertEqual(body["text"]?.string, "**Hi** ![p](cid:tok)")
            return #"{"ok": true, "formatted": true, "html": "<p><b>Hi</b> <img src=\"cid:tok@mailmind\"></p>"}"#
        }
        let draft = composer()
        draft.setBody("**Hi** ![p](cid:tok)", selection: NSRange(location: 0, length: 0))
        draft.add([.data(name: "p.png", type: "image/png", Data([1]))])
        await eventually("the file read") { draft.reading.isEmpty }
        await draft.showPreview()
        XCTAssertTrue(draft.previewing)
        XCTAssertEqual(draft.preview?.formatted, true)
        draft.write()
        XCTAssertFalse(draft.previewing)
    }
}

/// What a composer needs of the app, as a test sets it, and what the composer told it.
@MainActor
final class FakeHost: ComposeHost {
    var routes: (any Routes)?
    var senders = ["ada@example.test"]
    var mine: Set<String> = SendGuard.mineSet(["ada@example.test"])
    var tone = API.DraftTone.balanced
    var attachLimit: Int?
    var aiOn = true
    var asksOverLimit: AskOverLimit?
    var snippets: [API.Snippet] = []
    var signatures: [String: String] = [:]
    var clockNow = 100.0
    private(set) var announced: [String] = []
    private(set) var closings: [(save: Task<Bool?, Never>, snapshot: ComposeSeed)] = []
    private(set) var sent: [(body: API.SendBody, replyKey: Int?, after: Int, waited: Bool)] = []
    private(set) var closedCount = 0
    private(set) var outboxTurns = 0

    init(routes: (any Routes)? = nil) {
        self.routes = routes
    }

    func signature(_ email: String) -> String { signatures[email] ?? "" }
    func loadSnippets() {}
    func changeSnippets(_ change: API.SnippetChange) async -> String? { nil }
    func known(_ address: String) -> Person? { nil }
    func remember(_ address: String, _ person: Person) {}
    func now() -> Int { 1_000_000_000 }
    func clock() -> Double { clockNow }
    func announce(_ text: String) { announced.append(text) }
    func draftsChanged() {}
    func foldersChanged() {}
    func outboxChanged() { outboxTurns += 1 }

    func closing(_ composer: Composer, save: Task<Bool?, Never>, snapshot: ComposeSeed) {
        closings.append((save, snapshot))
    }

    func send(_ composer: Composer, _ body: API.SendBody, snapshot: ComposeSeed, replyKey: Int?, after: Int,
              waitingFor save: Task<Bool?, Never>?)
    {
        sent.append((body, replyKey, after, save != nil))
    }

    func closed(_ composer: Composer) { closedCount += 1 }
}
