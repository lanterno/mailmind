import Foundation
import XCTest

private typealias F = StatusFixtures

/// The status says what the page's status says, in its words, from what the routes answered.
final class StatusWordsTests: XCTestCase {
    private let now = F.at("2026-11-14T15:00:00Z")

    private func pill(_ digest: API.Digest?, _ health: [API.AccountStanding] = [], syncing: Bool = false,
                      reconciling: Bool = false) -> StatusWords.Pill
    {
        StatusWords.pill(digest: digest, health: health, syncing: syncing, reconciling: reconciling, now: now,
                         calendar: F.utc, locale: Locale(identifier: "en_US"))
    }

    func testThePillWaitsForTheDigest() {
        XCTAssertEqual(pill(nil), .init(dot: .none, text: "Connecting…"))
    }

    func testAnAccountThatCantConnectComesFirst() {
        let failing = F.standing("ada@example.test", ok: "2026-11-14T09:00:00Z", error: "LOGIN failed", kind: "AUTH")
        XCTAssertEqual(pill(F.digest(), [failing], syncing: true), .init(dot: .error, text: "Ada can’t connect"),
                       "by its alias, ahead of a sync running")
        let both = [failing, F.standing("news@example.test", error: "refused", kind: "NET")]
        XCTAssertEqual(pill(F.digest(), both), .init(dot: .error, text: "2 accounts can’t connect"))
        XCTAssertEqual(pill(F.digest(status: ["news@example.test": "error"])),
                       .init(dot: .error, text: "news can’t connect"), "the digest's word counts where it gives one")
    }

    func testWhatIsRunningComesNext() {
        XCTAssertEqual(pill(F.digest(), reconciling: true), .init(dot: .busy, text: "Checking against the server…"))
        XCTAssertEqual(pill(F.digest(), syncing: true), .init(dot: .busy, text: "Syncing…"))
        XCTAssertEqual(pill(F.digest(running: true)), .init(dot: .busy, text: "Syncing…"))
        XCTAssertEqual(pill(F.digest(status: ["ada@example.test": "syncing"])), .init(dot: .busy, text: "Syncing…"))
    }

    func testAFirstSyncSaysHowFarItHasGotAheadOfWhatElseRuns() {
        var digest = F.digest(status: ["ada@example.test": "ok", "news@example.test": "ok"])
        digest.status["ada@example.test"]?.done = 2300
        digest.status["ada@example.test"]?.total = 41000
        XCTAssertEqual(pill(digest, syncing: true), .init(dot: .busy, text: "Reading your inbox: 2,300 of 41,000"))
        digest.status["news@example.test"]?.done = 500
        digest.status["news@example.test"]?.total = 900
        XCTAssertEqual(pill(digest).text, "Reading your inboxes: 2,800 of 41,900")
        let failing = F.standing("news@example.test", error: "refused", kind: "NET")
        XCTAssertEqual(pill(F.digest(), [failing]).text, "news can’t connect", "what can't connect still comes first")
        digest.status["ada@example.test"] = API.AccountSync(health: "ok", msg: "+0")
        digest.status["news@example.test"] = API.AccountSync(health: "ok", msg: "+0")
        XCTAssertEqual(pill(digest).text, "Not synced yet", "read whole, it says nothing of it")
    }

    func testThenWhenMailWasLastSynced() {
        XCTAssertEqual(pill(F.digest(profile: "read-only")), .init(dot: .none, text: "Read only"),
                       "as the rest of the app spells it")
        XCTAssertEqual(pill(F.digest(last: "2026-11-14T10:42:00Z")).text, "Synced 10:42\u{202F}AM", "today, by its time")
        XCTAssertEqual(pill(F.digest(last: "2026-11-03T10:42:00Z")).text, "Synced Nov 3", "before today, by its day")
        XCTAssertEqual(pill(F.digest(), [F.standing("ada@example.test", ok: "2026-11-14 14:05:09")]).text,
                       "Synced 2:05\u{202F}PM", "with no word from the digest, the latest sync an account recorded")
        XCTAssertEqual(pill(F.digest()), .init(dot: .none, text: "Not synced yet"))
    }

