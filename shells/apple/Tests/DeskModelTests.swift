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
        routes.refuse(method, path, status: 409, type: "ai-off", detail: "AI is off: no Claude API key.",
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
        XCTAssertEqual(said, "Snoozed")
        XCTAssertTrue(undoable)
        XCTAssertEqual(changed, 1)
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
        XCTAssertEqual(model.asking, .failed(question: "anything", why: "AI is off"))
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

    private func scriptTheBrief(ai: String = "anthropic", briefing: API.BriefingAnswer) {
        routes.answer("GET", "/api/digest", DeskFixture.digest(ai: ai))
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

    func testWithAIOffTheBriefStandsOnTheDigestAndAsksNothing() async {
        scriptTheBrief(ai: "none", briefing: Self.missing)
        let model = briefModel()

        await model.load()

        XCTAssertTrue(routes.sent("POST", "/api/briefing").isEmpty)
        XCTAssertNil(model.brief)
        XCTAssertNil(model.trouble)
        XCTAssertEqual(model.aiOn, false)
        XCTAssertEqual(model.needs.count, 3)
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
        XCTAssertEqual(said, "Screened out offers · trashed")
    }

    func testScreenerSuggestionsWithAIOffSaySo() async {
        aiOff("POST", "/api/screener/suggest")
        let model = ScreenerModel(account: "ada@example.test", link: link, notices: notices)

        await model.suggest()

        XCTAssertNil(model.suggestions)
        XCTAssertEqual(said, "AI is off")
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
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: true, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", planned: 12)]))
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: false, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", moved: 11, planned: 12, stragglers: ["k-9"])]))
        let model = CurateModel(link: link, notices: notices)

        await model.armSweep("ada@example.test")
        XCTAssertEqual(model.sweepArmed?.planned, 12)
        await model.sweep()

        XCTAssertEqual(routes.sent("POST", "/api/sweep").map { $0["dry_run"] }, [.bool(true), .bool(false)])
        XCTAssertNil(model.sweepArmed)
        XCTAssertEqual(said, "Swept 11/12 · 1 unverified")
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

    func testNoticesGoWhereTheWindowSaysThem() {
        var heard: [String] = []
        notices.relay = { text, undoable in heard.append("\(text) \(undoable)") }

        notices.say("Snoozed", undoable: true)

        XCTAssertNil(notices.shown)
        XCTAssertEqual(heard, ["Snoozed true"])
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
