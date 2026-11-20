import Foundation
import XCTest

/// What the desk works out with no view and no server: how it words counts, days and refusals, how a suggestion's
/// time goes into a form and back, and a calendar file's name.
@MainActor
final class DeskWordsTests: XCTestCase {
    private let utc = TimeZone(identifier: "UTC")!
    private let paris = TimeZone(identifier: "Europe/Paris")!

    private var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = paris
        return calendar
    }

    func testCountsAndGreetings() {
        XCTAssertEqual(countNoun(1, "thread", "threads"), "1 thread")
        XCTAssertEqual(countNoun(3, "thread", "threads"), "3 threads")
        XCTAssertEqual(BriefWords.greeting(hour: 3), "Working late")
        XCTAssertEqual(BriefWords.greeting(hour: 9), "Good morning")
        XCTAssertEqual(BriefWords.greeting(hour: 14), "Good afternoon")
        XCTAssertEqual(BriefWords.greeting(hour: 21), "Good evening")
        XCTAssertEqual(BriefWords.needsNote(0), "Nothing needs you")
        XCTAssertEqual(BriefWords.needsNote(1), "1 needs you")
        XCTAssertEqual(BriefWords.needsNote(4), "4 need you")
    }

    /// The line over the brief's greeting says how the mail stands, with no count it doesn't know yet.
    func testTheBriefsLineSaysHowTheMailStands() {
        let english = Locale(identifier: "en_US")
        XCTAssertNil(BriefWords.update(unread: nil, needs: nil, locale: english), "the date, until a count is read")
        XCTAssertNil(BriefWords.update(unread: nil, needs: 0, locale: english), "nothing said of a count unread")
        XCTAssertEqual(BriefWords.update(unread: 0, needs: nil, locale: english), "Nothing unread")
        XCTAssertEqual(BriefWords.update(unread: 0, needs: 0, locale: english), "Nothing unread")
        XCTAssertEqual(BriefWords.update(unread: 1, needs: 0, locale: english), "1 unread")
        XCTAssertEqual(BriefWords.update(unread: 5, needs: nil, locale: english), "5 unread")
        XCTAssertEqual(BriefWords.update(unread: 0, needs: 1, locale: english), "1 needs you")
        XCTAssertEqual(BriefWords.update(unread: nil, needs: 3, locale: english), "3 need you")
        XCTAssertEqual(BriefWords.update(unread: 5, needs: 2, locale: english), "2 need you · 5 unread")
        XCTAssertEqual(BriefWords.update(unread: 1204, needs: 1, locale: english), "1 needs you · 1,204 unread")
    }

    func testWhatNeedsYouIsTheMostImportantThenTheNewest() {
        let items = [
            DeskFixture.item(110, from: "a@example.test", name: "A", subject: "a", importance: 3),
            DeskFixture.item(111, from: "b@example.test", name: "B", subject: "b", importance: 5),
            DeskFixture.item(112, from: "c@example.test", name: "C", subject: "c", importance: 3),
            DeskFixture.item(113, from: "d@example.test", name: "D", subject: "d", needs: false, importance: 5),
        ]
        XCTAssertEqual(BriefWords.needsYou(items).map(\.key), [111, 112, 110])
    }

    /// A conversation needs you once, by its weightiest message, as the page's brief lists it; another account's of
    /// the same id is another. Each message still counts where a list shows messages, not conversations.
    func testAConversationNeedsYouOnce() {
        let bea = "bea@example.test"
        let items = [
            DeskFixture.item(110, from: "a@example.test", name: "A", subject: "a", importance: 3, thread: "t1"),
            DeskFixture.item(111, from: "a@example.test", name: "A", subject: "Re: a", importance: 5, thread: "t1"),
            DeskFixture.item(112, from: "b@example.test", name: "B", subject: "b", importance: 3, account: bea, thread: "t1"),
            DeskFixture.item(113, from: "c@example.test", name: "C", subject: "c", importance: 3),
            DeskFixture.item(114, from: "c@example.test", name: "C", subject: "c again", importance: 3),
        ]
        XCTAssertEqual(BriefWords.needsYou(items).map(\.key), [111, 114, 113, 112])
        XCTAssertEqual(BriefWords.everyNeed(items).map(\.key), [111, 114, 113, 112, 110])
    }

    func testATopicNamesOnlyTheMailStillHeld() {
        let items = DeskFixture.digest().items
        let copies = [API.Copy(folder: "INBOX", key: 104, uid: 4), API.Copy(folder: "INBOX", key: 999, uid: 99),
                      API.Copy(folder: "INBOX", key: 101, uid: 1)]
        XCTAssertEqual(BriefWords.topicItems(copies, items).map(\.key), [104, 101])
    }

    func testWaitsNamesAndDueDays() {
        let now = Date(timeIntervalSince1970: 1_794_739_600)
        XCTAssertEqual(BriefWords.waitAge(sent: 1_794_739_600 - 3600, now: now), "today")
        XCTAssertEqual(BriefWords.waitAge(sent: 1_794_739_600 - 86400 - 60, now: now), "1 day")
        XCTAssertEqual(BriefWords.waitAge(sent: 1_794_739_600 - 4 * 86400, now: now), "4 days")
        XCTAssertEqual(BriefWords.nameIn("\"Ruth Lane\" <ruth@example.test>"), "Ruth Lane")
        XCTAssertEqual(BriefWords.nameIn("<ruth@example.test>"), "ruth@example.test")
        XCTAssertEqual(BriefWords.nameIn(nil), "")

        let late = BriefWords.due("2026-11-14", today: "2026-11-15")
        XCTAssertTrue(late.late)
        XCTAssertTrue(late.words.hasSuffix("· overdue"), late.words)
        let today = BriefWords.due("2026-11-15", today: "2026-11-15")
        XCTAssertFalse(today.late)
        XCTAssertTrue(today.words.hasSuffix("· today"), today.words)
        XCTAssertEqual(BriefWords.due(nil, today: "2026-11-15").words, "")
        XCTAssertEqual(BriefWords.localDay(Date(timeIntervalSince1970: 1_794_785_400), zone: paris), "2026-11-16")
    }

    /// See all opens Waiting, which holds what others wait on you for as well as what waits on them: it says how many
    /// the Waiting it opens holds, as the tally does, not how many the card beside it lists.
    func testSeeAllUnderWhoYoureWaitingOnSaysHowManyWaitingHolds() {
        XCTAssertNil(BriefWords.seeAllWaiting(listed: 3, held: 5), "the card shows all three")
        XCTAssertEqual(BriefWords.seeAllWaiting(listed: 4, held: 6), "See all 6")
        XCTAssertEqual(BriefWords.seeAllWaiting(listed: 4, held: nil), "See all 4", "before Waiting's count is read")
        XCTAssertEqual(BriefWords.seeAllWaiting(listed: 5, held: 2), "See all 2",
                       "beside one account's Inbox, the card lists every account's but See all opens that one's")
    }

    func testAReviewSaysWhatWeekItLookedBackOn() {
        let now = Date(timeIntervalSince1970: 1_794_739_600) // Sun 15 Nov 2026, 11:46 in Paris
        XCTAssertEqual(BriefWords.reviewRange(generated: "2026-11-15T07:00:00Z", now: now, calendar: calendar),
                       "last 7 days")
        XCTAssertEqual(BriefWords.reviewRange(generated: "2026-11-09T07:00:00Z", now: now, calendar: calendar),
                       "the 7 days to Monday")
    }

    func testASuggestedTimeGoesIntoAFormAndBack() {
        // A day stays that day here.
        let day = SuggestedTime.field("2026-11-20", allDay: true, in: paris)
        XCTAssertEqual(day.map { SuggestedTime.sent($0, allDay: true, in: paris) }, "2026-11-20")
        // A time named in a zone is that instant.
        let instant = SuggestedTime.field("2026-11-20T12:30:00Z", allDay: false, in: paris)
        XCTAssertEqual(instant, Date(timeIntervalSince1970: 1_795_177_800))
        XCTAssertEqual(instant.map { SuggestedTime.sent($0, allDay: false, in: paris) }, "2026-11-20T12:30:00.000Z")
        // A time named in no zone is its clock reading here.
        let clock = SuggestedTime.field("2026-11-20T12:30", allDay: false, in: paris)
        XCTAssertEqual(clock, Date(timeIntervalSince1970: 1_795_174_200))
        XCTAssertNil(SuggestedTime.field("", allDay: false, in: paris))
        XCTAssertNil(SuggestedTime.field(nil, allDay: true, in: paris))
    }

    func testWhenAnEventIs() {
        let now = Date(timeIntervalSince1970: 1_794_739_600) // Sun 15 Nov 2026, 11:46 in Paris
        let english = Locale(identifier: "en_US")
        func plain(_ text: String) -> String { text.replacingOccurrences(of: "\u{202F}", with: " ") }
        let later = EventWhen.short(start: "2026-11-15T15:00:00Z", allDay: false, now: now, calendar: calendar,
                                    locale: english)
        XCTAssertEqual(plain(later.big), "4:00 pm")
        XCTAssertEqual(later.small, "today")
        let tuesday = EventWhen.short(start: "2026-11-18T10:00:00Z", allDay: false, now: now, calendar: calendar,
                                      locale: english)
        XCTAssertEqual(tuesday.big, "Wed")
        XCTAssertEqual(plain(tuesday.small), "11:00 am")
        let trip = EventWhen.short(start: "2026-11-26", allDay: true, now: now, calendar: calendar, locale: english)
        XCTAssertEqual(trip.small, "all day")
        XCTAssertTrue(trip.big.contains("26"), trip.big)
        XCTAssertEqual(EventWhen.short(start: "2026-11-15", allDay: true, now: now, calendar: calendar, locale: english).big,
                       "Today")
        XCTAssertEqual(plain(EventWhen.long(start: "2026-11-20T14:30:00Z", allDay: false, calendar: calendar,
                                            locale: english)), "Fri, Nov 20, 3:30 pm")
        XCTAssertEqual(EventWhen.short(start: "soon", allDay: false, now: now, calendar: calendar).big, "soon")
    }

    /// An event's start as the core serves it, on the person's wall clock with no zone: Today & next's and Your Week's,
    /// copied from the served app's answers (`tests/golden/http_get/upcoming_screener_and_contacts.json` and the
    /// review in `tests/golden/http_ai`). Its hour reads as that hour here, wherever here is, never as the day's
    /// midnight, and the meeting reminder comes for it.
    func testAnEventsWallClockStartReadsAsItsHourHere() throws {
        let upcoming = try JSONDecoder().decode(API.Upcoming.self, from: Data("""
            {"items": [{"account": "ada@example.test", "all_day": false, "end": "2026-11-03T14:00", "folder": "INBOX",
              "invite": true, "key": 6, "location": "Room 2", "organizer": "Noor Haddad", "recurrence": "",
              "start": "2026-11-03T13:00", "summary": "Design review",
              "thread_id": "rf:ada@example.test:9f2993fccfd8ca64", "uid": 6}], "ok": true}
            """.utf8))
        let meeting = try JSONDecoder().decode(API.ReviewMeeting.self, from: Data("""
            {"account": "ada@example.test", "all_day": false, "folder": "INBOX", "key": 6, "past": false,
             "start": "2026-11-03T13:00", "summary": "Design review",
             "thread_id": "rf:ada@example.test:9f2993fccfd8ca64", "uid": 6}
            """.utf8))
        let event = try XCTUnwrap(upcoming.items.first)
        let english = Locale(identifier: "en_US")
        func plain(_ text: String) -> String { text.replacingOccurrences(of: "\u{202F}", with: " ") }
        for zone in ["Europe/Paris", "Asia/Tokyo", "America/Los_Angeles"] {
            var here = Calendar(identifier: .gregorian)
            here.timeZone = try XCTUnwrap(TimeZone(identifier: zone))
            let at = { (day: Int, hour: Int, minute: Int) in
                here.date(from: DateComponents(year: 2026, month: 11, day: day, hour: hour, minute: minute))!
            }
            let monday = EventWhen.short(start: event.start, allDay: event.allDay, now: at(2, 10, 0), calendar: here,
                                         locale: english)
            XCTAssertEqual(monday.big, "Tue", zone)
            XCTAssertEqual(plain(monday.small), "1:00 pm", zone)
            let today = EventWhen.short(start: event.start, allDay: event.allDay, now: at(3, 9, 0), calendar: here,
                                        locale: english)
            XCTAssertEqual(plain(today.big), "1:00 pm", "not 00:00: \(zone)")
            XCTAssertEqual(today.small, "today")
            XCTAssertEqual(plain(EventWhen.long(start: meeting.start, allDay: meeting.allDay, calendar: here,
                                                locale: english)), "Tue, Nov 3, 1:00 pm", "Your Week, \(zone)")
            let soon = Soon.next(upcoming.items, now: at(3, 12, 55), closed: [], calendar: here)
            XCTAssertEqual(soon?.title, "Design review", "the reminder five minutes before, \(zone)")
            XCTAssertEqual(soon?.when(now: at(3, 12, 55)), "in 5 min")
            XCTAssertEqual(soon?.end, at(3, 14, 0))
        }
    }

    func testRefusalsReadAsThePageReadsThem() {
        let off = Problem(CoreAnswer(status: 409, contentType: "application/problem+json", headers: [], body: Data(
            #"{"type": "/problems/ai-off", "title": "AI is off", "status": 409, "detail": "No key.", "ai_off": true}"#.utf8)))
        let limit = Problem(CoreAnswer(status: 402, contentType: "application/problem+json", headers: [], body: Data(
            #"{"type": "/problems/over-limit", "title": "Limit", "status": 402, "detail": "", "over_limit": true}"#.utf8)))
        XCTAssertTrue(DeskRefusal.isAIOff(off))
        XCTAssertFalse(DeskRefusal.isAIOff(limit))
        XCTAssertTrue(DeskRefusal.isOverLimit(limit))
        XCTAssertEqual(DeskRefusal.words(off, else: "fallback"), "No key.")
        XCTAssertEqual(DeskRefusal.words(limit, else: "fallback"), "fallback", "an empty detail says only the fallback")
        XCTAssertEqual(DeskRefusal.words(CancellationError(), else: "fallback"), "fallback")
        XCTAssertEqual(DeskRefusal.message(off), "No key.")
        XCTAssertEqual(DeskRefusal.message(CancellationError()), "")
        XCTAssertEqual(AttentionModel.couldnt("Couldn’t mute", ""), "Couldn’t mute")
        XCTAssertEqual(AttentionModel.couldnt("Couldn’t mute", "why"), "Couldn’t mute: why")
    }

    /// Later today says what it does once three hours on runs past midnight, from 21:00, when the server snoozes to
    /// the next morning, as the page's menu says it: the presets keep their order, so each digit picks the same one.
    func testLaterTodaySaysTomorrowMorningOnceItMeansIt() {
        XCTAssertEqual(Snoozing.words(.laterToday, at: Self.at(20, 59), calendar: Self.utc), "Later today")
        XCTAssertEqual(Snoozing.words(.laterToday, at: Self.at(0, 30), calendar: Self.utc), "Later today")
        XCTAssertEqual(Snoozing.words(.laterToday, at: Self.at(21), calendar: Self.utc), "Tomorrow morning")
        XCTAssertEqual(Snoozing.words(.laterToday, at: Self.at(23, 30), calendar: Self.utc), "Tomorrow morning")
        XCTAssertEqual(Snoozing.words(.tomorrow, at: Self.at(22), calendar: Self.utc), "Tomorrow 8am")
    }

    /// A calendar on UTC, and 20 November 2026 at `hour`:`minute` in it.
    private static var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }

    private static func at(_ hour: Int, _ minute: Int = 0) -> Date {
        utc.date(from: DateComponents(year: 2026, month: 11, day: 20, hour: hour, minute: minute))!
    }

    /// Your Week's lists say how many they show of the week's whole, as its tiles count it: at most eight of the
    /// thirteen that need you.
    func testYourWeeksListsSayHowManyTheyShowOfTheWhole() {
        XCTAssertEqual(ReviewBody.heading("Still needs you", shown: 8, of: 13), "Still needs you · 8 of 13")
        XCTAssertEqual(ReviewBody.heading("Still needs you", shown: 3, of: 3), "Still needs you · 3")
        XCTAssertEqual(ReviewBody.heading("Still needs you", shown: 0, of: 0), "Still needs you")
        XCTAssertEqual(ReviewBody.heading("You’re waiting on a reply", shown: 5, of: 9), "You’re waiting on a reply · 5 of 9")
        XCTAssertEqual(ReviewBody.heading("You’re waiting on a reply", shown: 2, of: 1), "You’re waiting on a reply · 2",
                       "never fewer than it shows")
    }

    func testTheVerbsWordsAndWhichTakeASecondPress() {
        XCTAssertEqual(PlanModel.label(.trash), "Delete")
        XCTAssertEqual(PlanModel.done(.star), "Updated")
        XCTAssertTrue(PlanModel.destructive(.trash))
        XCTAssertTrue(PlanModel.destructive(.junk))
        XCTAssertFalse(PlanModel.destructive(.archive))
        // The page's words, all in its sentence case.
        XCTAssertEqual(API.SnoozePreset.allCases.map { Snoozing.words($0, at: Self.at(12), calendar: Self.utc) },
                       ["Later today", "Tomorrow 8am", "This weekend", "Next week"])
        XCTAssertEqual(AttentionStack(folder: "__replylater__"), .replyLater)
        XCTAssertNil(AttentionStack(folder: "INBOX"))
        XCTAssertNil(AttentionStack(folder: nil))
        XCTAssertEqual(CurateModel.cleaned("mailto_sent"), "unsub email sent")
        XCTAssertEqual(CurateModel.cleaned("something new"), "")
        XCTAssertEqual(CurateModel.does(API.MailFilter(id: "f", q: "x", read: true, star: true)), "Star and mark read")
        XCTAssertEqual(InviteReplies.words(.tentative), "Tentative ?")
        XCTAssertEqual(BriefModel.filedWords(.archive, API.Filed(msg: "", ok: true, status: "queued", undoable: true)),
                       "Archived · once the server is back")
    }

    func testAnInviteNamesItsPeople() {
        XCTAssertEqual(InviteCard.attendees(DeskFixture.invite.attendees),
                       "3 attendees: Ada Lovelace, Alan Turing, Grace Hopper")
        let many = (1...10).map { API.Party(email: "p\($0)@example.test", name: nil) }
        XCTAssertEqual(InviteCard.attendees(many)?.hasSuffix("p8@example.test +2"), true)
        XCTAssertNil(InviteCard.attendees([API.Party(email: "", name: "")]))
        XCTAssertEqual(InviteCard.kind("CANCEL"), "Cancelled")
    }

    func testACalendarFileNameStaysInItsFolder() {
        XCTAssertEqual(CalendarFile.safeName("Lunch with Grace"), "Lunch with Grace.ics")
        XCTAssertEqual(CalendarFile.safeName("../../etc/passwd"), "-..-etc-passwd.ics")
        XCTAssertEqual(CalendarFile.safeName("a:b.ics"), "a-b.ics")
        XCTAssertEqual(CalendarFile.safeName("  . "), "event.ics")
        XCTAssertFalse(CalendarFile.safeName("../x").contains("/"))
    }

    func testACustomSnoozeStartsTomorrowAtNine() {
        let now = Date(timeIntervalSince1970: 1_794_739_600)
        let start = SnoozeChooser.tomorrowMorning(from: now, calendar: calendar)
        let parts = calendar.dateComponents([.day, .hour, .minute], from: start)
        XCTAssertEqual(parts.day, 16)
        XCTAssertEqual(parts.hour, 9)
        XCTAssertEqual(parts.minute, 0)
    }

    func testACopyNamedByARouteIsTheListsCopy() {
        let named = MailCopy.named(key: 101, folder: "INBOX", uid: 11, account: "ada@example.test")
        XCTAssertEqual(named, DeskFixture.ada)
        XCTAssertEqual(named.aCopy, API.ACopy(folder: "INBOX", key: 101, uid: 11))
        XCTAssertTrue(newUndoGroup().hasPrefix("ug-"))
        XCTAssertNotEqual(newUndoGroup(), newUndoGroup())
    }

    /// The page's own cases (features/assistant/prose.test.ts), so the two decide alike what costs a call.
    func testWhatReadsAsASentence() {
        for typed in [
            "invoices from last month", "emails from sarah about the budget", "show me unread mail from lin",
            "find the invoice from acme", "what did stripe send me", "where is my boarding pass?", "receipts since october",
            "mail about the quilt", "anything from lin yesterday", "flights last week", "the invoice lin sent me",
            "Show Me Unread Mail", "look for invoices from acme",
        ] {
            XCTAssertTrue(Prose.readsAsProse(typed), typed)
        }
    }

    func testWhatStaysALiteralSearch() {
        for typed in [
            "budget", "from lin", "quarterly budget review", "quarterly budget review meeting", "invoice 12345 acme",
            "bank of america", "lord of the rings", "the new york times", "thanks for lunch", "meeting with john",
            "do not reply", "list of attendees", "get well soon", "have a nice day", "find invoice acme",
            "annual report from", "from the desk", "from:lin last month", "mail from lin is:unread",
            "mail from lin -newsletter", "\"a phrase from lin\" budget", "this year's budget",
            "Rechnungen vom letzten Monat", "   ", Array(repeating: "mail from lin", count: 31).joined(separator: " "),
        ] {
            XCTAssertFalse(Prose.readsAsProse(typed), typed)
        }
    }
}
