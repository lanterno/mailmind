import AppKit
import SwiftUI
import XCTest

/// What floats over Meadow's window, by its rules: the palette's rows, sections, modes and icons, an answer's and a
/// bulk action's turns in it, the snooze chooser's keys and the status's words, which dialog shows of all asked for,
/// the toasts' icons, and the keyboard's legend against the keys the window has.
@MainActor
final class MeadowOverlayTests: XCTestCase {
    // MARK: The palette

    private func entry(_ id: String, _ group: String = "", detail: String? = nil) -> Entries.Entry {
        Entries.Entry(id: id, group: group, label: id, detail: detail) {}
    }

    func testNothingTypedShowsWhatToTryThenTheCommandsInTheirGroups() {
        let rows = Entries.rows("", entries: [entry("inbox", "Go to"), entry("sent", "Go to"), entry("archive", "Act")],
                                starters: [entry("? ask"), entry("! act")], typed: nil)
        XCTAssertEqual(rows.map(\.entry.id), ["? ask", "! act", "inbox", "sent", "archive"])
        XCTAssertEqual(rows.map(\.section), ["Try", nil, "Go to", nil, "Act"])
    }

    func testWhatIsTypedFindsCommandsThenSearchesMail() {
        let typed: (String) -> Entries.Entry? = {
            Entries.Entry(id: "search-all", label: "Search all mail for \($0)") {}
        }
        let rows = Entries.rows("arch", entries: [entry("archive"), entry("inbox")], starters: [entry("? ask")],
                                typed: typed)
        XCTAssertEqual(rows.map(\.entry.id), ["archive", "search-all"])
        XCTAssertEqual(rows.map(\.section), ["Commands", "Mail"])
        let none = Entries.rows("zzzz", entries: [entry("archive")], starters: [], typed: typed)
        XCTAssertEqual(none.map(\.entry.id), ["search-all"])
        XCTAssertEqual(none.map(\.section), [nil], "a search alone needs no heading")
    }

    func testAModeIsTheFirstCharacterTyped() {
        let modes = [Entries.Mode(prefix: "?", card: { _, _ in AnyView(EmptyView()) }, submit: { _, _ in }),
                     Entries.Mode(prefix: "!", card: { _, _ in AnyView(EmptyView()) }, submit: { _, _ in })]
        XCTAssertEqual(Entries.mode(of: "? when is the dentist", in: modes)?.words, "when is the dentist")
        XCTAssertEqual(Entries.mode(of: "  !archive", in: modes)?.mode.prefix, "!")
        XCTAssertEqual(Entries.mode(of: "  !archive", in: modes)?.words, "archive")
        XCTAssertNil(Entries.mode(of: "dentist?", in: modes))
        XCTAssertNil(Entries.mode(of: "? asked", in: []), "no assistant, no modes")
    }

    func testTheListIsAsTallAsItsRows() {
        XCTAssertEqual(Entries.height(of: []), 56)
        let rows: [(section: String?, entry: Entries.Entry)] = [("Try", entry("a", detail: "more")), (nil, entry("b"))]
        XCTAssertEqual(Entries.height(of: rows), 16 + 25 + 48 + 40)
    }

    func testEachCommandHasItsIcon() {
        XCTAssertEqual(PaletteIcons.of("archive"), .archive)
        XCTAssertEqual(PaletteIcons.of("snooze"), .clock)
        XCTAssertEqual(PaletteIcons.of("help"), .keyboard)
        XCTAssertEqual(PaletteIcons.of("across:\(VirtualFolder.waiting.rawValue)"), .hourglass)
        XCTAssertEqual(PaletteIcons.of("acct:ada@example.test"), .inbox)
        XCTAssertEqual(PaletteIcons.of("fold:ada@example.test:INBOX"), .inbox)
        XCTAssertEqual(PaletteIcons.of("fold:ada@example.test:Work:Clients"), .folder)
        XCTAssertEqual(PaletteIcons.of("view:3"), .search)
        XCTAssertEqual(PaletteIcons.of("something-new"), .arrowRight)
    }

    func testReturnAsksOnlyWhatIsntAskedAlready() {
        XCTAssertFalse(AskCard.asks("  ", now: nil))
        XCTAssertTrue(AskCard.asks("when is the dentist", now: nil))
        XCTAssertFalse(AskCard.asks("when is the dentist", now: .reading(question: "when is the dentist")))
        XCTAssertTrue(AskCard.asks("when is the dentist", now: .reading(question: "who wrote")))
        XCTAssertTrue(AskCard.asks("when is the dentist", now: .failed(question: "when is the dentist", why: "no")))
    }

