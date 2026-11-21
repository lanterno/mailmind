import Foundation
import XCTest

/// What the assistant's, curation's, attention's and the calendar's models send and say, against routes scripted as
/// the server answers them: each sends the body the page sends, words what came back as the page does, and offers
/// Undo only where the server can take it back.
@MainActor
final class DeskModelTests: XCTestCase {
    private var routes: DeskRoutes!
    private var link: CoreLink!
    private var notices: DeskNotices!

    override func setUp() async throws {
        routes = DeskRoutes()
        link = CoreLink(routes)
        notices = DeskNotices()
    }

    private var said: String? { notices.shown?.text }
    private var undoable: Bool { notices.shown?.undoable ?? false }

    private func aiOff(_ method: String, _ path: String) {
        routes.refuse(method, path, status: 409, type: "ai-off", detail: "AI is off: there is no API key",
                      more: #""ai_off": true"#)
    }

    private func overLimit(_ method: String, _ path: String) {
        routes.refuse(method, path, status: 402, type: "over-limit", detail: "Today's AI limit is reached.",
                      more: #""over_limit": true"#)
    }

    // MARK: Attention

    func testSnoozingOneMessageNamesItAndOffersUndo() async {
        routes.answer("POST", "/api/snooze", API.Snoozed(count: 1, ok: true, until: 1_763_200_000))
        let model = AttentionModel(link: link, notices: notices)
        var changed = 0
        model.changed = { changed += 1 }

        await model.snooze([DeskFixture.ada], .preset(.tomorrow))

        let sent = routes.sent("POST", "/api/snooze")
        XCTAssertEqual(sent.count, 1)
        XCTAssertEqual(sent[0]["preset"], .string("tomorrow"))
        XCTAssertEqual(sent[0]["key"], .number(101))
        XCTAssertEqual(sent[0]["folder"], .string("INBOX"))
        XCTAssertNil(sent[0]["copies"])
        XCTAssertNil(sent[0]["undo_group"])
        XCTAssertEqual(said, "Snoozed until \(SendTimes.words(1_763_200_000))", "until when, as the server set it")
        XCTAssertTrue(undoable)
        XCTAssertEqual(changed, 1)
    }

    func testASnoozeAwayFromTheMessageOnScreenSaysWhichItWas() async {
        routes.answer("POST", "/api/snooze", API.Snoozed(count: 1, ok: true, until: 1_763_200_000))
        let model = AttentionModel(link: link, notices: notices)
        model.naming = { copies in copies == [DeskFixture.ada] ? "Fabric samples" : nil }
        await model.snooze([DeskFixture.ada], .preset(.tomorrow))
        XCTAssertEqual(said, "Snoozed “Fabric samples” until \(SendTimes.words(1_763_200_000))")
    }

    func testSnoozeWordsSayUntilWhenAndWhich() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try XCTUnwrap(TimeZone(identifier: "Europe/Berlin"))
        let now = try XCTUnwrap(calendar.date(from: DateComponents(year: 2026, month: 11, day: 19, hour: 10)))
        let tomorrow = Int(try XCTUnwrap(calendar.date(byAdding: .hour, value: 23, to: now)).timeIntervalSince1970)
        let when = SendTimes.words(tomorrow, now: now, calendar: calendar)
        XCTAssertTrue(when.hasPrefix("tomorrow"))
        XCTAssertEqual(SnoozeWords.said(until: tomorrow, count: 1, subject: nil, now: now, calendar: calendar),
                       "Snoozed until \(when)")
        XCTAssertEqual(SnoozeWords.said(until: tomorrow, count: 3, subject: "Ignored", now: now, calendar: calendar),
                       "Snoozed 3 until \(when)")
        XCTAssertEqual(SnoozeWords.said(until: tomorrow, count: 1, subject: "Design review", now: now, calendar: calendar),
                       "Snoozed “Design review” until \(when)")
        let long = String(repeating: "Quilt ", count: 20)
        let said = SnoozeWords.said(until: tomorrow, count: 1, subject: long, now: now, calendar: calendar)
        XCTAssertTrue(said.contains("…” until"), "a long subject is cut short: \(said)")
        XCTAssertEqual(MailboxToast.icon(said), .clock, "and every one keeps the clock")
    }

    func testSnoozingSeveralIsOneRequestUnderOneUndoGroup() async {
        routes.answer("POST", "/api/snooze", API.Snoozed(count: 2, ok: true, until: 1_763_200_000))
        let model = AttentionModel(link: link, notices: notices)
        let until = Date(timeIntervalSince1970: 1_763_200_000.4)

        await model.snooze([DeskFixture.ada, DeskFixture.grace], .until(until))

        let sent = routes.sent("POST", "/api/snooze")
        XCTAssertEqual(sent.count, 1)
        XCTAssertEqual(sent[0]["copies"], .array([
            .object(["folder": .string("INBOX"), "key": .number(101), "uid": .number(11)]),
            .object(["folder": .string("INBOX"), "key": .number(102), "uid": .number(12)]),
        ]))
        // A time of a person's own goes as whole seconds, never before the moment chosen.
        XCTAssertEqual(sent[0]["until"], .number(1_763_200_001))
        guard case .string(let group)? = sent[0]["undo_group"] else { return XCTFail("no undo group") }
        XCTAssertTrue(group.hasPrefix("ug-"))
        XCTAssertNil(sent[0]["key"])
    }

    func testARefusedSnoozeSaysWhyAndOffersNoUndo() async {
        routes.refuse("POST", "/api/snooze", status: 409, detail: "MailMind is read-only.")
        let model = AttentionModel(link: link, notices: notices)

        await model.snooze([DeskFixture.ada], .preset(.weekend))

        XCTAssertEqual(said, "Couldn’t snooze: MailMind is read-only.")
        XCTAssertFalse(undoable)
    }

    func testMutingSeveralSendsEachUnderOneGroupAndCountsThem() async {
        routes.answer("POST", "/api/mute", API.OnThread(ok: true, threadId: "t-1"))
        let model = AttentionModel(link: link, notices: notices)

        await model.mute([DeskFixture.ada, DeskFixture.grace])

        let sent = routes.sent("POST", "/api/mute")
        XCTAssertEqual(sent.count, 2)
        XCTAssertNotNil(sent[0]["undo_group"])
        XCTAssertEqual(sent[0]["undo_group"], sent[1]["undo_group"])
        XCTAssertEqual(said, "Muted 2 threads")
        XCTAssertTrue(undoable)
    }

    func testReplyLaterPinsAndDoneUnpins() async {
        routes.answer("POST", "/api/replylater", API.RepliedLater(ok: true, on: true, threadId: "t-1"))
        let model = AttentionModel(link: link, notices: notices)

        await model.replyLater([DeskFixture.ada])
        XCTAssertEqual(said, "Saved to Reply later")
        await model.replyLaterDone([DeskFixture.ada])
        XCTAssertEqual(said, "Done")

        XCTAssertEqual(routes.sent("POST", "/api/replylater").map { $0["on"] }, [.bool(true), .bool(false)])
    }

    func testNothingIsSentWithoutACoreOrACopy() async {
        let model = AttentionModel(link: CoreLink(), notices: notices)
        await model.snooze([DeskFixture.ada], .preset(.tomorrow))
        await AttentionModel(link: link, notices: notices).mute([])
        XCTAssertTrue(routes.sent.isEmpty)
        XCTAssertNil(said)
    }

    // MARK: Summaries and suggestions

    func testASummaryIsWrittenForTheMessageAskedAbout() async {
        routes.answer("POST", "/api/ai/summarize", DeskFixture.summary)
        let cards = SummaryCards(link: link)

        await cards.summarize(DeskFixture.ada, force: true)

        XCTAssertEqual(cards.card(for: DeskFixture.ada), .written(DeskFixture.summary))
        XCTAssertNil(cards.card(for: DeskFixture.grace))
        XCTAssertEqual(routes.sent("POST", "/api/ai/summarize").first?["force"], .bool(true))
    }

    func testASummaryAtTheDailyLimitSaysSo() async {
        overLimit("POST", "/api/ai/summarize")
        let cards = SummaryCards(link: link)

        await cards.summarize(DeskFixture.ada)

        XCTAssertEqual(cards.card(for: DeskFixture.ada), .failed("today’s AI limit is reached"))
        XCTAssertNil(routes.sent("POST", "/api/ai/summarize").first?["force"])
    }

    func testSuggestionsAreFoundAndAnEventGoesBackAsConfirmed() async throws {
        routes.answer("POST", "/api/ai/extract", DeskFixture.extracted)
        routes.answer("POST", "/api/calendar/event", API.EventFile(filename: "lunch.ics", ics: "BEGIN:VCALENDAR", ok: true))
        let cards = SuggestionCards(link: link)

        await cards.find(in: DeskFixture.ada)
        XCTAssertEqual(cards.card(for: DeskFixture.ada), .found(DeskFixture.extracted))

        _ = try await cards.event(from: DeskFixture.ada, title: "Lunch", start: "2026-11-20T12:30:00Z",
                                  end: "2026-11-20T13:30:00Z", place: "Café Lumen")
        let sent = routes.sent("POST", "/api/calendar/event").first
        XCTAssertEqual(sent?["title"], .string("Lunch"))
        XCTAssertEqual(sent?["place"], .string("Café Lumen"))
        XCTAssertEqual(sent?["key"], .number(101))
    }

    func testSuggestionsWithAIOffSayNothingCanReadIt() async {
        aiOff("POST", "/api/ai/extract")
        let cards = SuggestionCards(link: link)

        await cards.find(in: DeskFixture.ada)

        XCTAssertEqual(cards.card(for: DeskFixture.ada), .failed("AI is off, so nothing can read this for dates."))
    }

    // MARK: Ask and plans

    func testAnAnswerKeepsItsQuestionAndSources() async {
        routes.answer("POST", "/api/ai/ask", DeskFixture.answered)
        let model = AskModel(link: link)

        await model.ask("  When is the dentist appointment?  ")

        XCTAssertEqual(model.asking, .answered(question: "When is the dentist appointment?", DeskFixture.answered))
        XCTAssertEqual(routes.sent("POST", "/api/ai/ask").first?["account"], .string("all"))
    }

    func testAskingWithAIOffOrAtTheLimitSaysWhich() async {
        aiOff("POST", "/api/ai/ask")
        overLimit("POST", "/api/ai/ask")
        let model = AskModel(link: link)

        await model.ask("anything")
        XCTAssertEqual(model.asking, .failed(question: "anything", why: "AI is off: there is no API key"))
        await model.ask("anything")
        XCTAssertEqual(model.asking, .failed(question: "anything", why: "today’s AI limit is reached"))
        await model.ask("   ")
        XCTAssertEqual(routes.sent("POST", "/api/ai/ask").count, 2, "a blank question asks nothing")
    }

    func testADestructivePlanTakesTwoPressesAndConfirmsByItsId() async {
        routes.answer("POST", "/api/ai/do-plan", API.AiDoPlanAnswer.planPreview(DeskFixture.preview(.trash)))
        routes.answer("POST", "/api/ai/do-confirm", API.PlanCarriedOut(
            count: 40, failed: 2, gone: 0, ok: true, queued: 0, undoable: true, verb: .trash))
        let model = PlanModel(link: link, notices: notices)
        var changed = 0
        model.changed = { changed += 1 }

        await model.plan("delete the shop's newsletters older than a week")
        XCTAssertEqual(model.doing, .plan(command: "delete the shop's newsletters older than a week",
                                          DeskFixture.preview(.trash), armed: false))
        await model.press()
        XCTAssertEqual(model.doing, .plan(command: "delete the shop's newsletters older than a week",
                                          DeskFixture.preview(.trash), armed: true))
        XCTAssertTrue(routes.sent("POST", "/api/ai/do-confirm").isEmpty, "the first press only arms it")

        await model.press()
        let confirmed = routes.sent("POST", "/api/ai/do-confirm")
        XCTAssertEqual(confirmed.count, 1)
        XCTAssertEqual(confirmed[0]["plan"], .string("plan-7"))
        XCTAssertNotNil(confirmed[0]["undo_group"])
        XCTAssertNil(model.doing)
        XCTAssertEqual(said, "Trashed 40, 2 failed")
        XCTAssertTrue(undoable)
        XCTAssertEqual(changed, 1)
    }

    func testAnArchivePlanConfirmsOnTheFirstPress() async {
        routes.answer("POST", "/api/ai/do-plan", API.AiDoPlanAnswer.planPreview(DeskFixture.preview(.archive)))
        routes.answer("POST", "/api/ai/do-confirm", API.PlanCarriedOut(
            count: 42, failed: 0, gone: 1, ok: true, queued: 0, undoable: true, verb: .archive))
        let model = PlanModel(link: link, notices: notices)

        await model.plan("archive the shop's newsletters")
        await model.press()

        XCTAssertEqual(routes.sent("POST", "/api/ai/do-confirm").count, 1)
        XCTAssertEqual(said, "Archived 42, 1 had moved")
    }

    func testAPlanThatIsASearchActsOnNothing() async {
        routes.answer("POST", "/api/ai/do-plan", API.AiDoPlanAnswer.planIsASearch(
            API.PlanIsASearch(filter: "from:grace@example.test", intent: "search", ok: true)))
        routes.answer("POST", "/api/ai/do-plan", API.AiDoPlanAnswer.planIsASearch(
            API.PlanIsASearch(empty: true, filter: "  ", intent: "search", ok: true)))
        let model = PlanModel(link: link, notices: notices)

        await model.plan("mail from grace")
        XCTAssertEqual(model.doing, .search(command: "mail from grace", filter: "from:grace@example.test"))
        await model.press()
        await model.plan("do the thing")
        XCTAssertEqual(model.doing, .noFilter(command: "do the thing"))
        XCTAssertTrue(routes.sent("POST", "/api/ai/do-confirm").isEmpty)
    }

    func testAnExpiredPreviewSaysToRunItAgain() async {
        routes.answer("POST", "/api/ai/do-plan", API.AiDoPlanAnswer.planPreview(DeskFixture.preview(.read)))
        routes.refuse("POST", "/api/ai/do-confirm", status: 410, detail: "That plan has expired.")
        let model = PlanModel(link: link, notices: notices)

        await model.plan("mark the newsletters read")
        await model.press()

        XCTAssertEqual(said, "That preview has expired. Run the command again.")
        XCTAssertFalse(undoable)
    }

    // MARK: The brief

    private func scriptTheBrief(ai: String = "anthropic", off: API.AiOff = .noKey, briefing: API.BriefingAnswer) {
        routes.answer("GET", "/api/digest", DeskFixture.digest(ai: ai, off: off))
        routes.answer("GET", "/api/briefing", briefing)
        routes.answer("GET", "/api/upcoming", DeskFixture.upcoming)
        routes.answer("GET", "/api/waiting-on-others", DeskFixture.waiting)
        routes.answer("GET", "/api/tidy", API.TidyRun(filed: 0, group: nil, items: [], ok: true))
        routes.answer("GET", "/api/folders", API.Folders(account: "ada@example.test", folders: [], muted: 1, ok: true,
                                                         replylater: 2, snoozed: 3, waiting: 4))
    }

    private static let missing = API.BriefingAnswer.briefMissing(API.BriefMissing(
        cached: false, groups: [], missing: true, narrative: "", ok: true, stale: false))

    private func briefModel() -> BriefModel {
        let defaults = UserDefaults(suiteName: "mm-desk-tests-\(UUID().uuidString)")!
        return BriefModel(link: link, notices: notices, defaults: defaults)
    }

    func testOpeningTheBriefWritesTodaysWhenNoneIsStoredAndAIIsOn() async {
        scriptTheBrief(briefing: Self.missing)
        routes.answer("POST", "/api/briefing", DeskFixture.brief)
        let model = briefModel()

        await model.load()

        XCTAssertEqual(routes.sent("POST", "/api/briefing").count, 1)
        XCTAssertEqual(model.brief, DeskFixture.brief)
        XCTAssertEqual(model.needs.map(\.key), [101, 103, 104], "the most important first")
        XCTAssertEqual(model.tidy.map(\.key), [105, 106])
        XCTAssertEqual(model.topics.map(\.label), ["Work", "School"])
        XCTAssertEqual(model.upcoming.count, 3)
        XCTAssertEqual(model.waitingCount, 4)
        XCTAssertEqual(model.replyLaterCount, 2)
        XCTAssertEqual(model.aiOn, true)
    }

    /// The Waiting and Reply later tallies count the stacks they open: beside one account's Inbox that account's
    /// alone, beside All Inboxes every account's, as the page counts them where its tallies go.
    func testTheBriefCountsTheStacksItsTalliesOpen() async {
        let (ada, bea) = ("ada@example.test", "bea@example.test")
        var digest = DeskFixture.digest()
        digest.accounts = [ada, bea]
        routes.answer("GET", "/api/digest", digest)
        routes.answer("GET", "/api/briefing", API.BriefingAnswer.brief(DeskFixture.brief))
        routes.answer("GET", "/api/upcoming", DeskFixture.upcoming)
        routes.answer("GET", "/api/waiting-on-others", DeskFixture.waiting)
        routes.answer("GET", "/api/tidy", API.TidyRun(filed: 0, group: nil, items: [], ok: true))
        routes.answer("GET", "/api/folders", API.Folders(account: ada, folders: [], muted: 1, ok: true, replylater: 2,
                                                         snoozed: 3, waiting: 4))
        let model = briefModel()
        var whose: String? = ada
        model.stacksAccount = { whose }

        await model.load()
        XCTAssertEqual(routes.sent("GET", "/api/folders").map { $0.query["account"] }, [ada], "Ada's alone")
        XCTAssertEqual(model.waitingCount, 4)
        XCTAssertEqual(model.replyLaterCount, 2)

        // Opened again beside All Inboxes: Ada's count isn't shown while every account's is read.
        model.close()
        whose = nil
        routes.hold("GET", "/api/folders")
        let opening = Task { await model.load() }
        await until { model.waitingCount == nil }
        XCTAssertNil(model.replyLaterCount)
        routes.release("GET", "/api/folders")
        await opening.value
        XCTAssertEqual(routes.sent("GET", "/api/folders").dropFirst().map { $0.query["account"] }, [ada, bea])
        XCTAssertEqual(model.waitingCount, 8, "Ada's and Bea's together")
        XCTAssertEqual(model.replyLaterCount, 4)
    }

    /// The brief is a place of no account: its tallies open the stacks of the account whose Inbox it stands beside,
    /// and every account's beside All Inboxes, as the page's `goToFolder` does; and they count those.
    func testTheBriefsTalliesOpenTheStacksOfTheInboxItStandsBeside() async {
        let app = MailApp()
        app.mailbox.wire = Fixture.server().wire
        await app.mailbox.refresh()
        let desk = Desk()
        desk.use(app, routes: routes)

        app.place = .folder(account: Fixture.ada, folder: "INBOX")
        app.place = .desk(.brief)
        XCTAssertEqual(app.listed, .folder(account: Fixture.ada, folder: "INBOX"), "the brief beside Ada's Inbox")
        XCTAssertEqual(desk.brief.stacksAccount(), Fixture.ada, "what the tallies count")
        desk.goToStack(.waiting)
        XCTAssertEqual(app.place, .virtual(.waiting, account: Fixture.ada))

        app.place = .allInboxes
        app.place = .desk(.brief)
        XCTAssertNil(desk.brief.stacksAccount())
        desk.goToStack(.replyLater)
        XCTAssertEqual(app.place, .virtual(.replyLater, account: nil), "every account's")
        withExtendedLifetime(desk) {}
    }

    func testWithAIOffTheBriefStandsOnTheDigestAndAsksNothing() async {
        scriptTheBrief(ai: "none", briefing: Self.missing)
        let model = briefModel()

        await model.load()

        XCTAssertTrue(routes.sent("POST", "/api/briefing").isEmpty)
        XCTAssertNil(model.brief)
        XCTAssertNil(model.trouble)
        XCTAssertEqual(model.aiOn, false)
        XCTAssertFalse(model.aiTurnedOff, "off for want of a key, the brief says how to turn it on")
        XCTAssertEqual(model.needs.count, 3)
    }

    /// The line over the greeting says how the mail stands, with AI off too: the date until the digest or the
    /// Inbox's unread is known, then what needs you and the unread.
    func testTheLineOverTheGreetingSaysHowTheMailStandsOnceItIsKnown() async {
        scriptTheBrief(ai: "none", briefing: Self.missing)
        let model = briefModel()
        let now = Date(timeIntervalSince1970: 1_794_739_600)
        XCTAssertEqual(model.line(unread: nil, now: now), BriefWords.dateLine(now), "nothing read yet")
        XCTAssertEqual(model.line(unread: 5, now: now), "5 unread", "the counts came before the digest")

        await model.load()

        XCTAssertEqual(model.line(unread: nil, now: now), "3 need you")
        XCTAssertEqual(model.line(unread: 5, now: now), "3 need you · 5 unread")
    }

    /// AI off for want of a key, the brief's note says what AI mode would send as Settings says it, Draft replies
    /// ahead included: it reads Settings' choices.
    func testWithAIOffTheBriefsNoteSaysWhatWouldGoAsSettingsDoes() async {
        scriptTheBrief(ai: "none", briefing: Self.missing)
        routes.answer("GET", "/api/preferences", API.PreferencesAnswer(
            ok: true, preferences: StatusFixtures.preferences(aiEnabled: true, provider: "openai", draftAhead: true)))
        let model = briefModel()

        await model.load()

        XCTAssertEqual(model.preferences?.draftAhead, true)
        let said = AIWords.sends(model.preferences, model.digest?.ai)
        XCTAssertTrue(said.contains("go to OpenAI to be summarized"), said)
        XCTAssertTrue(said.hasSuffix("to draft replies before you open them."), said)
    }

    /// Turned off in Settings (#49): the brief stands on the digest, writes nothing, and says nothing of AI.
    func testWithAITurnedOffTheBriefWritesNothing() async {
        scriptTheBrief(ai: "none", off: .turnedOff, briefing: Self.missing)
        let model = briefModel()

        await model.load()

        XCTAssertTrue(routes.sent("POST", "/api/briefing").isEmpty)
        XCTAssertEqual(model.aiOn, false)
        XCTAssertTrue(model.aiTurnedOff)
        XCTAssertNil(model.failing)
        XCTAssertEqual(model.needs.count, 3)
    }

    /// Turned off, what the AI rated as safe to file before then isn't offered or counted, and filing sends nothing.
    func testWithAITurnedOffNothingIsOfferedToFile() async {
        scriptTheBrief(ai: "none", off: .turnedOff, briefing: Self.missing)
        let model = briefModel()
        await model.load()

        XCTAssertEqual(BriefWords.safeToFile(model.items).map(\.key), [105, 106], "the digest still carries the ratings")
        XCTAssertTrue(model.tidy.isEmpty, "no card, no File to Archive and no \"can wait\"")
        await model.fileTidy()
        XCTAssertTrue(routes.sent("POST", "/api/bulk/file").isEmpty)
    }

    func testAStoredBriefIsShownWithoutWritingOne() async {
        scriptTheBrief(briefing: .brief(DeskFixture.brief))
        let model = briefModel()

        await model.load()
        await model.load()

        XCTAssertEqual(model.brief, DeskFixture.brief)
        XCTAssertTrue(routes.sent("POST", "/api/briefing").isEmpty)
        XCTAssertEqual(routes.sent("GET", "/api/briefing").count, 1, "a second open reads only the digest again")
    }

    func testArrivingAtTheBriefWritesNoneAndOpeningItWritesTodays() async {
        scriptTheBrief(briefing: Self.missing)
        routes.answer("POST", "/api/briefing", DeskFixture.brief)
        let model = briefModel()

        model.glance()
        await model.load()
        XCTAssertTrue(routes.sent("POST", "/api/briefing").isEmpty, "the arrow keys passing over it write nothing")
        XCTAssertTrue(model.missing, "it offers to write today's")

        await model.load()
        XCTAssertEqual(routes.sent("POST", "/api/briefing").count, 1, "opened on purpose, today's is written")
        XCTAssertFalse(model.missing)
        XCTAssertEqual(model.brief, DeskFixture.brief)
    }

    /// Turned on again with the brief open, the brief AI-off left unwritten is written, once.
    func testTurningAIOnWritesTheBriefAIOffLeftUnwritten() async {
        scriptTheBrief(ai: "none", off: .turnedOff, briefing: Self.missing)
        routes.answer("GET", "/api/digest", DeskFixture.digest())
        routes.answer("POST", "/api/briefing", DeskFixture.brief)
        let model = briefModel()
        await model.load()
        XCTAssertTrue(routes.sent("POST", "/api/briefing").isEmpty)

        await model.aiChanged().value

        XCTAssertEqual(model.aiOn, true)
        XCTAssertEqual(routes.sent("POST", "/api/briefing").count, 1)
        XCTAssertEqual(model.brief, DeskFixture.brief)
        XCTAssertFalse(model.missing)
        await model.aiChanged().value
        XCTAssertEqual(routes.sent("POST", "/api/briefing").count, 1, "written, it isn't asked for again")
    }

    func testTurningAIOnWithTheBriefClosedWritesNone() async {
        scriptTheBrief(ai: "none", off: .turnedOff, briefing: Self.missing)
        routes.answer("GET", "/api/digest", DeskFixture.digest())
        routes.answer("POST", "/api/briefing", DeskFixture.brief)
        let model = briefModel()
        await model.load()
        model.close()

        await model.aiChanged().value

        XCTAssertTrue(routes.sent("POST", "/api/briefing").isEmpty, "opening it writes it")
    }

    /// Only arrived at by the arrow keys, the brief AI-off left unwritten stays unwritten as AI is turned on: it offers
    /// to write it, and opening it on purpose writes it.
    func testTurningAIOnWithTheBriefOnlyArrivedAtWritesNone() async {
        scriptTheBrief(ai: "none", off: .turnedOff, briefing: Self.missing)
        routes.answer("GET", "/api/digest", DeskFixture.digest())
        routes.answer("POST", "/api/briefing", DeskFixture.brief)
        let model = briefModel()
        model.glance()
        await model.load()

        await model.aiChanged().value

        XCTAssertEqual(model.aiOn, true)
        XCTAssertTrue(routes.sent("POST", "/api/briefing").isEmpty, "the arrow keys passing over it write nothing")
        XCTAssertTrue(model.missing, "it offers to write today's")

        await model.load()
        XCTAssertEqual(routes.sent("POST", "/api/briefing").count, 1, "opened on purpose, today's is written")
        XCTAssertEqual(model.brief, DeskFixture.brief)
    }

    /// Another provider, model, URL, limit or key with AI on already isn't AI turned on: the brief its writing failed
    /// is asked for again only by Try Again.
    func testAnotherAISettingWithAIOnAlreadyWritesNone() async {
        scriptTheBrief(briefing: Self.missing)
        overLimit("POST", "/api/briefing")
        let model = briefModel()
        await model.load()
        XCTAssertEqual(routes.sent("POST", "/api/briefing").count, 1)
        XCTAssertEqual(model.trouble, .limit)

        await model.aiChanged().value

        XCTAssertEqual(model.aiOn, true)
        XCTAssertEqual(routes.sent("POST", "/api/briefing").count, 1, "a setting changed with AI on asks nothing")
        XCTAssertNil(model.brief)
        XCTAssertEqual(model.trouble, .limit)
    }

    func testTheRailsTalliesAreReadOnceTheCoreIsReachedWhenTheRailAskedFirst() async {
        scriptTheBrief(briefing: .brief(DeskFixture.brief))
        let desk = Desk()
        await desk.brief.keepTallied()?.value
        XCTAssertNil(desk.brief.digest, "the rail showed before the window's core was open")

        desk.connect(routes)
        await until { desk.brief.digest != nil }
        XCTAssertEqual(desk.brief.needs.count, 3, "the rail's count")
        XCTAssertEqual(routes.sent("GET", "/api/digest").count, 1)
    }

    /// Mail a verb sent away, from any window, leaves the brief and the rail's count at once, as the page's brief leaves
    /// out whatever is being filed: not once the server has carried it, which a server that doesn't answer puts off.
    func testMailSentAwayFromAnyWindowLeavesTheBriefAtOnce() async {
        scriptTheBrief(briefing: .brief(DeskFixture.brief))
        let desk = Desk()
        desk.connect(routes)
        await desk.brief.keepTallied()?.value
        await until { desk.brief.digest != nil }
        let first = desk.brief.needs[0]
        let reads = routes.sent("GET", "/api/digest").count
        XCTAssertEqual(desk.rail.needs(in: nil, grouped: false), 3)

        desk.wentAway([BriefModel.copy(of: first)])

        XCTAssertFalse(desk.brief.needs.contains { $0.key == first.key })
        XCTAssertEqual(desk.rail.needs(in: nil, grouped: false), 2)
        XCTAssertEqual(routes.sent("GET", "/api/digest").count, reads, "nothing was read for it")
        withExtendedLifetime(desk) {}
    }

    func testMailChangingReadsOnlyTheTalliesUntilTheBriefIsOpenAndABurstReadsOnceMore() async {
        scriptTheBrief(briefing: .brief(DeskFixture.brief))
        let model = briefModel()
        XCTAssertNil(model.reread(), "nothing is kept yet, so mail changing reads nothing")
        await model.keepTallied()?.value
        XCTAssertEqual(model.needs.count, 3, "the rail's count")

        // A burst of events while a read is under way: one more read, not one each.
        let burst = (0..<5).map { _ in model.reread() }
        for read in burst { await read?.value }
        XCTAssertEqual(routes.sent("GET", "/api/digest").count, 2)
        for path in ["/api/upcoming", "/api/waiting-on-others", "/api/tidy", "/api/folders", "/api/search"] {
            XCTAssertEqual(routes.sent("GET", path).count, 0, "\(path) waits for the brief to open")
        }

        await model.load()
        XCTAssertEqual(routes.sent("GET", "/api/upcoming").count, 1)
        await model.reread()?.value
        XCTAssertEqual(routes.sent("GET", "/api/upcoming").count, 2, "open, all it shows is read again")
        model.close()
        await model.reread()?.value
        XCTAssertEqual(routes.sent("GET", "/api/upcoming").count, 2, "closed, only the tallies")
        XCTAssertEqual(routes.sent("GET", "/api/digest").count, 5)
    }

    func testWritingAgainAtTheLimitSaysSo() async {
        scriptTheBrief(briefing: .brief(DeskFixture.brief))
        overLimit("POST", "/api/briefing/refresh")
        let model = briefModel()
        await model.load()

        await model.write(again: true)

        XCTAssertEqual(model.trouble, .limit)
        XCTAssertEqual(model.brief, DeskFixture.brief, "the brief already shown stays")
        XCTAssertFalse(model.writing)
    }

    func testTidyFilesWhatIsSafeAsOneUndoableAction() async {
        scriptTheBrief(briefing: .brief(DeskFixture.brief))
        routes.answer("POST", "/api/bulk/file", API.RowsFiled(
            count: 2, failed: 0, filed: [], msg: "", ok: true, partial: false, queued: 0, rowsFailed: 0, undoable: true))
        let model = briefModel()
        await model.load()

        await model.fileTidy()

        let sent = routes.sent("POST", "/api/bulk/file")
        XCTAssertEqual(sent.count, 1)
        XCTAssertEqual(sent[0]["verb"], .string("archive"))
        guard case .array(let rows)? = sent[0]["rows"] else { return XCTFail("no rows") }
        XCTAssertEqual(rows.count, 2)
        XCTAssertTrue(model.tidy.isEmpty, "filed rows leave before the lists are read again")
        XCTAssertEqual(said, "Archived 2")
        XCTAssertTrue(undoable)
    }

    func testACardFiledToTrashSaysWhereItWent() async {
        scriptTheBrief(briefing: .brief(DeskFixture.brief))
        routes.answer("POST", "/api/trash", API.Filed(msg: "", ok: true, status: nil, undoable: true))
        let model = briefModel()
        await model.load()

        await model.file(model.needs[0], .trash)

        XCTAssertEqual(routes.sent("POST", "/api/trash").first?["key"], .number(101))
        XCTAssertEqual(said, "Deleted · in the mailbox's Trash")
        XCTAssertFalse(model.needs.contains { $0.key == 101 })
    }

    func testADismissedTidyRunStaysDismissed() async {
        scriptTheBrief(briefing: .brief(DeskFixture.brief))
        routes.answer("GET", "/api/tidy", API.TidyRun(filed: 3, group: "tidy-1", items: [], ok: true))
        let defaults = UserDefaults(suiteName: "mm-desk-tests-\(UUID().uuidString)")!
        let model = BriefModel(link: link, notices: notices, defaults: defaults)
        // The script's first answer is the empty run: read again for the one that filed.
        await model.refresh()
        await model.refresh()
        XCTAssertEqual(model.tidyShown?.group, "tidy-1")

        model.dismissTidy()

        XCTAssertNil(model.tidyShown)
        XCTAssertNil(BriefModel(link: link, notices: notices, defaults: defaults).tidyShown)
        XCTAssertEqual(defaults.string(forKey: BriefModel.tidyDismissedKey), "tidy-1")
    }

    // MARK: Review and tasks

    func testAReviewNotYetWrittenIsWrittenOnOpen() async {
        routes.answer("GET", "/api/review", API.ReviewAnswer.reviewMissing(API.ReviewMissing(missing: true, ok: true)))
        routes.answer("POST", "/api/review", DeskFixture.review)
        let model = ReviewModel(link: link)

        await model.open()

        XCTAssertEqual(model.review, DeskFixture.review)
        await model.open()
        XCTAssertEqual(routes.sent("POST", "/api/review").count, 1, "an open review isn't written again")
    }

    func testAReviewWithAIOffSaysSo() async {
        routes.answer("GET", "/api/review", API.ReviewAnswer.reviewMissing(API.ReviewMissing(missing: true, ok: true)))
        aiOff("POST", "/api/review")
        let model = ReviewModel(link: link)

        await model.open()

        XCTAssertEqual(model.stage, .aiOff)
    }

    func testATaskMarkedDoneIsSentAndTheListReadAgain() async {
        routes.answer("GET", "/api/tasks", DeskFixture.tasks)
        routes.answer("POST", "/api/tasks/done", API.TaskGone(ok: true))
        let model = TasksModel(link: link, notices: notices)
        await model.load()
        XCTAssertEqual(model.open, 1)

        await model.mark(model.tasks[0], done: true)

        XCTAssertEqual(routes.sent("POST", "/api/tasks/done").first?["id"], .number(1))
        XCTAssertEqual(routes.sent("POST", "/api/tasks/done").first?["done"], .bool(true))
        XCTAssertEqual(routes.sent("GET", "/api/tasks").count, 2)
        XCTAssertEqual(TasksModel.copy(of: model.tasks[0]), .named(key: 104, folder: "INBOX", uid: 4, account: "ada@example.test"))
        XCTAssertNil(TasksModel.copy(of: model.tasks[1]), "a task whose mail is gone opens nothing")
    }

    // MARK: The Screener and curation

    func testTheScreenerDecidesBySenderAndWritesARule() async {
        routes.answer("GET", "/api/screener", DeskFixture.screener)
        routes.answer("POST", "/api/screener/suggest", DeskFixture.suggestions)
        routes.answer("POST", "/api/rules", API.RuleChanged(ok: true, reclassified: 12, rules: DeskFixture.rules,
                                                            token: "offers@deals.example.test", trashed: true))
        let model = ScreenerModel(account: "ada@example.test", link: link, notices: notices)
        await model.load()
        XCTAssertEqual(model.rows.count, 3)
        XCTAssertEqual(routes.sent("GET", "/api/screener").first?.query, ["account": "ada@example.test"])

        await model.suggest()
        XCTAssertEqual(model.suggestions?["offers@deals.example.test"]?.verdict, .block)
        await model.decide("offers@deals.example.test", .block)

        let rule = routes.sent("POST", "/api/rules").first
        XCTAssertEqual(rule?["token"], .string("offers@deals.example.test"))
        XCTAssertEqual(rule?["verdict"], .string("block"))
        XCTAssertEqual(said, "Screened out Daily Deals · trashed", "the sender as the app names them")
    }

    /// The Screener's words name a sender as the rest of the app does (`Words.calling`): a person by their first name,
    /// an organisation whole, and one with no name by their whole address, never the part before the @ in lower case.
    func testTheScreenersWordsNameTheSenderAsTheAppDoes() async {
        var queue = DeskFixture.screener
        queue.items.append(API.ScreenerItem(count: 1, domain: "example.test", fromAddr: "lin@example.test", fromName: "",
                                            when: "today"))
        // An organisation whose address carries the last word of its name: said whole, never "Guild".
        queue.items.append(API.ScreenerItem(count: 1, domain: "example.test", fromAddr: "gazette@example.test",
                                            fromName: "Guild Gazette", when: "today"))
        routes.answer("GET", "/api/screener", queue)
        routes.answer("POST", "/api/rules", API.RuleChanged(ok: true, reclassified: 1, rules: DeskFixture.rules,
                                                            token: "x", trashed: false))
        let model = ScreenerModel(account: "ada@example.test", link: link, notices: notices)
        await model.load()
        await model.decide("marie@example.test", .keep)
        XCTAssertEqual(said, "Allowed Marie")
        await model.decide("hello@studio.example.test", .block)
        XCTAssertEqual(said, "Screened out Studio Nine")
        await model.decide("lin@example.test", .block)
        XCTAssertEqual(said, "Screened out lin@example.test")
        await model.decide("gazette@example.test", .block)
        XCTAssertEqual(said, "Screened out Guild Gazette")
    }

    func testASenderWhoCameAfterTheAIWasAskedIsNotAskedYetUntilSuggestAgain() async {
        let newcomer = API.ScreenerItem(count: 1, domain: "example.test", fromAddr: "rosalind@example.test",
                                        fromName: "Rosalind Franklin", when: "now")
        var later = DeskFixture.screener
        later.items.insert(newcomer, at: 0)
        later.total = 4
        routes.answer("GET", "/api/screener", DeskFixture.screener)
        routes.answer("GET", "/api/screener", later)
        routes.answer("POST", "/api/screener/suggest", DeskFixture.suggestions)
        var again = DeskFixture.suggestions
        again.asked = 4
        routes.answer("POST", "/api/screener/suggest", again)
        let model = ScreenerModel(account: "ada@example.test", link: link, notices: notices)
        await model.load()
        XCTAssertEqual(model.said(of: "hello@studio.example.test"), .notAsked)

        await model.suggest()
        XCTAssertEqual(model.said(of: "hello@studio.example.test"), .unsure, "asked, and not sure")
        XCTAssertEqual(model.said(of: "marie@example.test"), .suggests(DeskFixture.suggestions.suggestions[0]))

        await model.load()
        XCTAssertEqual(model.said(of: "rosalind@example.test"), .notAsked, "she came after the AI was asked")
        XCTAssertEqual(routes.sent("POST", "/api/screener/suggest").count, 1, "only a press asks")

        await model.suggest()
        XCTAssertEqual(model.said(of: "rosalind@example.test"), .unsure)
        XCTAssertEqual(routes.sent("POST", "/api/screener/suggest").count, 2)
    }

    func testScreenerSuggestionsWithAIOffSaySo() async {
        aiOff("POST", "/api/screener/suggest")
        let model = ScreenerModel(account: "ada@example.test", link: link, notices: notices)

        await model.suggest()

        XCTAssertNil(model.suggestions)
        XCTAssertEqual(said, "AI is off: there is no API key")
    }

    func testASenderCleanupIsPreviewedArmedThenRunByItsToken() async {
        routes.answer("GET", "/api/cleanup/sender", DeskFixture.senderPreview)
        routes.answer("POST", "/api/cleanup/execute", API.CleanedUp(
            blockScope: .sender, blockedToken: "news@shop.example.test", method: "one-click", ok: true,
            reversible: API.Reversible(block: true, sweep: true, unsubscribe: false), status: "unsubscribed_oneclick",
            sweepCapped: false, sweepFailed: 0, sweepQueued: 0, swept: 31, undoGroup: "ug-1"))
        let model = CurateModel(link: link, notices: notices)

        await model.previewCleanup("news@shop.example.test")
        XCTAssertEqual(model.cleaning?.unsubscribe, true, "a one-click unsubscribe is on by default")
        await model.pressCleanup()
        XCTAssertEqual(model.cleaning?.armed, true)
        XCTAssertTrue(routes.sent("POST", "/api/cleanup/execute").isEmpty)
        await model.pressCleanup()

        let sent = routes.sent("POST", "/api/cleanup/execute").first
        XCTAssertEqual(sent?["confirm_token"], .string("tok-1"))
        XCTAssertEqual(sent?["from_addr"], .string("news@shop.example.test"))
        XCTAssertEqual(sent?["do_unsubscribe"], .bool(true))
        XCTAssertNil(model.cleaning)
        XCTAssertEqual(said, "Blocked · swept 31 · unsubscribed")
        XCTAssertTrue(undoable)
    }

    func testASweepIsPlannedThenRunOnConfirm() async {
        let going = [API.SweepBucket(bucket: "newsletters/marketing", count: 12, senders: [
            API.SweepSender(count: 9, fromAddr: "news@loom.example.test"),
            API.SweepSender(count: 3, fromAddr: "deals@loom.example.test")], subjects: ["Issue 12", "Spring sale"])]
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: true, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", going: going, heldBack: 2, planned: 12)]))
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: false, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", moved: 11, planned: 12, stragglers: ["k-9"])],
            undoGroup: "ug-sweep"))
        let model = CurateModel(link: link, notices: notices)

