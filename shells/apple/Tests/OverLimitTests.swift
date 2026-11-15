import Foundation
import XCTest

/// An AI call a person started at today's limit asks first, as the page's do: yes sends the same call again once,
/// with their leave, and shows what came back; no leaves the words that the limit is reached. A call the app makes on
/// its own never asks.
@MainActor
final class OverLimitTests: XCTestCase {
    private var routes: DeskRoutes!
    private var link: CoreLink!
    private var notices: DeskNotices!

    /// What the server says at the limit: $15.25 spent of $15.
    private static let atTheLimit = OverLimit(spentMicros: 15_250_000, limitMicros: 15_000_000)

    override func setUp() async throws {
        routes = DeskRoutes()
        link = CoreLink(routes)
        notices = DeskNotices()
    }

    /// The person, answering every question the same way, and what they were asked.
    @MainActor
    final class Person {
        let yes: Bool
        private(set) var asked: [OverLimit] = []

        init(saying yes: Bool) {
            self.yes = yes
        }

        var ask: AskOverLimit {
            { [self] over in
                asked.append(over)
                return yes
            }
        }
    }

    private func person(saying yes: Bool) -> Person {
        let person = Person(saying: yes)
        link.asksOverLimit = person.ask
        return person
    }

    /// `method path` refuses at the limit once, or `times` times, then answers `answer`.
    private func overLimitOnce<T: Encodable>(_ method: String, _ path: String, times: Int = 1, then answer: T) {
        for _ in 0..<times {
            routes.refuse(method, path, status: 402, type: "over-limit",
                          detail: "today's AI spending has reached the daily limit",
                          more: #""over_limit": true, "spent_micros": 15250000, "limit_micros": 15000000"#)
        }
        routes.answer(method, path, answer)
    }

    /// The POSTs to `path`, each as whether it carried the person's leave.
    private func leaves(_ path: String) -> [Bool] {
        routes.sent("POST", path).map(\.overLimit)
    }

    // MARK: The desk

    func testASummaryAtTheLimitAsksAndOnYesIsWrittenWithTheLeave() async {
        overLimitOnce("POST", "/api/ai/summarize", then: DeskFixture.summary)
        let person = person(saying: true)
        let cards = SummaryCards(link: link)

        await cards.summarize(DeskFixture.ada)

        XCTAssertEqual(person.asked, [Self.atTheLimit])
        XCTAssertEqual(cards.card(for: DeskFixture.ada), .written(DeskFixture.summary))
        XCTAssertEqual(leaves("/api/ai/summarize"), [false, true], "the same call, sent again once with the leave")
        let sent = routes.sent("POST", "/api/ai/summarize")
        XCTAssertEqual(sent[0].body, sent[1].body)
    }

    func testASummaryAtTheLimitOnNoSaysSoAndSendsNothingMore() async {
        overLimitOnce("POST", "/api/ai/summarize", then: DeskFixture.summary)
        let person = person(saying: false)
        let cards = SummaryCards(link: link)

        await cards.summarize(DeskFixture.ada)

        XCTAssertEqual(person.asked, [Self.atTheLimit])
        XCTAssertEqual(cards.card(for: DeskFixture.ada), .failed("today’s AI limit is reached"))
        XCTAssertEqual(leaves("/api/ai/summarize"), [false])
    }

    func testEachCallAPersonStartsAsksAndGoesOverOnYes() async {
        overLimitOnce("POST", "/api/ai/extract", then: DeskFixture.extracted)
        overLimitOnce("POST", "/api/ai/ask", then: DeskFixture.answered)
        overLimitOnce("POST", "/api/ai/do-plan", then: API.AiDoPlanAnswer.planPreview(DeskFixture.preview(.archive)))
        overLimitOnce("POST", "/api/screener/suggest", then: DeskFixture.suggestions)
        overLimitOnce("POST", "/api/briefing/refresh", then: DeskFixture.brief)
        routes.answer("GET", "/api/review", API.ReviewAnswer.reviewMissing(API.ReviewMissing(missing: true, ok: true)))
        overLimitOnce("POST", "/api/review", then: DeskFixture.review)
        overLimitOnce("POST", "/api/review/refresh", then: DeskFixture.review)
        let person = person(saying: true)

        let suggestions = SuggestionCards(link: link)
        await suggestions.find(in: DeskFixture.ada)
        XCTAssertEqual(suggestions.card(for: DeskFixture.ada), .found(DeskFixture.extracted))
        let asking = AskModel(link: link)
        await asking.ask("When is the dentist appointment?")
        XCTAssertEqual(asking.asking, .answered(question: "When is the dentist appointment?", DeskFixture.answered))
        let plan = PlanModel(link: link, notices: notices)
        await plan.plan("archive the shop's newsletters")
        guard case .plan? = plan.doing else { return XCTFail("no plan: \(String(describing: plan.doing))") }
        let screener = ScreenerModel(account: "ada@example.test", link: link, notices: notices)
        await screener.suggest()
        XCTAssertEqual(screener.suggestions?.count, 2)
        let brief = BriefModel(link: link, notices: notices,
                               defaults: UserDefaults(suiteName: "mm-over-limit-\(UUID().uuidString)")!)
        await brief.write(again: true)
        XCTAssertEqual(brief.brief, DeskFixture.brief)
        XCTAssertNil(brief.trouble)
        let review = ReviewModel(link: link)
        await review.open()
        XCTAssertEqual(review.review, DeskFixture.review, "none stored: opening it writes it, and asks at the limit")
        await review.again()
        XCTAssertEqual(review.review, DeskFixture.review)

        let paths = ["/api/ai/extract", "/api/ai/ask", "/api/ai/do-plan", "/api/screener/suggest", "/api/briefing/refresh",
                     "/api/review", "/api/review/refresh"]
        for path in paths {
            XCTAssertEqual(leaves(path), [false, true], path)
        }
        XCTAssertEqual(person.asked, Array(repeating: Self.atTheLimit, count: paths.count), "one question for each")
    }

    func testOpeningTheBriefWithNoneStoredNeverAsksButWriteItDoes() async {
        routes.answer("GET", "/api/digest", DeskFixture.digest())
        routes.answer("GET", "/api/briefing", API.BriefingAnswer.briefMissing(API.BriefMissing(
            cached: false, groups: [], missing: true, narrative: "", ok: true, stale: false)))
        overLimitOnce("POST", "/api/briefing", times: 2, then: DeskFixture.brief)
        let person = person(saying: true)
        let brief = BriefModel(link: link, notices: notices,
                               defaults: UserDefaults(suiteName: "mm-over-limit-\(UUID().uuidString)")!)

        await brief.load()

        XCTAssertTrue(person.asked.isEmpty, "no one pressed anything")
        XCTAssertEqual(brief.trouble, .limit)
        XCTAssertEqual(leaves("/api/briefing"), [false])

        await brief.write(again: false)   // Write It

        XCTAssertEqual(person.asked, [Self.atTheLimit])
        XCTAssertEqual(brief.brief, DeskFixture.brief)
        XCTAssertEqual(leaves("/api/briefing"), [false, false, true])
    }

    func testASentenceSearchedForNeverAsks() async {
        overLimitOnce("POST", "/api/ai/do-plan", then: API.AiDoPlanAnswer.planIsASearch(
            API.PlanIsASearch(filter: "from:lin newer_than:1d", intent: "search", ok: true)))
        let person = person(saying: true)
        let prose = ProseSearch(link: link)

        let translated = await prose.translate("mail from lin yesterday", aiOn: true)

        XCTAssertNil(translated, "the literal search stands")
        XCTAssertTrue(person.asked.isEmpty)
        XCTAssertEqual(leaves("/api/ai/do-plan"), [false])
    }

    func testWithNoOneToAskACallAtTheLimitOnlySaysSo() async {
        overLimitOnce("POST", "/api/ai/ask", then: DeskFixture.answered)
        let asking = AskModel(link: link)

        await asking.ask("anything")

        XCTAssertEqual(asking.asking, .failed(question: "anything", why: "today’s AI limit is reached"))
        XCTAssertEqual(leaves("/api/ai/ask"), [false])
    }

    // MARK: The composer and the mailbox

    func testARewriteAtTheLimitAsksAndOnYesIsRewritten() async {
        let fake = FakeRoutes(), host = FakeHost(routes: fake), person = Person(saying: true)
        host.asksOverLimit = person.ask
        fake.answer("/api/ai/rewrite") { [unowned fake] _ in
            if fake.calls(to: "/api/ai/rewrite").count == 1 { throw FakeRoutes.overLimit }
            return #"{"ok": true, "op": "shorten", "text": "Short."}"#
        }
        let draft = Composer(ComposeSeed(), host: host)
        defer { draft.close(force: true) }
        draft.setBody("A long and winding note.", selection: NSRange(location: 0, length: 0))

        await draft.rewrite(.shorten)

        XCTAssertEqual(person.asked, [Self.atTheLimit])
        XCTAssertEqual(draft.body, "Short.")
        XCTAssertEqual(fake.calls(to: "/api/ai/rewrite").map(\.overLimit), [false, true])
    }

    func testADraftAtTheLimitOnNoSaysItCouldntDraft() async {
        let fake = FakeRoutes(), host = FakeHost(routes: fake), person = Person(saying: false)
        host.asksOverLimit = person.ask
        fake.answer("/api/ai/draft-reply") { _ in throw FakeRoutes.overLimit }
        let reply = Composer(Self.replySeed, host: host)
        defer { reply.close(force: true) }

        await reply.draftReply()

        XCTAssertEqual(person.asked, [Self.atTheLimit])
        XCTAssertEqual(reply.notice, "Couldn’t draft: today's AI spending has reached the daily limit")
        XCTAssertEqual(fake.calls(to: "/api/ai/draft-reply").map(\.overLimit), [false])
    }

    func testAQuickReplysDraftNeverAsks() async {
        let fake = FakeRoutes(), host = FakeHost(routes: fake), person = Person(saying: true)
        host.asksOverLimit = person.ask
        fake.answer("/api/ai/draft-reply") { _ in throw FakeRoutes.overLimit }
        let reply = Composer(Self.replySeed, host: host)
        defer { reply.close(force: true) }
        reply.startWith("Thanks!")

        await reply.draftReply(tone: .brief, instruction: "Say thanks. One or two sentences.", unless: reply.body,
                               quiet: true)

        XCTAssertTrue(person.asked.isEmpty, "a quick reply's draft is the app's own")
        XCTAssertTrue(reply.body.hasPrefix("Thanks!"), "the line stays")
        XCTAssertNil(reply.notice)
        XCTAssertEqual(fake.calls(to: "/api/ai/draft-reply").map(\.overLimit), [false])
    }

    private static var replySeed: ComposeSeed {
        var seed = ComposeSeed()
        seed.mode = .reply
        seed.to = ["bo@example.test"]
        seed.subject = "Re: Plans"
        seed.origKey = 7
        return seed
    }

    func testASendersSummaryOnTheMailboxsWireAsksToo() async throws {
        let person = Person(saying: true)
        let summary = API.SenderSummary(addr: "grace@example.test", ok: true, open: "", summary: "Writes about the board.",
                                        threads: 3, topics: ["Q3 figures"])
        let refusal = CoreAnswer(status: 402, contentType: "application/problem+json", headers: [], body: Data(#"""
            {"type": "/problems/over-limit", "status": 402, "over_limit": true,
             "spent_micros": 15250000, "limit_micros": 15000000}
            """#.utf8))
        let answer = CoreAnswer(status: 200, contentType: "application/json", headers: [],
                                body: try JSONEncoder().encode(summary))
        let wire = Wire(send: { _, _, _ in refusal }, sendOverLimit: { _, _, _ in answer }, asksOverLimit: person.ask)

        let said = try await wire.post(API.Post.ContactSummary.self, API.SenderSummaryBody(addr: "grace@example.test"),
                                       asking: wire.asksOverLimit)

        XCTAssertEqual(said, summary)
        XCTAssertEqual(person.asked, [Self.atTheLimit])
    }

    // MARK: The question

    func testTheQuestionSaysWhatWasSpentAndTheLimitInThePagesWords() {
        XCTAssertEqual(OverLimit(FakeRoutes.overLimit), Self.atTheLimit)
        XCTAssertEqual(Self.atTheLimit.question,
                       "MailMind has spent $15.25 on AI today, and its daily limit is $15.00. Go over the limit for this?")
        XCTAssertEqual(OverLimit.title, "Today’s AI limit is reached")
        XCTAssertEqual([OverLimit.goOver, OverLimit.notNow], ["Go over the limit", "Not now"])
        XCTAssertNil(OverLimit(FakeRoutes.problem(402, "Payment Required")), "a 402 that isn't the limit")
        XCTAssertNil(OverLimit(Problem(CoreAnswer(status: 409, contentType: "application/problem+json", headers: [],
                                                  body: Data(#"{"over_limit": true}"#.utf8)))))
        XCTAssertNil(OverLimit(CancellationError()))
    }

    func testCallsThatReachTheLimitWhileTheQuestionIsUpShareItsAnswer() async {
        var shown = 0
        let question = OverLimitQuestion { _ in
            shown += 1
            try? await Task.sleep(for: .milliseconds(200))
            return true
        }

        let first = Task { await question.ask(Self.atTheLimit) }
        let second = Task { await question.ask(Self.atTheLimit) }
        let (one, two) = (await first.value, await second.value)

        XCTAssertTrue(one && two)
        XCTAssertEqual(shown, 1, "one question for both")
        _ = await question.ask(Self.atTheLimit)
        XCTAssertEqual(shown, 2, "once answered, the next call asks again")
    }
}