    /// A sync that failed for every account, once: the digest's round says when it ended, but nothing synced then. The
    /// pill says so at once in the sun's quiet words, never Synced in moss; the louder words wait for the digest's
    /// error. Once the accounts sync again it says when, by the syncs that worked.
    func testASyncThatFailedSaysItIsTryingAgainAndNeverSynced() {
        var failed = F.digest(status: ["ada@example.test": "ok"], last: "2026-11-14T14:59:00Z")
        failed.status["ada@example.test"]?.tryingAgain = true
        failed.status["news@example.test"] = API.AccountSync(health: "", msg: "", tryingAgain: true)
        let worked = [F.standing("ada@example.test", ok: "2026-11-14T14:05:00Z"),
                      F.standing("news@example.test", ok: "2026-11-14T13:00:00Z")]
        let trying = StatusWords.Pill(dot: .busy, text: "Couldn’t reach the server · trying again")
        XCTAssertEqual(pill(failed, worked), trying)
        XCTAssertEqual(pill(failed), trying, "before the accounts' records are read too")
        XCTAssertEqual(pill(failed, worked, syncing: true).text, "Syncing…", "what runs comes first")
        XCTAssertEqual(StatusWords.lastSynced(digest: failed, health: worked), F.at("2026-11-14T14:05:00Z"),
                       "the last sync that worked, not the round that failed")
        XCTAssertNil(StatusWords.lastSynced(digest: failed, health: []), "a round that failed is no sync")

        var one = F.digest(status: ["ada@example.test": "ok"], last: "2026-11-14T14:59:00Z")
        one.status["news@example.test"] = API.AccountSync(health: "", msg: "", tryingAgain: true)
        XCTAssertEqual(pill(one, worked), trying, "one account trying again is enough")

        let again = F.digest(status: ["ada@example.test": "ok", "news@example.test": "ok"],
                             last: "2026-11-14T14:59:00Z")
        let synced = [F.standing("ada@example.test", ok: "2026-11-14T14:59:00Z"),
                      F.standing("news@example.test", ok: "2026-11-14T14:58:00Z")]
        XCTAssertEqual(pill(again, synced), .init(dot: .ok, text: "Synced 2:59\u{202F}PM"))
    }

    func testHowLongAgoInTheFewestWords() {
        XCTAssertEqual(StatusWords.ago("2026-11-14T14:59:00Z", now: now), "just now")
        XCTAssertEqual(StatusWords.ago("2026-11-14T14:55:00Z", now: now), "5m ago")
        XCTAssertEqual(StatusWords.ago("2026-11-14T12:00:00Z", now: now), "3h ago")
        XCTAssertEqual(StatusWords.ago("2026-11-10T15:00:00Z", now: now), "4d ago")
        XCTAssertEqual(StatusWords.ago("not a time", now: now), "")
    }

    func testMoneyIsRoundedUpToTheCent() {
        XCTAssertEqual(StatusWords.dollars(1), "$0.01")
        XCTAssertEqual(StatusWords.dollars(15_000_000), "$15.00")
        XCTAssertEqual(StatusWords.dollars(1_234_560_001), "$1,234.57")
    }

    func testTheLineOnAI() {
        XCTAssertEqual(StatusWords.ai(F.digest(engine: "none").ai).text, "AI is off · no Claude API key")
        let on = StatusWords.ai(F.digest(spent: 120_000).ai)
        XCTAssertEqual(on.text, "Claude · Claude Haiku 4.5 · $0.12 of $15.00 today")
        XCTAssertFalse(on.failing)
        let failing = StatusWords.ai(F.digest(spent: 0, lastOk: false, lastError: "overloaded").ai)
        XCTAssertEqual(failing.text, "Claude · Claude Haiku 4.5 · last call failed: overloaded",
                       "nothing spent today says nothing of money")
        XCTAssertTrue(failing.failing)
    }

    /// The line names the provider chosen, or why AI is off, and never Claude for another's.
    func testTheLineOnAINamesTheProviderChosen() {
        XCTAssertEqual(StatusWords.ai(F.digest(spent: nil, provider: "openai", model: "gpt-fictional").ai).text,
                       "OpenAI · gpt-fictional")
        XCTAssertEqual(StatusWords.ai(F.digest(spent: nil, provider: "local", model: "llama-fictional").ai).text,
                       "A model on this Mac · llama-fictional")
        XCTAssertEqual(StatusWords.ai(F.digest(engine: "none", provider: "gemini").ai).text,
                       "AI is off · no Gemini API key")
        XCTAssertEqual(StatusWords.ai(F.digest(engine: "none", off: "turned-off").ai).text, "AI mode is off")
        XCTAssertEqual(StatusWords.ai(F.digest(engine: "none", provider: "compatible", off: "no-url").ai).text,
                       "AI is off · no URL for the service")
        XCTAssertEqual(StatusWords.ai(F.digest(engine: "none", provider: "local", off: "no-model").ai).text,
                       "AI is off · no model chosen")
    }

