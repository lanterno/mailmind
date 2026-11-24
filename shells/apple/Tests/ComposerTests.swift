import AppKit
import Foundation
import SwiftUI
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

    /// Closed with only a recipient picked, or only a subject, or only words, a message is saved to Drafts, as the
    /// page keeps it (`nothingToSave` counts a recipient) and both servers do; only one with nothing at all is let go.
    func testClosingKeepsAMessageWithOnlyARecipientASubjectOrWords() async throws {
        let composing = Composing()
        composing.use(routes, digest: Fixture.digest())
        composing.openWindow = { _ in }
        let fills: [(String, (Composer) -> Void)] = [
            ("a recipient", { $0.commitChip(.to, "bo@example.test") }),
            ("a subject", { $0.setSubject("Plans") }),
            ("words", { $0.setBody("Thursday works.", selection: NSRange(location: 15, length: 0)) }),
        ]
        for (what, fill) in fills {
            let before = routes.calls(to: "/api/draft").count
            let composer = try XCTUnwrap(composing.open(ComposeSeed()))
            fill(composer)
            composing.say("between")
            composer.close()
            await eventually("closed with \(what), it is kept") { composing.notice?.text == "Saved to Drafts" }
            XCTAssertEqual(routes.calls(to: "/api/draft").count, before + 1, "saved once, with \(what)")
        }

        let before = routes.calls(to: "/api/draft").count
        composing.say("between")
        try XCTUnwrap(composing.open(ComposeSeed())).close()
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(routes.calls(to: "/api/draft").count, before, "nothing in it: nothing saved")
        XCTAssertEqual(composing.notice?.text, "between", "and nothing said")
    }

    /// A draft only an autosave kept waits on this Mac for the push job, a minute or more: closing it sends it to its
    /// server then, as a save someone asks for does, so it is in Drafts within seconds. One the server has already is
    /// left as it is.
    func testADraftOnlyAnAutosaveKeptGoesToItsServerAsItCloses() async throws {
        routes.answer("/api/draft") { body in
            let pushed = body["autosave"] != .bool(true)   // an autosave is kept on this Mac only
            return #"{"ok": true, "draft_id": 41, "version": 1, "message_id": "<d@example.test>", "pushed": \#(pushed)}"#
        }
        let draft = written("Kept by an autosave")
        draft.autosaveTick()
        await draft.settled()
        XCTAssertEqual(routes.calls(to: "/api/draft").map { $0.body["autosave"] }, [.bool(true)])
        XCTAssertTrue(draft.isSaved)
        XCTAssertTrue(draft.waitsForServer)

        draft.close()
        let closing = try XCTUnwrap(host.closings.first)
        let saved = await closing.save.value
        XCTAssertEqual(saved, true)
        let calls = routes.calls(to: "/api/draft")
        XCTAssertEqual(calls.count, 2, "closing sends it to its server")
        XCTAssertNotEqual(calls.last?.body["autosave"], .bool(true), "as a save someone asks for")
        XCTAssertEqual(calls.last?.body["draft_id"]?.number, 41)
        XCTAssertFalse(draft.waitsForServer)

        // Saved by hand, it reached its server: closing has nothing more to send.
        let other = written("Saved by hand")
        _ = await other.save().value
        XCTAssertFalse(other.waitsForServer)
        other.close()
        _ = await host.closings.last?.save.value
        XCTAssertEqual(routes.calls(to: "/api/draft").count, 3, "one save, and none as it closed")
    }

    /// A draft closed while its server's gate was too busy reaches it later, by the push job, which says so only on
    /// the outbox's event: each such event reads the lists again until it may have, so Drafts shows it once it's there.
    func testADraftClosedBeforeItReachedItsServerReadsTheListsAgainAsTheOutboxChanges() async throws {
        routes.answer("/api/draft",
                      #"{"ok": true, "draft_id": 52, "version": 1, "message_id": "<d@example.test>", "pushed": false}"#)
        routes.answer(API.Get.Outbox.path, #"{"ok": true, "items": [], "drafts": []}"#)
        let composing = Composing()
        composing.use(routes, digest: Fixture.digest())
        composing.openWindow = { _ in }
        var reads = 0
        composing.mailChanged = { reads += 1 }
        composing.heardTheOutbox()
        XCTAssertEqual(reads, 0, "nothing on its way: the outbox's event reads nothing")

        let composer = try XCTUnwrap(composing.open(ComposeSeed()))
        composer.setBody("On its way", selection: NSRange(location: 10, length: 0))
        composer.close()
        await eventually("kept") { composing.notice?.text == "Saved to Drafts" }
        XCTAssertEqual(reads, 1, "read again as it closed")
        XCTAssertNotNil(composing.reaching[52])

        composing.heardTheOutbox()
        XCTAssertEqual(reads, 2, "the push job may have sent it")
        composing.heardTheOutbox(now: Date().addingTimeInterval(Composing.reachingFor + 1))
        XCTAssertEqual(reads, 2, "long past the job's longest wait, nothing is read for it")
        XCTAssertTrue(composing.reaching.isEmpty)
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

    /// A draft saved with "bob@example" in To and opened again (Drafts' Edit Draft, a draft reopened from the server)
    /// shows the chip as typed and red, and the send guard holds Send with the same words as when it was typed; a
    /// good address beside it stays good. A reply's own recipients, which the original named, are taken as they are.
    func testAReopenedDraftsBadChipIsMarkedAndHoldsTheSend() throws {
        let opened = try JSONDecoder().decode(API.DraftOpened.self, from: Data("""
            {"ok": true, "account": "ada@example.test", "draft_id": 41, "version": 3, "mode": "new",
             "to": ["bob@example", "Bo Lane <bo@example.test>"], "cc": [], "bcc": ["lin@"], "subject": "Plans",
             "text": "Hello", "tail_text": "", "in_reply_to": "", "references": "", "reply_to": "", "original_from": "",
             "participants": [], "files": [], "split": "", "notes": {"bcc_dropped": 0, "inline": 0, "plain": false}}
            """.utf8))
        let draft = composer(Seeds.resumed(opened))
        XCTAssertEqual(draft.to.map(\.value), ["bob@example", "Bo Lane <bo@example.test>"], "as typed")
        XCTAssertEqual(draft.to.map(\.bad), [true, false], "the bad one red, the good one not")
        XCTAssertEqual(draft.bcc.map(\.bad), [true])
        draft.send()
        let said = try XCTUnwrap(draft.check?.lines.first?.text)
        XCTAssertTrue(said.hasPrefix("“bob@example” isn’t a valid address."), "as when it was typed: \(said)")
        XCTAssertTrue(host.sent.isEmpty, "Send waits")

        var reply = ComposeSeed()
        reply.mode = .reply
        reply.to = ["undisclosed-recipients:;"]
        XCTAssertEqual(composer(reply).to.map(\.bad), [false], "a reply's recipient as the original named it")
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

    func testAHeldSendKeyNeverWavesACheckThrough() {
        let draft = written("The plan is attached.", subject: "")
        draft.pressSend()
        XCTAssertNotNil(draft.check, "the first press shows what holds the send")
        host.clockNow += SendGuard.armSeconds * 4
        for _ in 0..<30 { draft.pressSend(held: true) }   // the key's auto-repeat, long past the wait
        XCTAssertFalse(draft.closed, "a held key never sends")
        XCTAssertTrue(host.sent.isEmpty)
        XCTAssertNotNil(draft.check, "the check still stands")
        draft.pressSend()
        XCTAssertTrue(draft.closed, "a fresh press sends anyway")
        XCTAssertEqual(host.sent.count, 1)
    }

    func testAHeldSendKeyStartsNoSendOfItsOwn() {
        let draft = written("Lunch on Thursday?")
        draft.pressSend(held: true)
        XCTAssertFalse(draft.closed)
        XCTAssertNil(draft.check)
        draft.pressSend()
        XCTAssertTrue(draft.closed)
        XCTAssertEqual(host.sent.count, 1)
    }

    func testTheTextsCommandReturnSaysWhenTheKeyIsHeld() {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 300, height: 200), styleMask: [.titled],
                              backing: .buffered, defer: true)
        window.isReleasedWhenClosed = false
        let text = BodyTextView(frame: NSRect(x: 0, y: 0, width: 300, height: 200))
        window.contentView = text
        XCTAssertTrue(window.makeFirstResponder(text))
        var presses: [Bool] = []
        text.commandReturn = { presses.append($0) }
        let key = { (repeated: Bool) in
            NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: .command, timestamp: 0,
                             windowNumber: window.windowNumber, context: nil, characters: "\r",
                             charactersIgnoringModifiers: "\r", isARepeat: repeated, keyCode: 36)!
        }
        XCTAssertTrue(text.performKeyEquivalent(with: key(false)))
        XCTAssertTrue(text.performKeyEquivalent(with: key(true)))
        XCTAssertTrue(text.performKeyEquivalent(with: key(true)))
        XCTAssertEqual(presses, [false, true, true], "the press, then the key's repeats, each said for what it is")
    }

    func testOnlyAPressThatRepeatsOneCountsAsHeld() {
        let click = { (count: Int) in
            NSEvent.mouseEvent(with: .leftMouseUp, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: 0,
                               context: nil, eventNumber: 0, clickCount: count, pressure: 0)!
        }
        XCTAssertFalse(click(1).repeatsAPress)
        XCTAssertTrue(click(2).repeatsAPress, "a double click's second")
        let other = NSEvent.otherEvent(with: .applicationDefined, location: .zero, modifierFlags: [], timestamp: 0,
                                       windowNumber: 0, context: nil, subtype: 0, data1: 0, data2: 0)!
        XCTAssertFalse(other.repeatsAPress, "asked of an event that is neither a key's nor a click's, without raising")
        XCTAssertFalse(NSEvent.repeatingAPress, "no event being handled")
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

    /// Send later's popover opens with the keyboard on its first preset, as the page's does, never in the date and
    /// time of one's own: Return there schedules the message in an hour, and ↓ moves to the next.
    func testSendLaterOpensWithTheKeyboardOnItsFirstPreset() async throws {
        _ = NSApplication.shared
        let draft = written("Later")
        let window = NSWindow(contentRect: NSRect(x: -20_000, y: -20_000, width: 400, height: 300), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.alphaValue = 0
        let anchor = NSView(frame: NSRect(x: 20, y: 20, width: 40, height: 20))
        window.contentView = NSView(frame: NSRect(x: 0, y: 0, width: 400, height: 300))
        window.contentView?.addSubview(anchor)
        window.makeKeyAndOrderFront(nil)
        defer {
            window.orderOut(nil)
            window.contentView = nil
        }
        let popover = NSPopover()
        popover.contentViewController = NSHostingController(rootView: LaterChoices(composer: draft).meadowLight(.day))
        popover.show(relativeTo: anchor.bounds, of: anchor, preferredEdge: .maxY)
        defer { popover.close() }
        let shown = try XCTUnwrap(popover.contentViewController?.view.window)
        await until { shown.isKeyWindow || shown.firstResponder !== shown }
        try await Task.sleep(for: .milliseconds(150))
        XCTAssertFalse(shown.firstResponder is NSDatePicker, "not the date and time: \(String(describing: shown.firstResponder))")
        XCTAssertFalse(shown.firstResponder is NSTextView, "nor a field's editor")
        func press(_ code: UInt16, _ key: String) throws {
            shown.sendEvent(try XCTUnwrap(NSEvent.keyEvent(
                with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: shown.windowNumber,
                context: nil, characters: key, charactersIgnoringModifiers: key, isARepeat: false, keyCode: code)))
        }
        let hour = Int(Date().timeIntervalSince1970) + 3600
        try press(36, "\r")
        await until { !host.sent.isEmpty }
        XCTAssertEqual(Double(try XCTUnwrap(host.sent.first?.after)), Double(hour), accuracy: 5, "in 1 hour")
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

    // MARK: What VoiceOver hears

    /// The To field says its name, on the field and on the cell AppKit lets speak for a text field. The Subject field
    /// is SwiftUI's, named by its modifier, which a test can't read back: SwiftUI builds no accessibility tree in a
    /// test's window.
    func testTheToFieldSaysItsName() async throws {
        let host = FakeHost()
        let composer = Composer(ComposeSeed(), host: host)
        defer { composer.close(force: true) }
        let view = NSHostingView(rootView: ComposerForm(composer: composer, host: host, aliases: [:], lights: false) { _ in }
            .frame(width: 720, height: 560)
            .meadowLight(.day))
        view.frame = CGRect(x: 0, y: 0, width: 720, height: 560)
        let window = NSWindow(contentRect: view.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = view
        defer { window.contentView = nil }
        var found: RecipientField?
        for _ in 0..<50 where found == nil {
            view.layoutSubtreeIfNeeded()
            found = Self.views(RecipientField.self, in: view).first
            try await Task.sleep(for: .milliseconds(10))
        }
        let to = try XCTUnwrap(found, "the To field")
        XCTAssertEqual(to.accessibilityLabel(), "To")
        XCTAssertEqual(to.cell?.accessibilityLabel(), "To")
    }

    /// An address typed in To becomes a chip as the field is left by a click elsewhere (on Subject, say), as Tab makes
    /// it one, rather than stay behind as text the next field's first click seems to have lost.
    func testLeavingToByAClickMakesWhatIsTypedAChip() async throws {
        let host = FakeHost()
        let composer = Composer(ComposeSeed(), host: host)
        defer { composer.close(force: true) }
        let view = NSHostingView(rootView: ComposerForm(composer: composer, host: host, aliases: [:], lights: false) { _ in }
            .frame(width: 720, height: 560)
            .meadowLight(.day))
        view.frame = CGRect(x: 0, y: 0, width: 720, height: 560)
        let window = NSWindow(contentRect: view.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = view
        defer { window.contentView = nil }
        var found: RecipientField?
        for _ in 0..<50 where found == nil {
            view.layoutSubtreeIfNeeded()
            found = Self.views(RecipientField.self, in: view).first
            try await Task.sleep(for: .milliseconds(10))
        }
        let to = try XCTUnwrap(found, "the To field")
        XCTAssertTrue(window.makeFirstResponder(to))
        let editor = try XCTUnwrap(to.currentEditor() as? NSTextView, "To is being typed in")
        editor.insertText("ren@example.test", replacementRange: editor.selectedRange())
        await eventually("typed") { composer.typed[.to] == "ren@example.test" }
        XCTAssertTrue(composer.recipients(.to).isEmpty, "nothing made a chip of it yet")

        // A click elsewhere: the field's editing ends, as a click on Subject ends it.
        XCTAssertTrue(window.makeFirstResponder(nil))
        await eventually("a chip") { composer.recipients(.to).map(\.value) == ["ren@example.test"] }
        XCTAssertEqual(composer.typed[.to] ?? "", "", "and the field is empty for the next")
    }

    private static func views<V: NSView>(_ kind: V.Type, in view: NSView) -> [V] {
        ((view as? V).map { [$0] } ?? []) + view.subviews.flatMap { views(kind, in: $0) }
    }

    /// A chip's remove button names the address it takes away, and takes a press round its cross, not only on it.
    func testAChipsRemoveButtonNamesItsAddressAndIsEasyToHit() {
        XCTAssertEqual(ChipField.removeLabel(Recipient("Bo Brown <Bo@example.test>"), fresh: false),
                       "Remove bo@example.test")
        XCTAssertEqual(ChipField.removeLabel(Recipient("dee@example.test"), fresh: true),
                       "Remove dee@example.test, new to you")
        XCTAssertEqual(ChipField.removeLabel(Recipient("dee@"), fresh: false), "Remove dee@, not a valid address")
        XCTAssertGreaterThanOrEqual(13 + 2 * ChipField.removeReach, 16, "about 16 points across at least")
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
