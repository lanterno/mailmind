import SwiftUI
import XCTest

/// The desk's screens, sheets and cards drawn as a window draws them, in the light and the dark, over fictional mail,
/// to PNGs under shells/apple/build/screens/desk for a person to look at. Each test holds only that the view draws;
/// what it shows is read off the pictures.
@MainActor
final class DeskScreenTests: XCTestCase {
    private var routes: DeskRoutes!
    private var link: CoreLink!
    private var notices: DeskNotices!

    override func setUp() async throws {
        routes = DeskRoutes()
        link = CoreLink(routes)
        notices = DeskNotices()
    }

    private func draw<V: View>(_ name: String, width: CGFloat, height: CGFloat? = nil, _ view: V) throws {
        let files = try DeskScreens.draw(name, width: width, height: height, view)
        for file in files {
            XCTAssertTrue(FileManager.default.fileExists(atPath: file.path), file.lastPathComponent)
        }
    }

    private func scriptTheBrief(ai: String = "anthropic", off: API.AiOff = .noKey) {
        routes.answer("GET", "/api/digest", DeskFixture.digest(ai: ai, off: off))
        // With AI off none was written.
        routes.answer("GET", "/api/briefing", ai == "none"
            ? API.BriefingAnswer.briefMissing(API.BriefMissing(cached: false, groups: [], missing: true, narrative: "",
                                                               ok: true, stale: false))
            : API.BriefingAnswer.brief(DeskFixture.brief))
        routes.answer("GET", "/api/upcoming", DeskFixture.upcoming)
        routes.answer("GET", "/api/waiting-on-others", DeskFixture.waiting)
        routes.answer("GET", "/api/tidy", API.TidyRun(filed: 0, group: nil, items: [], ok: true))
        routes.answer("GET", "/api/folders", API.Folders(account: "ada@example.test", folders: [], muted: 1, ok: true,
                                                         replylater: 2, snoozed: 3, waiting: 1))
        routes.answer("GET", "/api/tasks", DeskFixture.tasks)
    }

    private func briefModel() -> BriefModel {
        BriefModel(link: link, notices: notices, defaults: UserDefaults(suiteName: "mm-desk-screens-\(UUID().uuidString)")!)
    }

    private static let sunday = Date(timeIntervalSince1970: 1_794_739_600)

    func testTheBrief() async throws {
        scriptTheBrief()
        let model = briefModel()
        await model.load()
        try draw("brief", width: 1000, height: 940, BriefScreen(
            model: model, attention: AttentionModel(link: link, notices: notices), actions: BriefActions(), now: Self.sunday))
    }

    func testTheBriefWithAIOff() async throws {
        scriptTheBrief(ai: "none")
        let model = briefModel()
        await model.load()
        try draw("brief-ai-off", width: 1000, height: 820, BriefScreen(
            model: model, attention: AttentionModel(link: link, notices: notices), actions: BriefActions(), now: Self.sunday))
    }

    /// Turned off in Settings (#49): the tallies under a plain heading, with no word of AI and no week.
    func testTheBriefWithAITurnedOff() async throws {
        scriptTheBrief(ai: "none", off: .turnedOff)
        let model = briefModel()
        await model.load()
        try draw("brief-ai-turned-off", width: 1000, height: 820, BriefScreen(
            model: model, attention: AttentionModel(link: link, notices: notices), actions: BriefActions(), now: Self.sunday))
    }

    func testTheReadersCards() async throws {
        routes.answer("POST", "/api/ai/summarize", DeskFixture.summary)
        routes.answer("POST", "/api/ai/extract", DeskFixture.extracted)
        routes.answer("POST", "/api/invite/rsvp", API.RsvpQueued(
            decision: .accept, ok: true, organizer: "Grace Hopper", outboxId: 7, queued: true, summary: "Design review",
            undoSeconds: 30))
        let summaries = SummaryCards(link: link)
        let suggestions = SuggestionCards(link: link)
        let replies = InviteReplies(link: link, notices: notices)
        await summaries.summarize(DeskFixture.ada)
        await suggestions.find(in: DeskFixture.ada)
        let cards = VStack(spacing: 8) {
            InviteCard(copy: DeskFixture.ada, invite: DeskFixture.invite, part: "2", replies: replies, link: link,
                       notices: notices)
            SummaryCard(copy: DeskFixture.ada, stored: nil, cards: summaries)
            SuggestionCard(copy: DeskFixture.ada, cards: suggestions, notices: notices)
        }
        .padding(16)
        try draw("reader-cards", width: 680, cards)

        await replies.reply(to: DeskFixture.ada, .accept)
        let answered = VStack(spacing: 8) {
            InviteCard(copy: DeskFixture.ada, invite: DeskFixture.invite, part: nil, replies: replies, link: link,
                       notices: notices)
            SummaryCard(copy: DeskFixture.grace, stored: ("Alan wants comments on chapter two before Wednesday.",
                                                          "Read Alan’s draft"), cards: summaries)
        }
        .padding(16)
        try draw("reader-cards-answered", width: 680, answered)
    }