    func testAnAccountsHealthLine() {
        XCTAssertEqual(StatusWords.healthLine(nil, now: now), StatusWords.HealthLine.none)
        let pin = F.standing("ada@example.test", error: "pin", kind: "TLS_PIN", errorAt: "2026-11-14T14:00:00Z")
        XCTAssertEqual(StatusWords.healthLine(pin, now: now), .certificateChanged(when: "60m ago"))
        let failed = F.standing("ada@example.test", error: "no", kind: "AUTH", errorAt: "2026-11-14T12:00:00Z")
        XCTAssertEqual(StatusWords.healthLine(failed, now: now), .syncError(kind: "AUTH", when: "3h ago"))
        let synced = F.standing("ada@example.test", ok: "2026-11-14T14:59:00Z", count: 3)
        XCTAssertEqual(StatusWords.healthLine(synced, now: now), .synced(when: "just now", count: 3))
    }

    func testAMomentReadsAsTheServerWritesIt() {
        let at = F.at("2026-11-14T10:42:00Z")
        for text in ["2026-11-14T10:42:00Z", "2026-11-14T10:42:00.000Z", "2026-11-14T11:42:00+01:00",
                     "2026-11-14T10:42:00", "2026-11-14 10:42:00"] {
            XCTAssertEqual(Moment.parse(text), at, text)
        }
        XCTAssertNil(Moment.parse(""))
    }

    func testAnAccountsNameIsItsAliasElseItsAddressShortened() {
        XCTAssertEqual(StatusWords.name("ada@example.test", aliases: ["ada@example.test": "Ada"]), "Ada")
        XCTAssertEqual(StatusWords.name("ada@example.test", aliases: ["ada@example.test": nil]), "ada")
        XCTAssertEqual(StatusWords.name("ada@example.test", aliases: ["ada@example.test": ""]), "ada")
    }
}

/// Quiet hours cover what the page's cover, by this computer's clock.
final class QuietHoursTests: XCTestCase {
    private func covers(_ time: String, from: String, until: String, on: Bool = true) -> Bool {
        QuietHours.cover(F.at("2026-11-14T\(time):00Z"), on: on, from: from, until: until, calendar: F.utc)
    }

    func testASpanWithinADay() {
        XCTAssertTrue(covers("13:00", from: "12:00", until: "14:00"))
        XCTAssertFalse(covers("14:00", from: "12:00", until: "14:00"), "until is when it ends")
        XCTAssertFalse(covers("11:59", from: "12:00", until: "14:00"))
    }

    func testASpanAcrossMidnightIsThatEveningAndTheMorningAfter() {
        XCTAssertTrue(covers("23:30", from: "22:00", until: "07:00"))
        XCTAssertTrue(covers("06:59", from: "22:00", until: "07:00"))
        XCTAssertFalse(covers("12:00", from: "22:00", until: "07:00"))
    }

    func testOffOrEmptyCoversNothing() {
        XCTAssertFalse(covers("23:30", from: "22:00", until: "07:00", on: false))
        XCTAssertFalse(covers("22:00", from: "22:00", until: "22:00"), "a span that starts where it ends")
    }
}

/// The reminder is for the first meeting that isn't all day and starts within ten minutes, or began under five ago.
final class SoonTests: XCTestCase {
    private let now = F.at("2026-11-14T15:00:00Z")

    func testTheNextMeetingWithinTenMinutes() {
        let upcoming = [
            F.meeting("All day", start: now, allDay: true, key: 1),
            F.meeting("Later", start: now.addingTimeInterval(11 * 60), key: 2),
            F.meeting("Stand-up", start: now.addingTimeInterval(4 * 60 - 30), key: 3),
        ]
        let soon = Soon.next(upcoming, now: now, closed: [])
        XCTAssertEqual(soon?.title, "Stand-up")
        XCTAssertEqual(soon?.when(now: now), "in 4 min")
        XCTAssertEqual(soon?.message(now: now), "Stand-up starts in 4 min")
        XCTAssertNil(Soon.next(upcoming, now: now, closed: [soon!.id]), "a reminder closed stays closed")
    }

    func testAMeetingUnderwaySaysNowUntilFiveMinutesIn() {
        let started = [F.meeting("", start: now.addingTimeInterval(-4 * 60))]
        XCTAssertEqual(Soon.next(started, now: now, closed: [])?.message(now: now), "(no title) starts now")
        XCTAssertNil(Soon.next([F.meeting("Old", start: now.addingTimeInterval(-6 * 60))], now: now, closed: []))
    }
}