    func testACitedDaySaysTodayOrTheServersWords() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let now = ISO8601DateFormatter().date(from: "2026-11-14T15:00:00Z")!
        XCTAssertEqual(AskCite.day("2026-11-14", now: now, calendar: calendar), "Today")
        XCTAssertEqual(AskCite.day("last spring", now: now, calendar: calendar), "last spring")
        XCTAssertEqual(AskCite.short("dentist@example.test"), "dentist")
    }

    // MARK: A bulk action in the palette

    func testThePlanShownIsTheOneForWhatIsTyped() async {
        let routes = DeskRoutes()
        let notices = DeskNotices()
        routes.answer("POST", "/api/ai/do-plan", API.AiDoPlanAnswer.planPreview(DeskFixture.preview(.trash)))
        let model = PlanModel(link: CoreLink(routes), notices: notices)
        XCTAssertNil(model.command)
        model.start()
        XCTAssertNil(model.command, "the intro plans nothing yet")

        await model.plan("delete the shop's newsletters older than a week")
        XCTAssertEqual(model.command, "delete the shop's newsletters older than a week")
        XCTAssertNotNil(model.shown(for: " delete the shop's newsletters older than a week "))
        XCTAssertNil(model.shown(for: "delete the shop's newsletters"), "an edit is a new plan")
    }

    func testReturnArmsADestructivePlanThenClosesAndRunsIt() async throws {
        let routes = DeskRoutes()
        let notices = DeskNotices()
        routes.answer("POST", "/api/ai/do-plan", API.AiDoPlanAnswer.planPreview(DeskFixture.preview(.trash)))
        routes.answer("POST", "/api/ai/do-confirm", API.PlanCarriedOut(
            count: 40, failed: 0, gone: 0, ok: true, queued: 0, undoable: true, verb: .trash))
        let model = PlanModel(link: CoreLink(routes), notices: notices)
        await model.plan("delete the shop's newsletters older than a week")
        var closed = 0

        model.press { closed += 1 }
        XCTAssertEqual(closed, 0, "the first press only arms it")
        XCTAssertTrue(routes.sent("POST", "/api/ai/do-confirm").isEmpty)

        model.press { closed += 1 }
        XCTAssertEqual(closed, 1)
        for _ in 0..<50 where routes.sent("POST", "/api/ai/do-confirm").isEmpty {
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertEqual(routes.sent("POST", "/api/ai/do-confirm").count, 1)
    }

    // MARK: Popovers

    func testADigitPicksASnoozePreset() {
        XCTAssertEqual(SnoozeChooser.preset(forKey: "1"), API.SnoozePreset.allCases.first)
        XCTAssertEqual(SnoozeChooser.preset(forKey: "\(API.SnoozePreset.allCases.count)"),
                       API.SnoozePreset.allCases.last)
        XCTAssertNil(SnoozeChooser.preset(forKey: "0"))
        XCTAssertNil(SnoozeChooser.preset(forKey: "\(API.SnoozePreset.allCases.count + 1)"))
        XCTAssertNil(SnoozeChooser.preset(forKey: "h"))
    }

    func testAnAccountsStateSaysHowItStands() {
        let now = StatusFixtures.at("2026-11-14T15:00:00Z")
        let health = [
            StatusFixtures.standing("ada@example.test", ok: "2026-11-14T14:58:00Z", count: 3),
            StatusFixtures.standing("news@example.test", ok: "2026-11-14T09:00:00Z", error: "LOGIN refused",
                                    kind: "AUTH", errorAt: "2026-11-14T14:30:00Z", count: 1),
        ]
        let digest = StatusFixtures.digest(status: ["bridge@example.test": "syncing"])
        let ada = StatusMenu.state(of: "ada@example.test", digest: digest, health: health, now: now)
        XCTAssertEqual(ada.standing, .connected)
        XCTAssertEqual(ada.words, "Synced 2m ago")
        let news = StatusMenu.state(of: "news@example.test", digest: digest, health: health, now: now)
        XCTAssertEqual(news.standing, .cantConnect)
        XCTAssertEqual(news.words, "Can’t connect")
        let bridge = StatusMenu.state(of: "bridge@example.test", digest: digest, health: health, now: now)
        XCTAssertEqual(bridge.words, "Syncing…")
        XCTAssertEqual(StatusMenu.state(of: "new@example.test", digest: digest, health: health, now: now).words, "")
    }

    // MARK: Which dialog

    func testTheDialogShownIsTheOneToAnswerFirst() {
        let move = MoveAsk(account: "ada@example.test", from: "INBOX")
        XCTAssertEqual(WindowDialog.shown(moving: move, savingView: "from:x", sheet: .ask, planned: nil, palette: true,
                                          keys: true), .move(move))
        XCTAssertEqual(WindowDialog.shown(moving: nil, savingView: "from:x", sheet: .ask, planned: nil, palette: true,
                                          keys: true), .saveView("from:x"))
        XCTAssertEqual(WindowDialog.shown(moving: nil, savingView: nil, sheet: .ask, planned: nil, palette: true,
                                          keys: true), .palette(initial: "? "))
        XCTAssertEqual(WindowDialog.shown(moving: nil, savingView: nil, sheet: .plan, planned: "archive old news",
                                          palette: false, keys: false), .palette(initial: "! archive old news"))
        XCTAssertEqual(WindowDialog.shown(moving: nil, savingView: nil, sheet: .curate, planned: nil, palette: true,
                                          keys: true), .palette(initial: ""), "Curate is a sheet of its own")
        XCTAssertEqual(WindowDialog.shown(moving: nil, savingView: nil, sheet: nil, planned: nil, palette: false,
                                          keys: true), .keys)
        XCTAssertNil(WindowDialog.shown(moving: nil, savingView: nil, sheet: .snoozeUntil([], canUnsnooze: false),
                                        planned: nil, palette: false, keys: false), "the snooze chooser is a popover")
    }

    func testOnlyTheDesksOwnSheetsOpenAsSheets() {
        XCTAssertTrue(Desk.Sheet.ask.floats)
        XCTAssertTrue(Desk.Sheet.plan.floats)
        XCTAssertTrue(Desk.Sheet.snoozeUntil([], canUnsnooze: true).floats)
        XCTAssertFalse(Desk.Sheet.curate.floats)
        XCTAssertFalse(Desk.Sheet.review.floats)
    }

    // MARK: Toasts

    func testAToastsIconFollowsItsVerb() {
        XCTAssertEqual(MailboxToast.icon("Archived 3 messages"), .archive)
        XCTAssertEqual(MailboxToast.icon("Trashed"), .trash)
        XCTAssertEqual(MailboxToast.icon("Moved to Work"), .folder)
        XCTAssertEqual(MailboxToast.icon("Marked unread"), .mail)
        XCTAssertEqual(MailboxToast.icon("Couldn’t archive"), .alert)
        XCTAssertEqual(MailboxToast.icon("Updated 3, 1 failed"), .alert)
        XCTAssertEqual(MailboxToast.icon("Archive undone"), .undo)
        XCTAssertEqual(MailboxToast.icon("Snoozed"), .clock)
        XCTAssertEqual(MailboxToast.icon("Notifications on"), .check)
    }

    func testASendsFloatSaysItsWordsUnderItsIcon() {
        XCTAssertEqual(SendFloatView.said("↑ Sending…", clock: false).words, "Sending…")
        XCTAssertEqual(SendFloatView.said("↑ Sending…", clock: false).icon, .send)
        XCTAssertEqual(SendFloatView.said("✓ Sent · 2 recipients", clock: false).icon, .check)
        XCTAssertEqual(SendFloatView.said("✓ Sent · 2 recipients", clock: false).words, "Sent · 2 recipients")
        XCTAssertEqual(SendFloatView.said("Scheduled · Mon 8:00", clock: true).icon, .clock)
    }

    // MARK: The keyboard's legend

    func testTheLegendListsTheWindowsKeys() {
        let listed = Set(KeysLegend.columns.flatMap { $0 }.flatMap(\.keys).map { $0.keys.joined(separator: " ") })
        // The list's single keys (`RowKeys`), the desk's and the composer's, and the frame's.
        for key in ["J", "K", "N", "P", "E", "D", "#", "B", "M", "S", "U", "Z", "V", "/", "H", "W", "L", "I",
                    "R", "A", "F", "C", "[", "↵", "Esc", "?", "⌘K"] {
            XCTAssertTrue(listed.contains(key), "the legend lists \(key)")
        }
        // Snooze is H, as the window has it.
        XCTAssertEqual(KeysLegend.triage.keys.first { $0.words.hasPrefix("Snooze") }?.keys, ["H"])
    }

    func testGoToListsEveryFolderAKeyGoesTo() {
        let chords = Set(KeysLegend.goTo.keys.map { $0.keys })
        XCTAssertEqual(chords.count, Mailbox.roleKeys.count + Mailbox.virtualKeys.count)
        for key in Mailbox.roleKeys.keys.map(String.init) + Mailbox.virtualKeys.keys.map(String.init) {
            XCTAssertTrue(chords.contains(["G", key.uppercased()]), "G \(key) is listed")
        }
        XCTAssertEqual(KeysLegend.goTo.keys.first?.words, "Inbox")
    }
}