        await model.armSweep("ada@example.test")
        XCTAssertEqual(model.sweepArmed?.planned, 12)
        XCTAssertEqual(model.sweepArmed?.going, going, "what goes is shown before Confirm")
        await model.sweep()

        let sent = routes.sent("POST", "/api/sweep")
        XCTAssertEqual(sent.map { $0["dry_run"] }, [.bool(true), .bool(false)])
        XCTAssertNil(sent.first?["undo_group"], "a plan moves nothing, so it has nothing to undo")
        guard case .string(let group)? = sent.last?["undo_group"] else { return XCTFail("the sweep names its undo group") }
        XCTAssertTrue(group.hasPrefix("ug-"))
        XCTAssertNil(model.sweepArmed)
        XCTAssertEqual(said, "Swept 11/12 · 1 unverified")
        XCTAssertTrue(undoable)
    }

    /// Planning a sweep reads the whole Inbox from its server, and the sweep reads it again as it moves what goes: while
    /// either runs the account's card says so, and Sweep and Confirm ask nothing more of the server.
    func testWhileASweepIsPlannedOrRunsItSaysSoAndIsntAskedAgain() async {
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: true, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", planned: 12)]))
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: false, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", moved: 12, planned: 12)], undoGroup: "ug-sweep"))
        let model = CurateModel(link: link, notices: notices)

        routes.hold("POST", "/api/sweep")
        let planning = Task { await model.armSweep("ada@example.test") }
        await deskEventually { model.sweepBusy != nil }
        XCTAssertEqual(model.sweepBusy, .init(account: "ada@example.test", step: .planning))
        XCTAssertEqual(model.sweepBusy?.words, "Reading this Inbox to plan the sweep…")
        await model.armSweep("ada@example.test")
        await model.armSweep("work@example.test")
        XCTAssertEqual(routes.sent("POST", "/api/sweep").count, 1, "Sweep pressed again asks nothing more")
        routes.release("POST", "/api/sweep")
        await planning.value
        XCTAssertNil(model.sweepBusy, "it clears when the answer comes")
        XCTAssertEqual(model.sweepArmed?.planned, 12)

        routes.hold("POST", "/api/sweep")
        let sweeping = Task { await model.sweep() }
        await deskEventually { model.sweepBusy != nil }
        XCTAssertEqual(model.sweepBusy, .init(account: "ada@example.test", step: .sweeping))
        XCTAssertEqual(model.sweepBusy?.words, "Sweeping…")
        await model.sweep()
        await model.armSweep("ada@example.test")
        XCTAssertEqual(routes.sent("POST", "/api/sweep").count, 2, "Confirm and Sweep wait for the answer")
        routes.release("POST", "/api/sweep")
        await sweeping.value
        XCTAssertNil(model.sweepBusy)
        XCTAssertEqual(said, "Swept 12/12")
    }

    /// One plan on screen at a time: planning another account's sweep takes away the plan waiting for Confirm, whose
    /// Confirm would do nothing while the new plan is read.
    func testPlanningAnotherAccountsSweepTakesAwayThePlanWaitingForConfirm() async {
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: true, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", planned: 12)]))
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: true, generated: "", ok: true, results: [
            API.AccountSwept(account: "work@example.test", planned: 4)]))
        let model = CurateModel(link: link, notices: notices)
        await model.armSweep("ada@example.test")
        XCTAssertEqual(model.sweepArmed?.account, "ada@example.test")

        routes.hold("POST", "/api/sweep")
        let planning = Task { await model.armSweep("work@example.test") }
        await deskEventually { model.sweepBusy != nil }
        XCTAssertNil(model.sweepArmed, "Ada's plan isn't left on screen with a Confirm that would do nothing")
        routes.release("POST", "/api/sweep")
        await planning.value
        XCTAssertEqual(model.sweepArmed?.account, "work@example.test", "only the new plan waits for Confirm")
        XCTAssertEqual(model.sweepArmed?.planned, 4)
    }

    /// A plan the server refused clears too, so Sweep can be pressed again.
    func testASweepsPlanThatFailedLetsSweepBePressedAgain() async {
        routes.refuse("POST", "/api/sweep", status: 502, detail: "The server is away")
        let model = CurateModel(link: link, notices: notices)
        await model.armSweep("ada@example.test")
        XCTAssertNil(model.sweepBusy)
        await model.armSweep("ada@example.test")
        XCTAssertEqual(routes.sent("POST", "/api/sweep").count, 2)
    }

    /// The server looked at the Inbox again and found nothing had left it, so it kept no way back.
    func testASweepTheServerStoppedBeforeAnythingMovedOffersNoUndo() async {
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: true, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", planned: 3)]))
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: false, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", error: "abort", moved: 0, planned: 3)]))
        let model = CurateModel(link: link, notices: notices)

        await model.armSweep("ada@example.test")
        await model.sweep()

        XCTAssertEqual(said, "Couldn’t reach the mail server, so nothing was swept. Try again in a moment. (abort)")
        XCTAssertFalse(undoable, "nothing went, so there is nothing to undo")
    }

    /// A sweep stopped where the server couldn't be asked again what had left the Inbox counts none gone, but keeps a
    /// way back for all it meant to move: it says it couldn't check how much went, not that nothing was swept, and
    /// offers the Undo that puts back whatever did.
    func testASweepStoppedWhereTheServerCantBeAskedAgainSaysSoAndOffersUndo() async {
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: true, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", planned: 3)]))
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: false, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", error: "ConnectionLost", moved: 0, planned: 3)],
            undoGroup: "ug-sweep"))
        let model = CurateModel(link: link, notices: notices)

        await model.armSweep("ada@example.test")
        await model.sweep()

        XCTAssertEqual(said, """
            Couldn’t reach the mail server, so the sweep stopped part way and MailMind couldn’t check how much went. \
            Undo puts back whatever did; sweep again for the rest. (ConnectionLost)
            """)
        XCTAssertTrue(undoable, "some of it may be in the Trash, and Undo brings back whatever is")
    }

    /// A sweep the server stopped part way says how many went, that Undo brings them back, and to sweep again for
    /// the rest; its class only after the words.
    func testASweepStoppedPartWaySaysUndoBringsBackWhatWent() async {
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: true, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", planned: 30)]))
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: false, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", error: "ConnectionLost", moved: 12, planned: 30)],
            undoGroup: "ug-sweep"))
        let model = CurateModel(link: link, notices: notices)

        await model.armSweep("ada@example.test")
        await model.sweep()

        XCTAssertEqual(said, """
            Couldn’t reach the mail server, so the sweep stopped after 12. Undo brings them back; sweep again for the \
            rest. (ConnectionLost)
            """)
        XCTAssertTrue(undoable)
        XCTAssertEqual(CurateModel.sweepStopped("ConnectionLost", moved: 1), """
            Couldn’t reach the mail server, so the sweep stopped after 1. Undo brings it back; sweep again for the rest. \
            (ConnectionLost)
            """)
    }

    /// A plan the server couldn't make says so in words, and one that got no answer says what to do, or the server's
    /// own words when it refused.
    func testASweepsPlanThatFailedSaysWhatToDo() async {
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: true, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", error: "TimeoutError", planned: 0)]))
        let model = CurateModel(link: link, notices: notices)
        await model.armSweep("ada@example.test")
        XCTAssertNil(model.sweepArmed)
        XCTAssertEqual(said, "Couldn’t reach the mail server, so nothing was swept. Try again in a moment. (TimeoutError)")

        let none = DeskRoutes()
        none.answer("POST", "/api/sweep", status: 503, json: "{}")
        let unanswered = CurateModel(link: CoreLink(none), notices: notices)
        await unanswered.armSweep("ada@example.test")
        XCTAssertEqual(said, "Couldn’t look at this Inbox. Try again in a moment.")

        let refusing = DeskRoutes()
        refusing.refuse("POST", "/api/sweep", status: 409, detail: "MailMind is read-only.")
        await CurateModel(link: CoreLink(refusing), notices: notices).armSweep("ada@example.test")
        XCTAssertEqual(said, "MailMind is read-only.")
        XCTAssertEqual(CurateModel.sweepFailed(Problem(CoreAnswer(status: 502, contentType: "", headers: [], body: Data())),
                                               planning: false),
                       "The sweep didn’t finish. Try again in a moment.")
    }

    func testACardOffersToSweepOnlyWhatASweepWouldMove() {
        var ada = DeskFixture.stats.accounts[0]
        XCTAssertEqual(CurateModel.sweepOffer(ada), 20, "the junk read and unstarred, not all 36")
        ada.sweepable = 0
        XCTAssertNil(CurateModel.sweepOffer(ada), "no button when a sweep would move nothing")
    }

    func testASweepThatLeavesEverythingSaysWhatStays() async {
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: true, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", heldBack: 3, planned: 0)]))
        let model = CurateModel(link: link, notices: notices)

        await model.armSweep("ada@example.test")

        XCTAssertNil(model.sweepArmed)
        XCTAssertEqual(said, "Nothing to sweep: 3 starred or unread stay")
    }

    func testASweepsPlanSaysWhatStaysWhatWaitsAndWhatUndoCantBringBack() {
        let plan = API.AccountSwept(account: "ada@example.test", capped: true, heldBack: 1, noUndo: 2, planned: 500)
        XCTAssertEqual(CurateModel.sweepNotes(plan), [
            "1 starred or unread message stays in your Inbox.",
            "The newest 500 only — sweep again for the rest.",
            "2 of them can’t be brought back with Undo."])
        XCTAssertEqual(CurateModel.undoLine(plan), "Undo puts the rest back.", "not all of it, beside the line above")
        let whole = API.AccountSwept(account: "ada@example.test", capped: false, heldBack: 4, noUndo: 0, planned: 3)
        XCTAssertEqual(CurateModel.sweepNotes(whole), ["4 starred or unread messages stay in your Inbox."])
        XCTAssertEqual(CurateModel.undoLine(whole), "Undo puts it all back.")
        XCTAssertEqual(CurateModel.undoLine(API.AccountSwept(account: "ada@example.test", planned: 3)),
                       "Undo puts it all back.")
        XCTAssertEqual(CurateModel.bucketWords("newsletters/marketing"), "Newsletters and marketing")
        XCTAssertEqual(CurateModel.bucketWords("something/new"), "something/new")
        XCTAssertEqual(CurateModel.sweepSenders(API.SweepBucket(bucket: "spam/scam", count: 3, senders: [
            API.SweepSender(count: 2, fromAddr: "win@prizes.example.test"),
            API.SweepSender(count: 1, fromAddr: "lotto@prizes.example.test")], subjects: [])),
                       "win@prizes.example.test (2), lotto@prizes.example.test")
    }

    func testARuleChangedReadsTheRulesTheServerSends() async {
        routes.answer("POST", "/api/rules", API.RuleChanged(ok: true, reclassified: 0, rules: DeskFixture.rules,
                                                            token: "school.example.test", trashed: false))
        let model = CurateModel(link: link, notices: notices)
        var changed = 0
        model.changed = { changed += 1 }

        await model.rule("  school.example.test ", .keep)

        XCTAssertEqual(routes.sent("POST", "/api/rules").first?["token"], .string("school.example.test"))
        XCTAssertEqual(model.rules, DeskFixture.rules)
        XCTAssertEqual(said, "Keeping school.example.test")
        XCTAssertEqual(changed, 0, "nothing stored was read differently")
    }

    /// What names no sender (a number pasted before the address) is refused in the server's words, which the sheet
    /// says as the page's toast does; a rule that got no answer says it failed.
    func testARuleTheServerRefusesSaysWhyInItsWords() async {
        let words = "A rule names one address, such as name@example.com, or one domain, such as example.com."
        routes.refuse("POST", "/api/rules", status: 400, detail: words)
        let model = CurateModel(link: link, notices: notices)

        let kept = await model.rule("50506 spam@lottery.example.test", .keep)

        XCTAssertFalse(kept, "so the field keeps what was typed")
        XCTAssertEqual(said, words)
        XCTAssertNil(model.rules)
        let none = DeskRoutes()
        none.answer("POST", "/api/rules", status: 503, json: "{}")
        await CurateModel(link: CoreLink(none), notices: notices).rule("spam@lottery.example.test", .block)
        XCTAssertEqual(said, "Rule failed")
    }

    // MARK: A sentence in the search field

    func testASentenceIsTranslatedOnceAndOnlyWithAIOn() async {
        routes.answer("POST", "/api/ai/do-plan", API.AiDoPlanAnswer.planIsASearch(
            API.PlanIsASearch(filter: " from:lin newer_than:30d ", intent: "search", ok: true)))
        let prose = ProseSearch(link: link)

        let off = await prose.translate("invoices from lin last month", aiOn: false)
        XCTAssertNil(off)
        let literal = await prose.translate("quarterly budget review", aiOn: true)
        XCTAssertNil(literal)
        XCTAssertTrue(routes.sent.isEmpty, "with AI off, or words that aren't a sentence, nothing is asked")

        let first = await prose.translate("invoices from lin last month", aiOn: true)
        let again = await prose.translate("invoices from lin last month", aiOn: true)
        XCTAssertEqual(first, "from:lin newer_than:30d")
        XCTAssertEqual(again, "from:lin newer_than:30d")
        XCTAssertEqual(routes.sent("POST", "/api/ai/do-plan").count, 1, "the same words again cost nothing")
        XCTAssertEqual(routes.sent("POST", "/api/ai/do-plan").first?["cmd"], .string("invoices from lin last month"))
        XCTAssertNil(prose.translating)

        prose.keepLiteral("invoices from lin last month")
        let kept = await prose.translate("invoices from lin last month", aiOn: true)
        XCTAssertNil(kept, "a translation taken back stays taken back")
    }

    func testAFailedOrSameTranslationLeavesTheLiteralSearch() async {
        overLimit("POST", "/api/ai/do-plan")
        routes.answer("POST", "/api/ai/do-plan", API.AiDoPlanAnswer.planIsASearch(
            API.PlanIsASearch(filter: "Mail From Lin Yesterday", intent: "search", ok: true)))
        let prose = ProseSearch(link: link)

        let limited = await prose.translate("mail from lin yesterday", aiOn: true)
        XCTAssertNil(limited)
        let same = await prose.translate("mail from lin yesterday", aiOn: true)
        XCTAssertNil(same)
        XCTAssertNil(prose.translating)
    }

    // MARK: Commands

    func testAttentionsCommandsFollowTheStack() {
        let model = AttentionModel(link: link, notices: notices)
        var asked: [MailCopy] = []
        let inbox = AttentionCommands.list(copies: [DeskFixture.ada], stack: nil, model: model) { asked = $0 }
        XCTAssertEqual(inbox.map(\.id), ["snooze", "mute", "later"])
        XCTAssertEqual(inbox.map(\.key), ["h", "w", "l"])
        XCTAssertEqual(inbox.map(\.label), ["Snooze…", "Mute thread", "Reply later"])
        inbox[0].run()
        XCTAssertEqual(asked, [DeskFixture.ada])

        let muted = AttentionCommands.list(copies: [DeskFixture.ada], stack: .muted, model: model) { _ in }
        XCTAssertEqual(muted[1].label, "Unmute thread")
        let later = AttentionCommands.list(copies: [DeskFixture.ada], stack: .replyLater, model: model) { _ in }
        XCTAssertEqual(later[2].label, "Reply later — Done")
        let waiting = AttentionCommands.list(copies: [DeskFixture.ada], stack: .waiting, model: model) { _ in }
        XCTAssertEqual(waiting.last?.id, "dismiss")
        XCTAssertTrue(AttentionCommands.list(copies: [], stack: nil, model: model) { _ in }.isEmpty)
    }

    func testAMuteCommandMutesWhatWasChosen() async {
        routes.answer("POST", "/api/mute", API.OnThread(ok: true, threadId: "t-1"))
        let model = AttentionModel(link: link, notices: notices)

        AttentionCommands.list(copies: [DeskFixture.grace], stack: nil, model: model) { _ in }[1].run()

        await deskEventually { self.said == "Muted" }
        XCTAssertEqual(routes.sent("POST", "/api/mute").first?["key"], .number(102))
    }

    /// What the desk says of a refusal or a failure shows a refusal's mark, never the tick, as the mailbox's do: a rule
    /// the server turned down, said on the desk's own toast and through the window's.
    func testARefusalShowsTheAlertNotTheTick() async {
        routes.refuse("POST", "/api/rules", status: 400, detail: "A rule names one address or one domain.")
        let model = CurateModel(link: link, notices: notices)
        let kept = await model.rule("not an address", .block)
        XCTAssertFalse(kept)
        XCTAssertEqual(said, "A rule names one address or one domain.")
        XCTAssertEqual(notices.shown?.refused, true)
        XCTAssertEqual(MailboxToast.icon(said ?? "", refused: true), .alert)
        XCTAssertEqual(MailboxToast.icon(said ?? ""), .check, "its words alone would have taken the tick")

        let app = MailApp()
        let mailbox = app.mailbox
        notices.relay = { text, undo, refused in mailbox.say(text, undo: undo, refused: refused) }
        routes.refuse("POST", "/api/rules", status: 400, detail: "A rule names one address or one domain.")
        _ = await model.rule("not an address", .block)
        XCTAssertEqual(mailbox.toast?.refused, true, "the window's toast says it as a refusal too")
        XCTAssertEqual(MailboxToast.icon(mailbox.toast?.text ?? "", refused: mailbox.toast?.refused ?? false), .alert)
        withExtendedLifetime(app) {}
    }

    /// The status's own refusals (a setting the server turned down, say) show the alert as well.
    func testTheStatussRefusalsShowTheAlert() {
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        status.say("The Undo Send window must be 5 to 60 seconds.", refused: true)
        XCTAssertEqual(status.notice?.refused, true)
        status.say("Diagnostics copied")
        XCTAssertEqual(status.notice?.refused, false)
    }

    /// Curate's sheet says the window's toast on itself while it is up, so the window's foot, under it, doesn't say it
    /// again; other sheets leave it to the window.
    func testCuratesToastIsSaidOnceOnItsSheet() {
        XCTAssertFalse(WindowToasts.saysMailboxToast(under: .curate))
        XCTAssertTrue(WindowToasts.saysMailboxToast(under: nil))
        XCTAssertTrue(WindowToasts.saysMailboxToast(under: .review))
        XCTAssertTrue(WindowToasts.saysMailboxToast(under: .commitments))
    }

    func testNoticesGoWhereTheWindowSaysThem() {
        var heard: [String] = []
        notices.relay = { text, undo, _ in heard.append("\(text), Undo \(undo ?? "none")") }

        notices.say("Snoozed", undo: "Snooze")
        notices.say("Unsnoozed")

        XCTAssertNil(notices.shown)
        XCTAssertEqual(heard, ["Snoozed, Undo Snooze", "Unsnoozed, Undo none"], "what Undo takes back, by name")
    }

    // MARK: Invites

    func testAnRSVPIsQueuedAndUndoCancelsIt() async {
        let queued = API.RsvpQueued(decision: .accept, ok: true, organizer: "Grace Hopper", outboxId: 77, queued: true,
                                    summary: "Design review", undoSeconds: 10)
        routes.answer("POST", "/api/invite/rsvp", queued)
        routes.answer("POST", "/api/outbox/cancel", API.Cancelled(ok: true))
        let replies = InviteReplies(link: link, notices: notices)

        await replies.reply(to: DeskFixture.ada, .accept)
        guard case .queued(let held, _)? = replies.said[DeskFixture.ada] else { return XCTFail("not queued") }
        XCTAssertEqual(held, queued)
        XCTAssertEqual(said, "RSVP: Accepted ✓")
        XCTAssertEqual(routes.sent("POST", "/api/invite/rsvp").first?["decision"], .string("accept"))

        await replies.undo(DeskFixture.ada)

        XCTAssertEqual(routes.sent("POST", "/api/outbox/cancel").first?["id"], .number(77))
        XCTAssertEqual(replies.said[DeskFixture.ada], .taken(.accept))
        XCTAssertEqual(said, "RSVP not sent")
        replies.again(DeskFixture.ada)
        XCTAssertNil(replies.said[DeskFixture.ada])
    }

    func testAnUndoTooLateSaysTheReplyWent() async {
        routes.answer("POST", "/api/invite/rsvp", API.RsvpQueued(
            decision: .decline, ok: true, organizer: "", outboxId: 78, queued: true, summary: "", undoSeconds: 10))
        routes.refuse("POST", "/api/outbox/cancel", status: 409, detail: "Already sent.")
        let replies = InviteReplies(link: link, notices: notices)

        await replies.reply(to: DeskFixture.ada, .decline)
        await replies.undo(DeskFixture.ada)

        guard case .sent? = replies.said[DeskFixture.ada] else { return XCTFail("not sent") }
        XCTAssertEqual(said, "✓ Sent — too late to undo")
    }

    func testAFailedRSVPSaysWhy() async {
        routes.refuse("POST", "/api/invite/rsvp", status: 409, detail: "MailMind is read-only.")
        let replies = InviteReplies(link: link, notices: notices)

        await replies.reply(to: DeskFixture.ada, .tentative)

        XCTAssertEqual(replies.said[DeskFixture.ada], .failed("Couldn’t RSVP: MailMind is read-only."))
        XCTAssertEqual(said, "RSVP failed")
    }
}