/// New mail counts on the Dock and notifies only while nobody is at the app, and only as the preferences allow.
@MainActor
final class NewMailAlertsTests: XCTestCase {
    /// What was posted and asked. Each arrival posts from a task of its own: on the main actor, one at a time.
    @MainActor
    private final class Posted: Notifier {
        var said: [(id: String, title: String, body: String)] = []
        var asked = 0
        var answer: Bool? = nil
        func allowed() async -> Bool? { answer }
        func ask() async -> Bool {
            asked += 1
            return true
        }
        func post(id: String, title: String, body: String) async { said.append((id, title, body)) }
    }

    private var active = false
    private var badge: String?
    private var heard: [String] = []
    private var posted = Posted()

    private func alerts(_ preferences: API.Preferences?, now: Date = F.at("2026-11-14T15:00:00Z")) -> NewMailAlerts {
        let alerts = NewMailAlerts(notifier: posted, isActive: { [unowned self] in active },
                                   badge: { [unowned self] in badge = $0 }, announce: { [unowned self] in heard.append($0) })
        alerts.preferences = { preferences }
        alerts.name = { $0 == "ada@example.test" ? "Ada" : StatusWords.short($0) }
        alerts.now = { now }
        alerts.hasAccounts = { true }
        return alerts
    }

    /// A moment for what should not happen, to have had its chance to.
    private func settle() async {
        for _ in 0..<5 { await Task.yield() }
        try? await Task.sleep(for: .milliseconds(50))
    }

    /// Until `count` notifications are posted, each from a task a slow runner may not have run within any fixed time.
    private func posts(_ count: Int) async {
        await until { posted.said.count >= count }
    }

    func testWithSomeoneAtTheAppOnlyVoiceOverHearsIt() async {
        active = true
        let alerts = alerts(F.preferences())
        alerts.arrived(.init(account: "ada@example.test", folder: "INBOX", count: 2))
        await settle()
        XCTAssertEqual(heard, ["2 new messages arrived in Ada"])
        XCTAssertNil(badge)
        XCTAssertTrue(posted.said.isEmpty)
    }

    func testInTheBackgroundTheDockCountsAndANotificationGoes() async {
        let alerts = alerts(F.preferences())
        alerts.arrived(.init(account: "ada@example.test", folder: "INBOX", count: 1))
        alerts.arrived(.init(account: "news@example.test", folder: "INBOX", count: 2))
        await posts(2)
        XCTAssertEqual(badge, "3")
        XCTAssertEqual(posted.said.map(\.title).sorted(), ["New mail · Ada", "New mail · news"])
        XCTAssertEqual(Set(posted.said.map(\.body)), ["1 new message", "2 new messages"])
        XCTAssertEqual(Set(posted.said.map(\.id)), ["mailmind-ada@example.test", "mailmind-news@example.test"],
                       "one notification an account, replaced by the next")
        alerts.cameBack()
        XCTAssertNil(badge, "the count goes when the app is come back to")
        XCTAssertEqual(alerts.cameIn, 0)
    }

    func testThePreferencesAndQuietHoursHoldANotificationBackButNotTheCount() async {
        alerts(F.preferences(newMail: false)).arrived(.init(account: "ada@example.test", count: 1))
        // Quiet hours are by this Mac's clock: an hour either side of the test's now, wherever it runs.
        let hour = Calendar.current.component(.hour, from: F.at("2026-11-14T15:00:00Z"))
        let quiet = F.preferences(quiet: true, from: String(format: "%02d:00", (hour + 23) % 24),
                                  until: String(format: "%02d:00", (hour + 1) % 24))
        alerts(quiet).arrived(.init(account: "ada@example.test", count: 1))
        alerts(nil).arrived(.init(account: "ada@example.test", count: 1))
        await settle()
        XCTAssertTrue(posted.said.isEmpty)
        XCTAssertEqual(badge, "1")
    }

    func testAMeetingIsToldOnce() async {
        let now = F.at("2026-11-14T15:00:00Z")
        let alerts = alerts(F.preferences(), now: now)
        let soon = Soon.next([F.meeting("Stand-up", start: now.addingTimeInterval(240))], now: now, closed: [])
        alerts.meetingSoon(soon)
        alerts.meetingSoon(soon)
        await posts(1)
        await settle()
        XCTAssertEqual(posted.said.map(\.title), ["Stand-up starts in 4 min"])
        XCTAssertTrue(posted.said[0].body.contains("Room 4"))
    }

    func testTheSystemIsAskedOnceAndOnlyWhenTheresAnAccountToHearFrom() async {
        let none = alerts(F.preferences())
        none.hasAccounts = { false }
        none.askOnce()
        let off = alerts(F.preferences(newMail: false, meetings: false))
        off.askOnce()
        let on = alerts(F.preferences())
        on.askOnce()
        on.askOnce()
        await until { posted.asked >= 1 }
        await settle()
        XCTAssertEqual(posted.asked, 1)
    }