    func testTheAnswer() async throws {
        routes.answer("POST", "/api/ai/ask", DeskFixture.answered)
        let model = AskModel(link: link)
        await model.ask("When is the dentist appointment?")
        try draw("ask", width: 760, height: 520, AskScreen(model: model, reading: nil, open: { _ in }, askAgain: {}))
        try draw("ask-card", width: 700,
                 AskCard(model: model, words: "When is the dentist appointment?", open: { _ in }).padding(8))
    }

    func testABulkActionArmed() async throws {
        routes.answer("POST", "/api/ai/do-plan", API.AiDoPlanAnswer.planPreview(DeskFixture.preview(.trash)))
        let model = PlanModel(link: link, notices: notices)
        await model.plan("delete the shop's newsletters older than a week")
        await model.press()
        try draw("plan-armed", width: 700, PlanCard(model: model,
                                                    words: "delete the shop's newsletters older than a week",
                                                     showSearch: { _ in }, close: {}).padding(8))
    }

    func testTheScreener() async throws {
        routes.answer("GET", "/api/screener", DeskFixture.screener)
        routes.answer("POST", "/api/screener/suggest", DeskFixture.suggestions)
        let model = ScreenerModel(account: "ada@example.test", link: link, notices: notices)
        await model.load()
        await model.suggest()
        try draw("screener", width: 820, height: 460, ScreenerScreen(model: model))
        try draw("screener-ai-turned-off", width: 820, height: 460, ScreenerScreen(model: model, suggests: false))
    }

    func testCurate() async throws {
        routes.answer("GET", "/api/cleanup/senders", DeskFixture.flood)
        routes.answer("GET", "/api/rules", API.RuleBook(rules: DeskFixture.rules))
        routes.answer("GET", "/api/stats", DeskFixture.stats)
        routes.answer("GET", "/api/audit", API.AuditTrail(events: [
            API.AuditEvent(account: "ada@example.test", from: "news@shop.example.test", kind: "sweep",
                           subject: "Ten deals for your weekend", swept: 31, ts: "2026-11-14T18:00:00Z"),
        ], hasMore: false))
        routes.answer("GET", "/api/cleanup/sender", DeskFixture.senderPreview)
        let model = CurateModel(link: link, notices: notices)
        await model.load()
        model.marked = ["news@shop.example.test"]
        let name: (String) -> String = { $0 == "ada@example.test" ? "Work" : $0 }
        try draw("curate-senders", width: 620, height: 600, CurateSheet(model: model, nameOf: name, open: { _ in }))
        try draw("curate-inboxes", width: 620, height: 600,
                 CurateSheet(model: model, nameOf: name, open: { _ in }, part: .inboxes))
        try draw("curate-rules", width: 620, height: 600,
                 CurateSheet(model: model, nameOf: name, open: { _ in }, part: .rules))
        try draw("curate-filters", width: 620, height: 600,
                 CurateSheet(model: model, nameOf: name, open: { _ in }, part: .filters))
        try draw("curate-activity", width: 620, height: 600,
                 CurateSheet(model: model, nameOf: name, open: { _ in }, part: .activity))

        // A sweep being planned: the card says so, and its Sweep waits.
        routes.answer("POST", "/api/sweep", API.Swept(dryRun: true, generated: "", ok: true, results: [
            API.AccountSwept(account: "ada@example.test", planned: 20)]))
        routes.hold("POST", "/api/sweep")
        let planning = Task { await model.armSweep("ada@example.test") }
        await deskEventually { model.sweepBusy != nil }
        try draw("curate-inboxes-planning", width: 620, height: 600,
                 CurateSheet(model: model, nameOf: name, open: { _ in }, part: .inboxes))
        routes.release("POST", "/api/sweep")
        await planning.value

        await model.previewCleanup("news@shop.example.test")
        await model.pressCleanup()
        try draw("cleanup-armed", width: 500, CleanupSheet(model: model))
        try draw("filter-new", width: 460, FilterSheet(form: .new("from:grace@example.test"), model: model))
    }

    func testTheWeekAndCommitments() async throws {
        scriptTheBrief()
        let review = ReviewModel(link: link)
        routes.answer("GET", "/api/review", API.ReviewAnswer.review(DeskFixture.review))
        await review.open()
        try draw("review", width: 620, height: 620, ReviewSheet(model: review, open: { _ in }))

        let brief = briefModel()
        await brief.refresh()
        let tasks = TasksModel(link: link, notices: notices)
        await tasks.load()
        try draw("commitments", width: 600, height: 560, CommitmentsSheet(
            tasks: tasks, brief: brief, attention: AttentionModel(link: link, notices: notices), open: { _ in }))
    }

    func testSnoozeAndNotices() throws {
        let attention = AttentionModel(link: link, notices: notices)
        try draw("snooze-until", width: 300, SnoozeChooser(
            copies: [DeskFixture.ada, DeskFixture.grace], model: attention, canUnsnooze: true, menu: MeadowMenuModel(),
            close: {}).padding(20))
        notices.say("Snoozed", undo: "Snooze")
        try draw("notice", width: 420, height: 80, DeskNoticeBanner(notices: notices, undo: {}).padding(12))
    }
}
