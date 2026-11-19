import AppKit
import SwiftUI
import XCTest

/// What the desk's screens in Meadow's look work out before they draw: the brief's greeting by the light and the
/// hour, its date line and when it was written, the mail its words name as links, its tallies and what is worth a
/// read; the paper trail's month against the one before, its bars and its facts; and the gate's queue, what it puts
/// off and remembers deciding, how it reads a visitor's letter, and the keys it takes.
@MainActor
final class MeadowDeskTests: XCTestCase {
    private var english: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }

    // MARK: The brief's greeting

    func testTheGreetingFollowsTheHourAndTheLight() {
        XCTAssertEqual(BriefWords.greeting(hour: 3, light: .night), "Working late")
        XCTAssertEqual(BriefWords.greeting(hour: 9, light: .night), "Good morning")
        XCTAssertEqual(BriefWords.greeting(hour: 14, light: .dusk), "Good afternoon")
        // Dusk's light turns a late afternoon into the evening; Day's keeps it the afternoon.
        XCTAssertEqual(BriefWords.greeting(hour: 17, light: .day), "Good afternoon")
        XCTAssertEqual(BriefWords.greeting(hour: 17, light: .dusk), "Good evening")
        XCTAssertEqual(BriefWords.greeting(hour: 17, light: .night), "Good evening")
        XCTAssertEqual(BriefWords.greeting(hour: 19, light: .day), "Good evening")
        // Night's light turns a late evening into working late.
        XCTAssertEqual(BriefWords.greeting(hour: 22, light: .day), "Good evening")
        XCTAssertEqual(BriefWords.greeting(hour: 22, light: .dusk), "Good evening")
        XCTAssertEqual(BriefWords.greeting(hour: 22, light: .night), "Working late")
    }

    func testTheGreetingNamesThePersonWhenTheMacKnowsTheirName() {
        XCTAssertEqual(BriefWords.greeting(hour: 15, light: .day, name: "Ada"), "Good afternoon, Ada.")
        XCTAssertEqual(BriefWords.greeting(hour: 23, light: .night, name: "Ada"), "Working late, Ada.")
        XCTAssertEqual(BriefWords.greeting(hour: 15, light: .day, name: nil), "Good afternoon.")
        XCTAssertEqual(BriefWords.greeting(hour: 15, light: .day, name: ""), "Good afternoon.")
        XCTAssertEqual(BriefWords.firstName("Ada King Lovelace"), "Ada")
        XCTAssertEqual(BriefWords.firstName("  Grace  "), "Grace")
        XCTAssertNil(BriefWords.firstName("   "))
    }

    func testTheBriefReadsTheTimeAsTheClockTurnsNotWhenItWasMade() throws {
        // Made in the morning, the brief is drawn again as the clock turns in the evening: it reads the evening.
        let evening = try XCTUnwrap(english.date(from: DateComponents(year: 2026, month: 11, day: 14, hour: 21)))
        var read: [Date] = []
        let calendar = english
        let clock = BriefClock(fixed: nil, schedule: ExplicitTimelineSchedule([evening])) { now in
            let _ = read.append(now)
            Text(verbatim: BriefWords.greeting(hour: calendar.component(.hour, from: now), light: .day))
        }
        try host(clock)
        XCTAssertEqual(read.last, evening)

        // A test or a picture gives its own time, which stays.
        let morning = try XCTUnwrap(english.date(from: DateComponents(year: 2026, month: 11, day: 14, hour: 9)))
        read = []
        try host(BriefClock(fixed: morning) { now in
            let _ = read.append(now)
            Text(verbatim: "")
        })
        XCTAssertEqual(read, [morning])

        // On its own, the brief's clock turns each minute: it shows this minute, whenever the brief was made.
        read = []
        try host(BriefClock(fixed: nil) { now in
            let _ = read.append(now)
            Text(verbatim: "")
        })
        let shown = try XCTUnwrap(read.last)
        XCTAssertLessThanOrEqual(abs(shown.timeIntervalSinceNow), 61)
    }

    /// Lay `view` out in a window, as the screen would.
    private func host(_ view: some View) throws {
        let host = NSHostingView(rootView: view.frame(width: 200, height: 40))
        host.frame = CGRect(x: 0, y: 0, width: 200, height: 40)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        window.layoutIfNeeded()
        host.layoutSubtreeIfNeeded()
        let bitmap = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: bitmap)
        window.contentView = nil
        window.close()
    }

    func testTheDateLineAndWhenTheBriefWasWritten() throws {
        let sunday = try XCTUnwrap(english.date(from: DateComponents(year: 2026, month: 11, day: 15, hour: 14)))
        XCTAssertEqual(BriefWords.dateLine(sunday, calendar: english, locale: Locale(identifier: "en_GB")),
                       "Sunday · 15 November")
        let us = Locale(identifier: "en_US")
        // The formatter puts a narrow no-break space before "am".
        func written(_ brief: API.Brief) -> String? {
            BriefWords.written(brief, calendar: english, locale: us)?.replacingOccurrences(of: "\u{202F}", with: " ")
        }
        var brief = DeskFixture.brief
        brief.generated = "2026-11-15T08:10:00Z"
        XCTAssertEqual(written(brief), "written at 8:10 am")
        brief.stale = true
        XCTAssertEqual(written(brief), "written at 8:10 am · mail has changed since")
        XCTAssertNil(BriefWords.written(nil, calendar: english, locale: us))
    }

    // MARK: The brief's links

    private func person(_ key: Int, _ name: String, subject: String, importance: Int) -> API.DigestItem {
        let address = name.lowercased().replacingOccurrences(of: " ", with: ".") + "@example.test"
        return DeskFixture.item(key, from: address, name: name, subject: subject, importance: importance)
    }

    func testTheBriefLinksEachMessageWhereItIsFirstNamedToTheEndOfItsClause() {
        let items = [
            person(101, "Ruth Lane", subject: "Office lease", importance: 5),
            person(102, "Grace Hopper", subject: "Re: Q3 figures", importance: 4),
            person(103, "Alan Turing", subject: "The river walk", importance: 4),
            person(104, "Alan Kay", subject: "Lunch", importance: 1),
        ]
        let text = "Three things need you: Ruth needs the signed lease back by Monday, Grace Hopper wants the "
            + "regional split, and Alan is waiting to hear about Saturday. Ruth wrote again."
        let runs = BriefWords.runs(text, items)
        XCTAssertEqual(runs.map(\.text), [
            "Three things need you: ", "Ruth needs the signed lease back by Monday", ", ",
            "Grace Hopper wants the regional split",
            // Two senders are called Alan, so "Alan" alone names neither; Ruth is linked once, where first named.
            ", and Alan is waiting to hear about Saturday. Ruth wrote again.",
        ])
        XCTAssertEqual(runs.map { $0.names?.key }, [nil, 101, nil, 102, nil])
    }

    func testABriefLinkStopsBeforeAnotherClauseAndAfterItsWords() {
        let marie = person(105, "Marie Curie", subject: "Photos from the pottery class", importance: 2)
        XCTAssertEqual(BriefWords.runs("Marie sent photos and a date for the next class.", [marie]).map(\.text),
                       ["Marie sent photos", " and a date for the next class."])
        // A subject names its message too, before the sender does.
        XCTAssertEqual(BriefWords.runs("Your photos from the pottery class arrived.", [marie]).map(\.text),
                       ["Your ", "photos from the pottery class arrived", "."])
        XCTAssertEqual(BriefWords.runs("Marie wrote a long note about the kiln the glaze and the next three classes",
                                       [marie], words: 5).map(\.text),
                       ["Marie wrote a long note", " about the kiln the glaze and the next three classes"])
    }

    func testABriefLinkMatchesWholeWordsInAnyCaseAndAccent() {
        let cafe = person(106, "Café Lumen", subject: "Your order", importance: 1)
        XCTAssertEqual(BriefWords.runs("A receipt from cafe lumen came in.", [cafe]).map(\.text),
                       ["A receipt from ", "cafe lumen came in", "."])
        let ann = person(107, "Ann Lee", subject: "Notes", importance: 1)
        XCTAssertEqual(BriefWords.runs("The Annual report is out.", [ann]).map(\.text), ["The Annual report is out."])
        // An address is never a name to look for.
        let robot = DeskFixture.item(108, from: "noreply@example.test", name: "noreply@example.test", subject: "Hi",
                                     importance: 1)
        XCTAssertEqual(BriefWords.runs("Mail from noreply@example.test.", [robot]).count, 1)
    }

    // MARK: The brief's tallies

    func testTheTalliesCountWhatThereIsInTheMockupsOrderAndEndWithTheWeek() {
        typealias Tally = BriefWords.Tally
        XCTAssertEqual(BriefWords.tallies(needs: 3, waiting: 1, replyLater: 0, worthARead: 2, canWait: 5,
                                          commitments: 1),
                       [Tally(count: 3, words: "need you", goes: .needs, sun: true),
                        Tally(count: 1, words: "waiting", goes: .waiting),
                        Tally(count: 2, words: "worth a read", goes: .worthARead),
                        Tally(count: 5, words: "can wait", goes: .canWait),
                        Tally(count: 1, words: "commitment", goes: .commitments),
                        Tally(words: "Your week", goes: .week)])
        XCTAssertEqual(BriefWords.tallies(needs: 1, waiting: 0, replyLater: 2, worthARead: 0, canWait: 0,
                                          commitments: 3),
                       [Tally(count: 1, words: "needs you", goes: .needs, sun: true),
                        Tally(count: 2, words: "to reply later", goes: .replyLater),
                        Tally(count: 3, words: "commitments", goes: .commitments),
                        Tally(words: "Your week", goes: .week)])
        XCTAssertEqual(BriefWords.tallies(needs: 0, waiting: 0, replyLater: 0, worthARead: 0, canWait: 0,
                                          commitments: 0),
                       [Tally(words: "Your week", goes: .week)])
    }

    func testWorthAReadIsUnreadMailThatAsksNothingTheMostImportantFirst() {
        var seen = DeskFixture.item(201, from: "a@example.test", name: "A", subject: "Seen", needs: false,
                                    importance: 5)
        seen.seen = true
        let items = [
            DeskFixture.item(202, from: "b@example.test", name: "B", subject: "Needs", needs: true, importance: 5),
            DeskFixture.item(203, from: "c@example.test", name: "C", subject: "Safe", needs: false, importance: 4,
                             safe: true),
            seen,
            DeskFixture.item(204, from: "d@example.test", name: "D", subject: "Essay", needs: false, importance: 2),
            DeskFixture.item(205, from: "e@example.test", name: "E", subject: "Ticket", needs: false, importance: 3),
            DeskFixture.item(206, from: "f@example.test", name: "F", subject: "Photos", needs: false, importance: 2),
        ]
        XCTAssertEqual(BriefWords.worthARead(items).map(\.key), [205, 206, 204])
    }

    // MARK: The paper trail

    private func receipt(_ date: String, _ subject: String, account: String = "ada@example.test",
                         folder: String = "INBOX") -> API.SearchHit {
        API.SearchHit(account: account, commitmentWorthy: false, date: date, flagged: false, folder: folder,
                      fromAddr: "shop@example.test", fromName: "Shop", hasAttach: false, isJunk: false, key: 1,
                      needsYou: false, replyWorthy: false, safeToFile: true, seen: true, sentTo: [], sentToMore: 0,
                      snippet: "", subject: subject,
                      uid: 1, when: date)
    }

    private var trail: [Ledger.Month] {
        Ledger.months([
            receipt("2026-11-13", "Your e-ticket — $98.50", folder: "Travel"),
            receipt("2026-11-11", "Invoice — $45.00", account: "work@example.test", folder: "Invoices"),
            receipt("2026-11-02", "Refund for a cancelled class — $20.00"),
            receipt("2026-10-28", "Your receipt — $18.20"),
            receipt("2026-10-03", "Your e-ticket — $250.20"),
            receipt("2026-09-21", "Your receipt — €30.00"),
            receipt("", "A receipt with no date — $5.00"),
        ])
    }

    func testAMonthsTotalIsSaidAgainstTheMonthBefore() throws {
        let us = Locale(identifier: "en_US")
        let november = try XCTUnwrap(Ledger.summary(trail, at: 0))
        XCTAssertEqual(november.total, 123.5, accuracy: 0.001)
        XCTAssertEqual([november.receipts, november.refunds], [2, 1])
        XCTAssertEqual(november.beforeMonth, "2026-10")
        XCTAssertEqual(Ledger.words(november, locale: us), "2 receipts · 1 refund · $144.90 less than October")
        // September's receipts are in euros, so October has nothing to be said against.
        XCTAssertEqual(Ledger.words(try XCTUnwrap(Ledger.summary(trail, at: 1)), locale: us), "2 receipts")
        // The month after September is mail with no date, which is no month before it.
        XCTAssertNil(try XCTUnwrap(Ledger.summary(trail, at: 2)).before)
        XCTAssertNil(Ledger.summary(trail, at: 9))

        let even = Ledger.months([receipt("2026-11-05", "$10.00"), receipt("2026-10-05", "$10.00")])
        XCTAssertEqual(Ledger.words(try XCTUnwrap(Ledger.summary(even, at: 0)), locale: us),
                       "1 receipt · the same as October")
        let more = Ledger.months([receipt("2026-11-05", "$30.00"), receipt("2026-10-05", "$10.00")])
        XCTAssertEqual(Ledger.words(try XCTUnwrap(Ledger.summary(more, at: 0)), locale: us),
                       "1 receipt · $20.00 more than October")
    }

    func testAMonthsBarsRunOldestFirstAndEndAtTheMonth() {
        let bars = Ledger.bars(trail, at: 0)
        XCTAssertEqual(bars.count, 3)
        XCTAssertEqual(bars[0], 0, accuracy: 0.001, "September's total is in euros")
        XCTAssertEqual(bars[1], 1, accuracy: 0.001)
        XCTAssertEqual(bars[2], 123.5 / 268.4, accuracy: 0.001)
        XCTAssertEqual(Ledger.bars(trail, at: 1, count: 1), [1])
    }

    func testAMonthsFactsByAccountAndByFolder() {
        let november = trail[0]
        XCTAssertEqual(Ledger.byAccount(november).map { "\($0.account) \($0.currency)\($0.value)" },
                       ["ada@example.test $78.5", "work@example.test $45.0"])
        XCTAssertEqual(Ledger.byFolder(november).map { "\($0.folder) \($0.count)" },
                       ["Travel 1", "Invoices 1", "INBOX 1"])
        XCTAssertEqual(Ledger.monthOnly("2026-10"), "October")
        XCTAssertEqual(Ledger.monthOnly(""), "Undated")
    }

    // MARK: The gate

    func testTheGatePutsASenderOffToTheBackAndRemembersWhatItDecided() async {
        let routes = DeskRoutes()
        routes.answer("GET", "/api/screener", DeskFixture.screener)
        routes.answer("POST", "/api/rules", API.RuleChanged(ok: true, reclassified: 0, rules: DeskFixture.rules,
                                                            token: "marie@example.test", trashed: false))
        let model = ScreenerModel(account: "ada@example.test", link: CoreLink(routes), notices: DeskNotices())
        await model.load()
        XCTAssertEqual(model.visitor?.fromAddr, "hello@studio.example.test")

        model.putOff("hello@studio.example.test")
        XCTAssertEqual(model.rows.map(\.fromAddr),
                       ["marie@example.test", "offers@deals.example.test", "hello@studio.example.test"])
        XCTAssertEqual(model.visitor?.fromAddr, "marie@example.test")
        model.chosen = "offers@deals.example.test"
        XCTAssertEqual(model.visitor?.fromAddr, "offers@deals.example.test")

        await model.decide("marie@example.test", .keep)
        XCTAssertEqual(model.recent.map(\.sender.fromAddr), ["marie@example.test"])
        XCTAssertEqual(model.recent.first?.verdict, .keep)
    }

    func testTheGateReadsAVisitorsLatestLetterOnceByASearchOfTheirAddress() async {
        let routes = DeskRoutes()
        routes.answer("GET", "/api/search", API.SearchAnswer.searchPage(API.SearchPage(
            generated: "2026-11-15T09:00:00Z", hasMore: false, items: [Fixture.hit(9, subject: "Hello")], offset: 0,
            ok: true, query: "", scope: "account")))
        let model = ScreenerModel(account: "ada@example.test", link: CoreLink(routes), notices: DeskNotices())
        await model.readLetter("hello@studio.example.test")
        await model.readLetter("hello@studio.example.test")

        let asked = routes.sent("GET", "/api/search")
        XCTAssertEqual(asked.count, 1)
        XCTAssertEqual(asked.first?.query, ["q": "from:\"hello@studio.example.test\"", "scope": "account",
                                            "account": "ada@example.test", "limit": "1", "offset": "0"])
        XCTAssertEqual(model.letters["hello@studio.example.test"]??.subject, "Hello")
        XCTAssertEqual(ScreenerModel.quoted(#"a"b\c"#), #""a\"b\\c""#)
    }

    func testTheGatesKeysWorkWheneverItHasTheKeyboardAndNeverFromAField() async throws {
        let routes = DeskRoutes()
        routes.answer("GET", "/api/screener", DeskFixture.screener)
        routes.answer("GET", "/api/search", API.SearchAnswer.searchPage(API.SearchPage(
            generated: "2026-11-15T09:00:00Z", hasMore: false, items: [], offset: 0, ok: true, query: "",
            scope: "account")))
        routes.answer("POST", "/api/rules", API.RuleChanged(ok: true, reclassified: 0, rules: DeskFixture.rules,
                                                            token: "hello@studio.example.test", trashed: false))
        let model = ScreenerModel(account: "ada@example.test", link: CoreLink(routes), notices: DeskNotices())
        await model.load()
        let host = NSHostingView(rootView: VStack(spacing: 0) {
            TextField("Search", text: .constant(""))
            ScreenerScreen(model: model)
        }
        .meadowLight(.day))
        host.frame = CGRect(x: 0, y: 0, width: 1000, height: 800)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        defer { window.contentView = nil }
        func settle() async throws {
            for _ in 0..<20 {
                host.layoutSubtreeIfNeeded()
                try await Task.sleep(for: .milliseconds(10))
            }
        }
        func type(_ key: String, _ code: UInt16) throws {
            window.sendEvent(try XCTUnwrap(NSEvent.keyEvent(
                with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: window.windowNumber,
                context: nil, characters: key, charactersIgnoringModifiers: key, isARepeat: false, keyCode: code)))
        }
        try await settle()

        // Whether or not its buttons can take the keyboard, the gate holds it as it shows.
        try type("y", 16)
        try await settle()
        XCTAssertEqual(routes.sent("POST", "/api/rules").first?["token"], .string("hello@studio.example.test"))
        XCTAssertEqual(routes.sent("POST", "/api/rules").first?["verdict"], .string("keep"))

        // A field with the keyboard keeps what is typed in it.
        let field = try XCTUnwrap(Self.views(in: host).first { $0 is NSTextField })
        window.makeFirstResponder(field)
        try type("n", 45)
        try await settle()
        XCTAssertEqual(routes.sent("POST", "/api/rules").count, 1, "an n typed in a field decides no one")
    }

    /// Under a layout whose letters aren't Latin (Arabic's characters on the US key codes, as macOS hands them over),
    /// the gate's Y and N are the keys they stand on: غ lets the visitor in. A field still gets the Arabic typed in it,
    /// and decides no one.
    func testUnderANonLatinLayoutTheGatesKeysAreTheOnesTheyStandOn() async throws {
        let close = try KeyLayouts.arabic()
        defer { close() }
        let routes = DeskRoutes()
        routes.answer("GET", "/api/screener", DeskFixture.screener)
        routes.answer("GET", "/api/search", API.SearchAnswer.searchPage(API.SearchPage(
            generated: "2026-11-15T09:00:00Z", hasMore: false, items: [], offset: 0, ok: true, query: "",
            scope: "account")))
        routes.answer("POST", "/api/rules", API.RuleChanged(ok: true, reclassified: 0, rules: DeskFixture.rules,
                                                            token: "hello@studio.example.test", trashed: false))
        let model = ScreenerModel(account: "ada@example.test", link: CoreLink(routes), notices: DeskNotices())
        await model.load()
        let host = NSHostingView(rootView: VStack(spacing: 0) {
            TextField("Search", text: .constant(""))
            ScreenerScreen(model: model)
        }
        .meadowLight(.day))
        host.frame = CGRect(x: 0, y: 0, width: 1000, height: 800)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        defer { window.contentView = nil }
        func settle() async throws {
            for _ in 0..<20 {
                host.layoutSubtreeIfNeeded()
                try await Task.sleep(for: .milliseconds(10))
            }
        }
        try await settle()

        // The gate holds the keyboard as it shows: غ is Y.
        window.sendEvent(try KeyLayouts.event("غ", 16, in: window))
        try await settle()
        XCTAssertEqual(routes.sent("POST", "/api/rules").first?["token"], .string("hello@studio.example.test"))
        XCTAssertEqual(routes.sent("POST", "/api/rules").first?["verdict"], .string("keep"))

        // A field with the keyboard keeps what is typed in it.
        let field = try XCTUnwrap(Self.views(in: host).first { $0 is NSTextField })
        window.makeFirstResponder(field)
        window.sendEvent(try KeyLayouts.event("ر", 45, in: window))
        try await settle()
        XCTAssertEqual((window.firstResponder as? NSTextView)?.string, "ر", "the field gets the Arabic typed")
        XCTAssertEqual(routes.sent("POST", "/api/rules").count, 1, "a ر typed in a field decides no one")
    }

    private static func views(in view: NSView) -> [NSView] {
        [view] + view.subviews.flatMap(views(in:))
    }
}