    func testATestRunNeverAsksTheSystem() {
        XCTAssertTrue(SystemNotifier.forThisProcess(["XCTestConfigurationFilePath": "x"]) is SystemNotifier.Silent)
        XCTAssertTrue(SystemNotifier.forThisProcess(["MAILMIND_MEASURE": "home"]) is SystemNotifier.Silent)
    }

    /// A demo's run (`just apple demo`: a home and a keychain's stand-in of its own) is no person's: it never asks to
    /// notify and never posts, as the person's MailMind, but keeps what it would have posted for a test to read.
    func testADemoRunNeverAsksOrPostsAndKeepsWhatItWouldHave() async throws {
        let home = FileManager.default.temporaryDirectory.appending(path: "mm-demo-\(UUID().uuidString)").path
        for demo in [["MAILMIND_HOME": home, "MAILMIND_TEST_KEYCHAIN": home + "/keychain.json"],
                     ["MAILMIND_HOME": home], ["MAILMIND_TEST_KEYCHAIN": home + "/keychain.json"]] {
            XCTAssertFalse(CoreHome.isAPersonsRun(demo))
            XCTAssertTrue(SystemNotifier.forThisProcess(demo) is SystemNotifier.Silent, "\(demo.keys.sorted())")
        }
        let quiet = try XCTUnwrap(SystemNotifier.forThisProcess(["MAILMIND_HOME": home]) as? SystemNotifier.Silent)
        let alerts = NewMailAlerts(notifier: quiet, isActive: { false }, badge: { _ in }, announce: { _ in })
        alerts.preferences = { F.preferences() }
        alerts.name = { _ in "Ada" }
        alerts.now = { F.at("2026-11-14T15:00:00Z") }
        alerts.hasAccounts = { true }

        alerts.askOnce()
        let asked = await alerts.ask()
        XCTAssertFalse(asked, "nobody is asked")
        let allowed = await alerts.allowed()
        XCTAssertEqual(allowed, false)
        alerts.arrived(.init(account: "ada@example.test", folder: "INBOX", count: 2))
        await until { !quiet.wouldHavePosted.isEmpty }
        XCTAssertEqual(quiet.wouldHavePosted, [.init(id: "mailmind-ada@example.test", title: "New mail · Ada",
                                                    body: "2 new messages", opens: nil)])
    }

    /// The quiet notifier keeps only the latest it would have posted, so a demo left running keeps no more.
    func testTheQuietNotifierKeepsOnlyTheLatest() async {
        let quiet = SystemNotifier.Silent()
        for n in 0..<(SystemNotifier.Silent.keeps + 5) {
            await quiet.post(id: "mailmind-\(n)", title: "New mail", body: "\(n)")
        }
        XCTAssertEqual(quiet.wouldHavePosted.count, SystemNotifier.Silent.keeps)
        XCTAssertEqual(quiet.wouldHavePosted.first?.body, "5")
        XCTAssertEqual(quiet.wouldHavePosted.last?.body, "\(SystemNotifier.Silent.keeps + 4)")
    }
}

/// The status model's own logic: a change laid over the preferences, and when a sync asked for has ended.
@MainActor
final class AppStatusTests: XCTestCase {
    func testAChangeIsLaidOverThePreferences() throws {
        let merged = try XCTUnwrap(AppStatus.merged(F.preferences(), .init(quietFrom: "21:30", quietHours: true)))
        XCTAssertTrue(merged.quietHours)
        XCTAssertEqual(merged.quietFrom, "21:30")
        XCTAssertEqual(merged.quietUntil, "07:00", "what the change leaves out stays")
        XCTAssertEqual(merged.undoSendSeconds, 10)
    }

    /// Another provider starts from its own model and URL, as the server sets them back, unless the change names them.
    func testAnotherProviderStartsFromItsOwnModelAndURL() throws {
        let held = F.preferences(provider: "compatible", model: "fictional-7b", baseURL: "https://ai.example.test/v1")
        let other = try XCTUnwrap(AppStatus.merged(held, AIChoices.choose(.openai)))
        XCTAssertEqual(other.aiProvider, .openai)
        XCTAssertEqual(other.aiModel, "")
        XCTAssertEqual(other.aiBaseUrl, "")
        let named = try XCTUnwrap(AppStatus.merged(held, .init(aiModel: "gpt-fictional", aiProvider: .openai)))
        XCTAssertEqual(named.aiModel, "gpt-fictional", "a model named with it stays")
        XCTAssertEqual(named.aiBaseUrl, "")
        let same = try XCTUnwrap(AppStatus.merged(held, .init(aiProvider: .compatible)))
        XCTAssertEqual(same.aiModel, "fictional-7b", "the same provider again keeps them")
        XCTAssertEqual(same.aiBaseUrl, "https://ai.example.test/v1")
    }

