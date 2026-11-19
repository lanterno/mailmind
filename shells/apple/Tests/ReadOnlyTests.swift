import Foundation
import XCTest

/// Read only, as a person meets it: the status's note while it is on, and the same plain words wherever the server
/// turned down, up front, something that would have reached a mail server (a 409 with `read_only`), asked ahead of a
/// busy 409's words. A send it turned down keeps the message, and a draft kept on this Mac says so.
@MainActor
final class ReadOnlyTests: XCTestCase {
    /// A test's run has no switch for read only, so its words name none: the page's.
    private static let refused = "Read only is on, so nothing was changed."
    /// Send's: what didn't happen is the message going.
    private static let notSent = "Read only is on, so nothing was sent."
    nonisolated private static let detail = "MailMind is running read-only"

    /// The read-only 409 as both servers answer it.
    nonisolated private static var problem: Problem {
        Problem(FakeServer.refused(409, detail, more: ["read_only": true]))
    }

    func testTheWordsAreThePagesAndNameTheSwitchOnlyWhereThereIsOne() {
        let note = """
            Read only is on: MailMind changes nothing on your mail servers, so sending, filing and starring are turned \
            off.
            """
        XCTAssertEqual(ReadOnlyWords.note(switchable: true), note + " Turn it off in Settings › General.")
        XCTAssertEqual(ReadOnlyWords.note(switchable: false), note)
        XCTAssertEqual(ReadOnlyWords.refused(switchable: true), Self.refused + " Turn it off in Settings › General.")
        XCTAssertEqual(ReadOnlyWords.refused(switchable: false), Self.refused)
        XCTAssertEqual(ReadOnlyWords.notSent(switchable: true), Self.notSent + " Turn it off in Settings › General.")
        XCTAssertEqual(ReadOnlyWords.notSent(switchable: false), Self.notSent)
        XCTAssertEqual(ReadOnlyWords.notSent, Self.notSent)
        XCTAssertFalse(ReadOnlyRun.switchable, "a test's run has no switch in Settings")
        XCTAssertEqual(ReadOnlyWords.note, note, "so its words name none")
        XCTAssertEqual(ReadOnlyWords.refused, Self.refused)
        XCTAssertTrue(Self.problem.isReadOnly)
        XCTAssertFalse(Problem(FakeServer.refused(409, "busy", more: ["busy": true])).isReadOnly)
        XCTAssertEqual(ReadOnlyWords.said(Self.problem, else: "other"), Self.refused)
        XCTAssertEqual(ReadOnlyWords.said(URLError(.timedOut), else: "other"), "other")
    }

    // MARK: The mailbox

    private func opened(_ server: FakeServer) async -> MailApp {
        let app = MailApp()
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.place = .folder(account: Fixture.ada, folder: "INBOX")
        await until { app.list?.copy(at: 0) != nil }
        return app
    }

    private func refusing(_ paths: String..., rows: [API.FolderRow]? = nil) -> FakeServer {
        let server = rows.map { Fixture.server(rows: $0, grouped: false) } ?? Fixture.server(grouped: false)
        for path in paths {
            server.on("POST", path) { _ in FakeServer.refused(409, Self.detail, more: ["read_only": true]) }
        }
        return server
    }

    func testFilingAMessageSaysReadOnlyAndTheReaderStays() async throws {
        let app = await opened(refusing(API.Post.Archive.path))
        app.mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
        await app.mailbox.fileNow(.archive)
        XCTAssertEqual(app.mailbox.toast?.text, Self.refused)
        XCTAssertNil(app.mailbox.toast?.undo, "nothing was done, so nothing undoes")
        XCTAssertEqual(app.selection.copy?.key, 1, "the message didn't go, so the reader stays on it")
    }

    func testMovingSaysReadOnly() async throws {
        let app = await opened(refusing(API.Post.Move.path))
        app.mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
        await app.mailbox.moveNow(to: "Receipts", label: "Receipts")
        XCTAssertEqual(app.mailbox.toast?.text, Self.refused)
    }

    func testManyRowsAskOnceAndSayReadOnly() async {
        let rows = (1...1200).map { Fixture.row($0) }
        let server = refusing(API.Post.BulkFile.path, rows: rows)
        let app = await opened(server)
        await app.mailbox.fileNow(.trash, rows: rows)
        XCTAssertEqual(server.asked(API.Post.BulkFile.path).count, 1, "read only's no is the same for every chunk")
        XCTAssertEqual(app.mailbox.toast?.text, Self.refused)
    }

