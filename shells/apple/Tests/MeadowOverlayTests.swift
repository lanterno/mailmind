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

    private static let deleting = "delete the shop's newsletters older than a week"

    /// A destructive plan for `deleting`, its first press made.
    private func armed(_ routes: DeskRoutes = DeskRoutes()) async -> PlanModel {
        routes.answer("POST", "/api/ai/do-plan", API.AiDoPlanAnswer.planPreview(DeskFixture.preview(.trash)))
        let model = PlanModel(link: CoreLink(routes), notices: DeskNotices())
        await model.plan(Self.deleting)
        model.press {}
        return model
    }

    private func isArmed(_ model: PlanModel) -> Bool {
        if case .plan(_, _, let armed)? = model.shown(for: Self.deleting) { return armed }
        return false
    }

    /// Draw `host` until `done`, as the window would over a few frames: a window off screen draws only when asked.
    private func settle(_ host: NSView, until done: () -> Bool) async throws {
        for _ in 0..<50 where !done() {
            host.layoutSubtreeIfNeeded()
            if let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) {
                host.cacheDisplay(in: host.bounds, to: bitmap)
            }
            try await Task.sleep(for: .milliseconds(10))
        }
    }

    func testAFirstPressIsTakenBackAndTheNextOnlyArmsAgain() async throws {
        let routes = DeskRoutes()
        let model = await armed(routes)
        XCTAssertTrue(isArmed(model))
        model.disarm()
        XCTAssertFalse(isArmed(model))
        XCTAssertNotNil(model.shown(for: Self.deleting), "the preview stays")
        var closed = 0
        model.press { closed += 1 }
        XCTAssertEqual(closed, 0, "the next press only arms it again")
        XCTAssertTrue(routes.sent("POST", "/api/ai/do-confirm").isEmpty)
    }

    func testThePlanCardTakesBackAFirstPressAsItsWordsChangeOrItGoes() async throws {
        let model = await armed()
        func card(_ words: String) -> AnyView {
            AnyView(PlanCard(model: model, words: words, showSearch: nil, close: {}).meadowLight(.day))
        }
        let host = NSHostingView(rootView: card(Self.deleting))
        host.frame = CGRect(x: 0, y: 0, width: 600, height: 500)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        try await settle(host) { false }
        XCTAssertTrue(isArmed(model), "showing it takes nothing back")

        host.rootView = card(Self.deleting + "s")
        try await settle(host) { !isArmed(model) }
        XCTAssertFalse(isArmed(model), "an edit, even one taken back, needs a first press again")

        model.press {}
        host.rootView = card(Self.deleting)
        try await settle(host) { !isArmed(model) }
        model.press {}
        XCTAssertTrue(isArmed(model))
        host.rootView = AnyView(EmptyView())
        try await settle(host) { !isArmed(model) }
        XCTAssertFalse(isArmed(model), "a card gone from the screen takes it back")
        window.contentView = nil
    }

    func testAPlanStopsHoweverThePaletteGoes() async throws {
        let mailbox = Mailbox()
        let desk = Desk()
        let routes = DeskRoutes()
        desk.link.routes = routes
        routes.answer("POST", "/api/ai/do-plan", API.AiDoPlanAnswer.planPreview(DeskFixture.preview(.trash)))
        await desk.plan.plan(Self.deleting)
        desk.plan.press {}
        let host = NSHostingView(rootView: Color.clear.frame(width: 1000, height: 700)
            .modifier(WindowOverlays(mailbox: mailbox))
            .environment(desk)
            .meadowLight(.day))
        host.frame = CGRect(x: 0, y: 0, width: 1000, height: 700)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        mailbox.paletteOpen = true
        try await settle(host) { mailbox.typing }
        XCTAssertTrue(mailbox.typing, "the palette is up")
        XCTAssertTrue(isArmed(desk.plan))

        // Move To takes the palette's place, never through the palette's close.
        mailbox.moving = MoveAsk(account: "ada@example.test", from: "INBOX")
        try await settle(host) { desk.plan.doing == nil }
        XCTAssertNil(desk.plan.doing, "nothing waits armed for the palette to come back")
        mailbox.moving = nil
        mailbox.paletteOpen = false
        window.contentView = nil
    }

    // MARK: Popovers

    /// The snooze chooser drawn in a window, as its popover shows it, until its menu has offered its items.
    private func chooser(_ copies: [MailCopy], model: AttentionModel, menu: MeadowMenuModel,
                         close: @escaping @MainActor () -> Void) async throws -> NSWindow {
        let host = NSHostingView(rootView: SnoozeChooser(copies: copies, model: model, menu: menu, close: close)
            .meadowLight(.day))
        host.frame = CGRect(x: 0, y: 0, width: 300, height: 400)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        for _ in 0..<50 where menu.items.isEmpty {
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(10))
        }
        return window
    }

    func testPickingATimeOfOnesOwnClosesTheChooserAndAsksTheWindow() async throws {
        let attention = AttentionModel(link: CoreLink(DeskRoutes()), notices: DeskNotices())
        let menu = MeadowMenuModel()
        var closed = 0
        let window = try await chooser([DeskFixture.ada, DeskFixture.grace], model: attention, menu: menu) {
            closed += 1
        }
        let own = try XCTUnwrap(menu.items.firstIndex { $0.id == "own" })
        menu.at = own
        XCTAssertTrue(menu.key(NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0,
                                                windowNumber: 0, context: nil, characters: "\r",
                                                charactersIgnoringModifiers: "\r", isARepeat: false, keyCode: 36)!))
        XCTAssertEqual(closed, 1, "the popover goes, so the dialog can have the keyboard")
        XCTAssertEqual(attention.choosingTime, [DeskFixture.ada, DeskFixture.grace])
        XCTAssertEqual(WindowDialog.shown(moving: nil, savingView: nil, choosingTime: attention.choosingTime,
                                          sheet: nil, planned: nil, palette: false, keys: false),
                       .snoozeTime([DeskFixture.ada, DeskFixture.grace]))
        window.contentView = nil
    }

    func testATypedTimeIsTextThatAPopoverLeavesAlone() {
        let field = NSDatePicker()
        field.datePickerStyle = .textFieldAndStepper
        XCTAssertTrue(MeadowPopoverKeys.typesText(field))
        let calendar = NSDatePicker()
        calendar.datePickerStyle = .clockAndCalendar
        XCTAssertFalse(MeadowPopoverKeys.typesText(calendar))
    }

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

    func testASnoozesTimeOfOnesOwnIsAskedBeforeWhatTheDeskOpened() {
        let copies = [DeskFixture.ada]
        XCTAssertEqual(WindowDialog.shown(moving: nil, savingView: nil, choosingTime: copies, sheet: .ask,
                                          planned: nil, palette: true, keys: true), .snoozeTime(copies))
        XCTAssertEqual(WindowDialog.shown(moving: nil, savingView: "from:x", choosingTime: copies, sheet: nil,
                                          planned: nil, palette: false, keys: false), .saveView("from:x"))
        XCTAssertEqual(WindowDialog.snoozeTime(copies).kind, "snooze-time")
    }

    func testASendersCardStandsOnTheScrimBeforeWhatTheDeskOpened() {
        let aoi = "aoi@example.test"
        XCTAssertEqual(WindowDialog.shown(moving: nil, savingView: nil, contact: aoi, sheet: .ask, planned: nil,
                                          palette: true, keys: true), .contact(aoi))
        XCTAssertEqual(WindowDialog.shown(moving: nil, savingView: nil, choosingTime: [DeskFixture.ada], contact: aoi,
                                          sheet: nil, planned: nil, palette: false, keys: false),
                       .snoozeTime([DeskFixture.ada]))
        XCTAssertEqual(MeadowDialogPlace.card.frame(in: CGSize(width: 1440, height: 900)).width, 420)
    }

    func testASendersCardOpensOverTheWindowAndItsCloseLetsItGo() async throws {
        let mailbox = Mailbox()
        let host = NSHostingView(rootView: Color.clear.frame(width: 1000, height: 700)
            .modifier(WindowOverlays(mailbox: mailbox))
            .meadowLight(.day))
        host.frame = CGRect(x: 0, y: 0, width: 1000, height: 700)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        mailbox.contact = "aoi@example.test"
        try await settle(host) { mailbox.typing }
        XCTAssertTrue(mailbox.typing, "the card is a dialog of the window's, whose keys wait behind it")
        XCTAssertTrue(mailbox.asking, "and the Message menu's verbs wait for it")
        mailbox.contact = nil
        try await settle(host) { !mailbox.typing }
        XCTAssertFalse(mailbox.typing)
        window.contentView = nil
    }

    func testOnlyTheDesksOwnSheetsOpenAsSheets() {
        XCTAssertTrue(Desk.Sheet.ask.floats)
        XCTAssertTrue(Desk.Sheet.plan.floats)
        XCTAssertTrue(Desk.Sheet.snoozeUntil([], canUnsnooze: true).floats)
        XCTAssertFalse(Desk.Sheet.curate.floats)
        XCTAssertFalse(Desk.Sheet.review.floats)
    }

    // MARK: The keyboard around a dialog

    /// A list's table, which takes the keyboard once it has a column.
    private func table(in window: NSWindow) -> MailboxTableView {
        let table = MailboxTableView(frame: NSRect(x: 0, y: 0, width: 300, height: 400))
        table.addTableColumn(NSTableColumn(identifier: NSUserInterfaceItemIdentifier("row")))
        window.contentView?.addSubview(table)
        return table
    }

    /// A window with the list's table in it, the table holding the keyboard, and the window's `DialogFocus`.
    private func listWindow() -> (NSWindow, MailboxTableView, DialogFocus) {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 600, height: 400), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let table = self.table(in: window)
        XCTAssertTrue(window.makeFirstResponder(table))
        XCTAssertTrue(window.firstResponder === table)
        let focus = DialogFocus()
        focus.window = window
        return (window, table, focus)
    }

    /// A dialog's field, holding the keyboard as the palette's does.
    private func field(in window: NSWindow) -> NSTextField {
        let field = NSTextField(frame: NSRect(x: 320, y: 300, width: 200, height: 22))
        window.contentView?.addSubview(field)
        XCTAssertTrue(window.makeFirstResponder(field))
        return field
    }

    func testADialogTakesTheListsKeyboardAndGivesItBackAsItGoes() {
        let (window, table, focus) = listWindow()
        focus.opened()
        XCTAssertFalse(window.firstResponder === table, "the list's arrows wait behind the scrim")
        let palette = field(in: window)
        XCTAssertTrue(DialogFocus.owner(of: window.firstResponder) === palette, "a field stands for its editor")

        focus.closing()
        palette.removeFromSuperview()
        focus.gone()
        XCTAssertTrue(window.firstResponder === table, "j and e work again at once")
    }

    func testTheKeyboardGoesBackWhileTheDialogsFieldStillHasIt() {
        let (window, table, focus) = listWindow()
        focus.opened()
        let palette = field(in: window)
        focus.closing()
        focus.gone()
        XCTAssertTrue(window.firstResponder === table, "as the dialog fades, its field gives the keyboard up")
        palette.removeFromSuperview()
    }

    func testANewListTakesTheKeyboardWhenTheOneThatHadItHasGone() {
        let (window, table, focus) = listWindow()
        focus.opened()
        let palette = field(in: window)
        focus.closing()
        palette.removeFromSuperview()
        // A command run from the palette went to another folder: its list is a table of its own.
        table.removeFromSuperview()
        let next = self.table(in: window)
        focus.gone()
        XCTAssertTrue(window.firstResponder === next)
    }

    func testWhatADialogOpenedKeepsTheKeyboard() {
        let (window, table, focus) = listWindow()
        focus.opened()
        let palette = field(in: window)
        focus.closing()
        palette.removeFromSuperview()
        // The palette's Search put the keyboard in the search field.
        let search = field(in: window)
        focus.gone()
        XCTAssertTrue(DialogFocus.owner(of: window.firstResponder) === search)
        XCTAssertFalse(window.firstResponder === table)
    }

    func testADialogGivingWayToAnotherKeepsTheKeyboardWithIt() {
        let (window, table, focus) = listWindow()
        focus.opened()
        let palette = field(in: window)
        // The palette gives way to Move To: one dialog follows another, and none has gone yet.
        palette.removeFromSuperview()
        let move = field(in: window)
        focus.gone()
        XCTAssertTrue(DialogFocus.owner(of: window.firstResponder) === move)
        focus.closing()
        move.removeFromSuperview()
        focus.gone()
        XCTAssertTrue(window.firstResponder === table)
    }

    func testTheKeyboardGoesBackToTheFieldThatHadIt() {
        let (window, _, focus) = listWindow()
        let search = field(in: window)
        focus.opened()
        XCTAssertTrue(DialogFocus.owner(of: window.firstResponder) === search, "only the list lets it go")
        let palette = field(in: window)
        focus.closing()
        palette.removeFromSuperview()
        focus.gone()
        XCTAssertTrue(DialogFocus.owner(of: window.firstResponder) === search)
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