    /// Only AI's settings make the window read how AI stands again.
    func testWhatTouchesAI() {
        XCTAssertTrue(AppStatus.touchesAI(.init(aiEnabled: false)))
        XCTAssertTrue(AppStatus.touchesAI(.init(aiProvider: .gemini)))
        XCTAssertTrue(AppStatus.touchesAI(.init(aiModel: "")))
        XCTAssertTrue(AppStatus.touchesAI(.init(aiBaseUrl: "")))
        XCTAssertTrue(AppStatus.touchesAI(.init(aiDailyLimitUsd: 5)))
        XCTAssertFalse(AppStatus.touchesAI(.init(quietHours: true)))
        XCTAssertFalse(AppStatus.touchesAI(.init(morningBrief: true)))
    }

    func testASyncEndsWhenEveryAccountHasSyncedOrFailedSince() {
        let before = [F.standing("ada@example.test", ok: "2026-11-14T09:00:00Z", count: 1),
                      F.standing("news@example.test", ok: "2026-11-14T09:00:00Z", count: 1)]
        let one = [F.standing("ada@example.test", ok: "2026-11-14T09:05:00Z", count: 2), before[1]]
        let both = [one[0], F.standing("news@example.test", ok: "2026-11-14T09:00:00Z", error: "no", kind: "NET",
                                       errorAt: "2026-11-14T09:05:00Z", count: 1)]
        let accounts = ["ada@example.test", "news@example.test"]
        XCTAssertFalse(AppStatus.syncEnded(before: before, after: one, accounts: accounts, running: false))
        XCTAssertTrue(AppStatus.syncEnded(before: before, after: both, accounts: accounts, running: false))
        XCTAssertFalse(AppStatus.syncEnded(before: before, after: both, accounts: accounts, running: true))
    }

    /// A sync that fails with trouble that may pass (offline, a timeout) records nothing for its account, so Get New Mail
    /// stops once a round has ended since it asked, whether or not every account's sync went well.
    func testASyncEndsWithTheRoundEvenWhenAnAccountRecordedNothing() {
        let before = [F.standing("ada@example.test", ok: "2026-11-14T09:00:00Z", count: 1),
                      F.standing("news@example.test", ok: "2026-11-14T09:00:00Z", count: 1)]
        let one = [F.standing("ada@example.test", ok: "2026-11-14T09:05:00Z", count: 2), before[1]]
        let accounts = ["ada@example.test", "news@example.test"]
        XCTAssertFalse(AppStatus.syncEnded(before: before, after: one, accounts: accounts, running: false,
                                           round: "2026-11-14T09:00:01Z", now: "2026-11-14T09:00:01Z"),
                       "no round has ended since")
        XCTAssertTrue(AppStatus.syncEnded(before: before, after: one, accounts: accounts, running: false,
                                          round: "2026-11-14T09:00:01Z", now: "2026-11-14T09:05:02Z"))
        XCTAssertTrue(AppStatus.syncEnded(before: before, after: one, accounts: accounts, running: false,
                                          round: nil, now: "2026-11-14T09:05:02Z"), "the first round since launch")
        XCTAssertFalse(AppStatus.syncEnded(before: before, after: one, accounts: accounts, running: true,
                                           round: "2026-11-14T09:00:01Z", now: "2026-11-14T09:05:02Z"),
                       "another round still runs")
    }