    func testStarringAndMarkingSayReadOnlyAndAskOnce() async throws {
        let server = refusing(API.Post.Flag.path, API.Post.Seen.path)
        let app = await opened(server)
        let first = try XCTUnwrap(app.list?.copy(at: 0)), second = try XCTUnwrap(app.list?.copy(at: 1))
        await app.mailbox.starNow(rows: [first])
        XCTAssertEqual(app.mailbox.toast?.text, Self.refused)
        await app.mailbox.setSeenNow(false, rows: [first])
        XCTAssertEqual(app.mailbox.toast?.text, Self.refused)
        await app.mailbox.starNow(rows: [first, second])
        XCTAssertEqual(server.asked(API.Post.Flag.path).count, 2, "one for the one row, then one for the two")
        XCTAssertEqual(app.mailbox.toast?.text, Self.refused)
    }

    func testAnUndoThatWouldReachAServerSaysReadOnly() async {
        let app = await opened(refusing(API.Post.Undo.path))
        await app.mailbox.undoNow()
        XCTAssertEqual(app.mailbox.toast?.text, Self.refused)
    }

    // MARK: Opening mail

    /// Opening mail marks it read only once the digest says MailMind runs with its writes on, as the page does: read
    /// only would turn each one down with its 409, and before the profile is known nothing is asked either. Both the
    /// reader's message and a conversation's members go this way.
    func testOpeningMailAsksNoServerToMarkItReadWhileReadOnlyOrBeforeTheProfileIsKnown() async throws {
        let member = API.ThreadMessage(answered: false, flagged: false, folder: "INBOX", hasAttach: false,
                                       isJunk: false, key: 22, seen: false, sentTo: [], sentToMore: 0,
                                       snippet: "", subject: "Plans", uid: 22,
                                       when: "")
        let copy = MailCopy(member, account: Fixture.ada)
        for (profile, marks) in [(nil, false), ("read-only", false), ("full", true)] as [(String?, Bool)] {
            XCTAssertEqual(MarksOpened.marks(profile), marks, "\(profile ?? "not known yet")")
            let routes = FakeRoutes()
            routes.answer(API.Post.Seen.path, #"{"msg": "ok", "ok": true, "undoable": true}"#)
            let conversation = ConversationModel(routes: routes, account: Fixture.ada, id: "t1", profile: { profile })
            await conversation.markRead(member)
            await MarksOpened.mark(copy, routes: routes, profile: profile)
            let asked = routes.calls(to: API.Post.Seen.path)
            XCTAssertEqual(asked.count, marks ? 2 : 0, "\(profile ?? "not known yet")")
            for call in asked {
                XCTAssertEqual(call.body, try JSONDecoder().decode(JSONValue.self, from: JSONEncoder().encode(
                    API.SeenBody(folder: "INBOX", key: 22, seen: true, uid: 22))))
            }
        }
    }

    func testAMemberAlreadyReadIsntMarkedAgain() async {
        let routes = FakeRoutes()
        let conversation = ConversationModel(routes: routes, account: Fixture.ada, id: "t1", profile: { "full" })
        await conversation.markRead(API.ThreadMessage(answered: false, flagged: false, folder: "INBOX", hasAttach: false,
                                                      isJunk: false, key: 21, seen: true, sentTo: [], sentToMore: 0,
                                                      snippet: "", subject: "Plans",
                                                      uid: 21, when: ""))
        XCTAssertTrue(routes.calls(to: API.Post.Seen.path).isEmpty)
    }

    // MARK: The outbox's tray

    private func tray(refusing paths: [String], with problem: Problem = ReadOnlyTests.problem) -> (OutboxTray, () -> [String]) {
        let routes = FakeRoutes()
        for path in paths {
            routes.answer(path) { _ in throw problem }
        }
        var said: [String] = []
        let tray = OutboxTray(routes: routes) { said.append($0) }
        return (tray, { said })
    }

    func testTheTraysRetryAndSendNowSayReadOnlyBeforeABusyNo() async {
        let (tray, said) = tray(refusing: [API.Post.PendingRetry.path, API.Post.OutboxSendnow.path,
                                           API.Post.OutboxRetry.path])
        await tray.act("retry", 3)
        await tray.sendNow(4)
        await tray.retry(5)
        XCTAssertEqual(said(), [Self.refused, Self.refused, Self.refused])

        let busy = Problem(FakeServer.refused(409, "it is being tried", more: ["busy": true]))
        let (other, saidOther) = self.tray(refusing: [API.Post.PendingRetry.path], with: busy)
        await other.act("retry", 3)
        XCTAssertEqual(saidOther(), ["It’s being tried now. Try again in a moment"], "a busy 409 keeps its words")
    }

    func testADraftKeptOnThisMacSaysSoAndOnlyEarlierRowsWaitForAFullRun() {
        func draft(kept: Bool) -> API.WaitingDraft {
            API.WaitingDraft(account: Fixture.ada, attempts: 0, discarding: false, id: 1, kept: kept, savedAt: 0,
                             subject: "Plans")
        }
        XCTAssertEqual(OutboxTray.why(draft(kept: true), readOnly: true), "Kept on this Mac")
        XCTAssertEqual(OutboxTray.why(draft(kept: true), readOnly: false), "Kept on this Mac",
                       "until it is saved again with read only off")
        XCTAssertEqual(OutboxTray.why(draft(kept: false), readOnly: true), "reaches the server on a full run",
                       "a draft a full run queued earlier waits for the next")
        XCTAssertEqual(OutboxTray.why(draft(kept: false), readOnly: false), "reaches the server once it’s back")
    }

    /// A change given back says why in the page's words: a folder another app deleted is not the server refusing it.
    func testAChangeGivenBackSaysWhyAsThePageDoes() {
        func change(_ error: String) -> API.PendingChange {
            API.PendingChange(account: Fixture.ada, attempts: 1, createdAt: "2026-11-19T09:00:00+00:00",
                              dest: "Plans", error: error, folder: "INBOX", id: 1, nextAttemptAt: 0, op: "move",
                              state: "error")
        }
        XCTAssertEqual(OutboxTray.why(change("folder_gone"), readOnly: false),
                       "its folder was deleted or renamed in another app")
        XCTAssertEqual(OutboxTray.why(change("refused"), readOnly: false), "the server refused it")
        XCTAssertEqual(OutboxTray.why(change("something else"), readOnly: false), "couldn’t reach the server")
    }

    // MARK: Sending

    private func readOnlySend(_ composing: Composing, digest: API.Digest) -> (FakeRoutes, () -> [UUID]) {
        let routes = FakeRoutes()
        routes.answer(API.Post.Send.path) { _ in throw ReadOnlyTests.problem }
        composing.use(routes, digest: digest)
        var windows: [UUID] = []
        composing.openWindow = { windows.append($0) }
        return (routes, { windows })
    }

    private var reply: ComposeSeed {
        var seed = ComposeSeed()
        seed.mode = .reply
        seed.to = ["bo@example.test"]
        seed.subject = "Re: Plans"
        seed.origKey = 7
        seed.body = "Yes, Thursday."
        return seed
    }

    /// Send, read only: the composer stays open as it is, saying why in itself, and nothing is asked to go.
    func testASendReadOnlyKeepsTheComposerOpenWithTheWordsInIt() async throws {
        let composing = Composing()
        let (routes, windows) = readOnlySend(composing, digest: Fixture.digest(readOnly: true))
        let composer = try XCTUnwrap(composing.open(reply))
        composer.send()
        composer.send()
        XCTAssertFalse(composer.closed, "it stays open")
        XCTAssertEqual(windows().count, 1, "and no window opens again")
        XCTAssertEqual(composer.subject, "Re: Plans")
        XCTAssertEqual(composer.body, "Yes, Thursday.")
        XCTAssertEqual(composer.to.map(\.address), ["bo@example.test"])
        XCTAssertEqual(composer.notice, Self.notSent, "said in the composer: it wasn't sent")
        XCTAssertNil(composing.notice, "not in the main window")
        XCTAssertNil(composing.float, "no countdown and no Sent")
        XCTAssertTrue(routes.calls(to: API.Post.Send.path).isEmpty, "nothing was asked to go")
        composer.close(force: true)
    }

    /// The server's no to a send, from a run the composer didn't know was read only yet: the message comes back as it
    /// was, saying why in its composer, and the next Send is turned down where it is.
    func testASendTheServerTurnsDownReadOnlyComesBackSayingSoInItsComposer() async throws {
        let composing = Composing()
        let (routes, windows) = readOnlySend(composing, digest: Fixture.digest())
        let now = try XCTUnwrap(String(data: JSONEncoder().encode(Fixture.digest(readOnly: true)), encoding: .utf8))
        routes.answer(API.Get.Digest.path, now)
        let composer = try XCTUnwrap(composing.open(reply))
        composer.send()
        XCTAssertTrue(composer.closed)
        await eventually("the message to come back") { windows().count == 2 }
        let back = try XCTUnwrap(composing.composers[windows()[1]])
        XCTAssertEqual(back.subject, "Re: Plans")
        XCTAssertEqual(back.body, "Yes, Thursday.")
        XCTAssertEqual(back.to.map(\.address), ["bo@example.test"])
        XCTAssertEqual(back.notice, Self.notSent, "said in its composer: it wasn't sent")
        XCTAssertNil(composing.notice, "not in the main window")
        XCTAssertNil(composing.float, "no countdown and no Sent: nothing was queued")
        await eventually("the run read again") { composing.readOnly }
        back.send()
        XCTAssertFalse(back.closed, "the next Send is turned down where it is")
        XCTAssertEqual(routes.calls(to: API.Post.Send.path).count, 1)
        back.close(force: true)
    }

    /// A draft closed while read only is on was kept on this Mac, where no Drafts folder shows it: closing says so in
    /// the tray's words, and the outbox's tray is read again, so the draft shows there at once, as after a relaunch.
    func testADraftClosedReadOnlySaysItIsKeptOnThisMacAndShowsInTheOutbox() async throws {
        XCTAssertEqual(Composing.keptWords, "Draft saved · Kept on this Mac")
        // Closed with words not saved yet, which its close saves; and closed once saved, with nothing new in it.
        for savedFirst in [false, true] {
            let composing = Composing()
            let routes = FakeRoutes()
            routes.answer(API.Post.Draft.path,
                          #"{"ok": true, "draft_id": 41, "version": 1, "message_id": "<d@example.test>", "pushed": false}"#)
            composing.use(routes, digest: Fixture.digest(readOnly: true))
            composing.openWindow = { _ in }
            // The tray's reads, not the scheduled sends' (`readScheduled`, 200 at a time).
            let trayReads = { routes.calls(to: API.Get.Outbox.path).filter { $0.query["limit"] == "50" }.count }
            await eventually("the tray read as it opened") { trayReads() == 1 }

            var seed = ComposeSeed()
            seed.to = ["bo@example.test"]
            seed.subject = "Plans"
            let composer = try XCTUnwrap(composing.open(seed))
            composer.setBody("Thursday works.", selection: NSRange(location: 15, length: 0))
            if savedFirst {
                _ = await composer.save(silent: true, autosave: true).value
                XCTAssertFalse(composer.dirty)
            }
            let before = trayReads()
            composer.close()

            await eventually("where it is kept said") { composing.notice?.text == Composing.keptWords }
            await eventually("the tray read again, which shows it") { trayReads() > before }
            XCTAssertNotEqual(composing.notice?.text, "Saved to Drafts", "no Drafts folder holds it")
        }
    }

    func testADiscardReadOnlyTurnsDownKeepsTheComposerOpenWithItsWords() async {
        let routes = FakeRoutes()
        routes.answer(API.Post.Draft.path) { body in
            if body["delete"] == .bool(true) { throw ReadOnlyTests.problem }   // its copy may be on the server
            return #"{"ok": true, "draft_id": 41, "version": 2, "message_id": "<d@example.test>", "pushed": false}"#
        }
        let host = FakeHost(routes: routes)
        var seed = ComposeSeed()
        seed.to = ["bo@example.test"]
        seed.subject = "Plans"
        seed.draftId = 41   // opened from Drafts
        seed.version = 1
        let composer = Composer(seed, host: host)
        composer.setBody("Thursday works.", selection: NSRange(location: 15, length: 0))

        await composer.discard()

        XCTAssertEqual(routes.calls(to: API.Post.Draft.path).last?.body["delete"], .bool(true))
        XCTAssertFalse(composer.closed, "the draft is still there, so its composer stays")
        XCTAssertEqual(host.closedCount, 0)
        XCTAssertTrue(composer.dirty, "its words are still to save")
        XCTAssertEqual(composer.body, "Thursday works.")
        XCTAssertEqual(composer.notice, Self.refused)
        XCTAssertEqual(host.announced.last, Self.refused)
        composer.close(force: true)
    }

    // MARK: Attention

    func testMutingSeveralSaysReadOnlyAndAsksOnce() async {
        let routes = DeskRoutes(), notices = DeskNotices()
        routes.refuse("POST", API.Post.Mute.path, status: 409, type: "read-only", detail: Self.detail,
                      more: #""read_only": true"#)
        let model = AttentionModel(link: CoreLink(routes), notices: notices)

        await model.mute([DeskFixture.ada, DeskFixture.grace])

        XCTAssertEqual(notices.shown?.text, Self.refused, "not \"Muted 0 threads\"")
        XCTAssertEqual(notices.shown?.undoable, false)
        XCTAssertEqual(routes.sent("POST", API.Post.Mute.path).count, 1, "read only's no is the same for every thread")
    }

    // MARK: The desk, curation and folders

    func testTheDeskCurationAndFoldersSayReadOnly() {
        XCTAssertEqual(DeskRefusal.words(Self.problem, else: "Couldn’t change that."), Self.refused)
        XCTAssertEqual(CurateModel.sweepFailed(Self.problem, planning: false), Self.refused)
        XCTAssertEqual(CurateModel.sweepFailed(Self.problem, planning: true), Self.refused)
        XCTAssertEqual(FolderWords.refusal(Self.problem), .refused(Self.refused))
    }
}