    /// What Get New Mail says as it stops: done only when every account's sync went well.
    func testWhatASyncSaysAsItStops() {
        let before = [F.standing("ada@example.test", ok: "2026-11-14T09:00:00Z", count: 1),
                      F.standing("news@example.test", ok: "2026-11-14T09:00:00Z", count: 1)]
        let accounts = ["ada@example.test", "news@example.test"]
        let name: (String) -> String = { $0 == "ada@example.test" ? "Ada" : StatusWords.short($0) }
        let both = [F.standing("ada@example.test", ok: "2026-11-14T09:05:00Z", count: 2),
                    F.standing("news@example.test", ok: "2026-11-14T09:05:00Z", count: 2)]
        XCTAssertEqual(AppStatus.synced(before: before, after: both, accounts: accounts, ended: true, name: name),
                       "Sync complete")
        let quiet = [both[0], before[1]]
        XCTAssertEqual(AppStatus.synced(before: before, after: quiet, accounts: accounts, ended: true, name: name),
                       "Couldn’t sync news. MailMind tries again on its own.", "a failure that may pass records nothing")
        let slow = [F.standing("ada@example.test", ok: "2026-11-14T09:00:00Z", error: "no", kind: "TIMEOUT",
                               errorAt: "2026-11-14T09:05:00Z", count: 2), before[1]]
        XCTAssertEqual(AppStatus.synced(before: before, after: slow, accounts: accounts, ended: true, name: name),
                       "Couldn’t sync 2 accounts. MailMind tries again on its own.", "a timeout may pass")
        XCTAssertEqual(AppStatus.synced(before: before, after: [slow[0], both[1]], accounts: accounts, ended: true,
                                        name: name),
                       "Couldn’t sync Ada. MailMind tries again on its own.")
        let fresh = [F.standing("ada@example.test", ok: "2026-11-14T09:05:00Z", count: 1), before[1]]
        XCTAssertEqual(AppStatus.synced(before: [], after: fresh, accounts: ["ada@example.test"], ended: true, name: name),
                       "Sync complete", "a new account's first sync")
        XCTAssertEqual(AppStatus.synced(before: before, after: before, accounts: accounts, ended: false, name: name),
                       "Still syncing. New mail shows as it comes.")
    }

    /// A sync that failed in a way trying again won't mend (the password turned down, a certificate to trust, a server
    /// not reached or one that said no) says where to see what to do, not that MailMind tries again on its own.
    func testASyncThatWaitsForYouSaysWhereToSeeWhatToDo() {
        let before = [F.standing("ada@example.test", ok: "2026-11-14T09:00:00Z", count: 1),
                      F.standing("news@example.test", ok: "2026-11-14T09:00:00Z", count: 1)]
        let accounts = ["ada@example.test", "news@example.test"]
        let name: (String) -> String = { $0 == "ada@example.test" ? "Ada" : StatusWords.short($0) }
        let newsSynced = F.standing("news@example.test", ok: "2026-11-14T09:05:00Z", count: 2)
        for kind in ["AUTH", "TLS_PIN", "NETWORK", "IMAP", "SOMETHING_NEW"] {
            let failed = F.standing("ada@example.test", ok: "2026-11-14T09:00:00Z", error: "no", kind: kind,
                                    errorAt: "2026-11-14T09:05:00Z", count: 2)
            XCTAssertEqual(AppStatus.synced(before: before, after: [failed, newsSynced], accounts: accounts, ended: true,
                                            name: name),
                           "Couldn’t sync Ada. Open Settings › Accounts to see what to do.", kind)
            XCTAssertEqual(AppStatus.synced(before: before, after: [failed, before[1]], accounts: accounts, ended: true,
                                            name: name),
                           "Couldn’t sync 2 accounts. Open Settings › Accounts to see what to do.",
                           "\(kind), with another that recorded nothing")
        }
    }

    func testTheWelcomesCardSaysWhatComesNotAgainThatThereAreNoAccounts() {
        // Its label says "No accounts yet": the words under it go on from there, as the welcome says welcome once.
        XCTAssertTrue(Welcome.noAccounts.hasPrefix("Connect an account"))
        XCTAssertFalse(Welcome.noAccounts.localizedCaseInsensitiveContains("no email accounts"))
        XCTAssertFalse(Welcome.noAccounts.localizedCaseInsensitiveContains("yet"))
    }

    func testTheFirstRunIsNoAccountOnceTheyHaveBeenRead() {
        let status = AppStatus()
        XCTAssertFalse(status.firstRun, "not before the accounts are read")
        status.hold()
        XCTAssertTrue(status.firstRun)
        status.hold(accounts: [F.account("ada@example.test")])
        XCTAssertFalse(status.firstRun)
    }

    func testAnAlertClosedStaysClosed() {
        let status = AppStatus()
        status.hold(digest: F.digest(), health: [F.standing("ada@example.test", error: "no", kind: "AUTH")])
        XCTAssertEqual(status.alerting, ["ada@example.test"])
        status.alertsClosed.insert("ada@example.test")
        XCTAssertEqual(status.alerting, [])
    }

    func testTheGuideIsOfferedOnceWithMailAndNothingInTheWay() {
        XCTAssertTrue(Guide.offers(preferences: F.preferences(), accounts: 1, hasMail: true, busy: false))
        XCTAssertFalse(Guide.offers(preferences: F.preferences(guideOffered: true), accounts: 1, hasMail: true, busy: false))
        XCTAssertFalse(Guide.offers(preferences: nil, accounts: 1, hasMail: true, busy: false), "not before the server says")
        XCTAssertFalse(Guide.offers(preferences: F.preferences(), accounts: 0, hasMail: false, busy: false))
        XCTAssertFalse(Guide.offers(preferences: F.preferences(), accounts: 1, hasMail: true, busy: true))
    }

    func testTheGuideTeachesOnlyWhatIsOn() {
        XCTAssertEqual(Guide.steps(ai: true, singleKeys: true).map(\.id), ["brief", "rail", "palette", "ask", "screener"])
        XCTAssertEqual(Guide.steps(ai: false, singleKeys: false).map(\.id), ["brief", "rail", "palette", "screener"])
        XCTAssertFalse(Guide.steps(ai: false, singleKeys: false)[1].text.contains("j and k"))
    }

    func testTheGuideCallsTheGateWhatTheRailCallsIt() throws {
        let gate = try XCTUnwrap(Guide.steps(ai: true, singleKeys: true).first { $0.id == "screener" })
        XCTAssertEqual(gate.title, "At the gate", "as the rail says it")
        XCTAssertFalse(gate.title.contains("Screener") || gate.text.contains("Screener"), "one name for one place")
        XCTAssertTrue(gate.text.contains("Let in") && gate.text.contains("Screen out"), "and the gate's own buttons")
        XCTAssertEqual(DeskPlace.screener(account: "ada@example.test").title, "At the gate", "and the window's title")
    }
}

/// What the status reads again as events come.
@MainActor
final class StatusHearsTests: XCTestCase {
    /// A first sync ends, and the older-mail lane then says folder-synced at each step, for minutes: the status reads
    /// the digest again while the steps go on, once for the burst, not only once they stop.
    func testTheDigestIsReadWhileTheOlderMailLaneGoesOn() async {
        let server = Fixture.server(rows: [])
        server.on("GET", API.Get.Digest.path, Fixture.digest(accounts: [Fixture.ada]))
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        status.wire = server.wire
        let started = ContinuousClock.now
        var step = 0
        while status.digest == nil {
            guard ContinuousClock.now - started < .seconds(3) else {
                return XCTFail("the status waited out \(step) steps of the lane")
            }
            step += 1
            await status.heard(CoreEvent(id: String(step), name: "folder-synced",
                                         data: #"{"account": "ada@example.test", "folder": "INBOX", "ok": true, "new": 50}"#))
            try? await Task.sleep(for: .milliseconds(100))
        }
        XCTAssertEqual(status.digest?.accounts, [Fixture.ada])
        XCTAssertGreaterThan(step, 2, "the steps came faster than a read waits")
        XCTAssertLessThan(server.asked(API.Get.Digest.path).count, step, "a read for the burst, not one a step")
    }

    /// An account's server stops answering: the core says so (`sync-trouble`) and nothing else, no sync having ended.
    /// The status reads the digest again, and the pill says it is trying again within moments, not at the next sync.
    func testAServerThatDidntAnswerIsShownTryingAgainOnceTheCoreSaysSo() async {
        var trying = StatusFixtures.digest(accounts: [Fixture.ada], status: [Fixture.ada: "ok"],
                                           last: "2026-11-14T14:59:00Z")
        trying.status[Fixture.ada]?.tryingAgain = true
        let server = Fixture.server(rows: [])
        server.on("GET", API.Get.Digest.path, trying)
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        status.wire = server.wire
        await status.heard(CoreEvent(id: "1", name: "sync-trouble", data: #"{"account": "ada@example.test"}"#))
        let started = ContinuousClock.now
        while status.digest == nil {
            guard ContinuousClock.now - started < .seconds(3) else { return XCTFail("the digest was never read again") }
            try? await Task.sleep(for: .milliseconds(50))
        }
        XCTAssertEqual(status.pill, StatusWords.Pill(dot: .busy, text: "Couldn’t reach the server · trying again"))
    }

    /// A round of summaries landed: how many still wait for one, which Settings' AI says, is read again.
    func testSummariesLandingReadHowManyStillWait() async {
        var digest = StatusFixtures.digest(accounts: [Fixture.ada], status: [Fixture.ada: "ok"],
                                           last: "2026-11-14T14:59:00Z")
        digest.ai.summariesWaiting = 3
        let server = Fixture.server(rows: [])
        server.on("GET", API.Get.Digest.path, digest)
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        status.wire = server.wire
        await status.heard(CoreEvent(id: "1", name: "summarized", data: #"{"count": 9}"#))
        let started = ContinuousClock.now
        while status.digest == nil {
            guard ContinuousClock.now - started < .seconds(3) else { return XCTFail("the digest was never read again") }
            try? await Task.sleep(for: .milliseconds(50))
        }
        XCTAssertEqual(status.digest?.ai.summariesWaiting, 3)
    }
}
