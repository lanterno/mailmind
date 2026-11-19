import PDFKit
import SwiftUI
import XCTest

/// Meadow's shell (#44): where each panel stands at the mockup's widths, the list's limits and the splitter's keys,
/// the rail, the drawer and focus reading, where each place stands, and how the pane's bar folds.
@MainActor
final class MeadowShellTests: XCTestCase {
    /// A suite of its own for each test, so nothing it saves reaches the app's own defaults.
    private let suite = "meadow-shell-\(UUID().uuidString)"
    private var defaults: UserDefaults { UserDefaults(suiteName: suite)! }

    override func tearDown() {
        UserDefaults().removePersistentDomain(forName: suite)
    }

    // MARK: Where the panels stand

    /// The mockup's 1440 by 900 window: the shell is 844 high under the 56 of the top bar.
    private let wide = CGSize(width: 1440, height: 844)

    func testTheColumnsWithTheRailFolded() {
        let layout = ShellLayout(size: wide, railOpen: false, listWidth: 372, mode: .columns)
        XCTAssertTrue(layout.docked)
        XCTAssertEqual(layout.rail, CGRect(x: 8, y: 0, width: 64, height: 836))
        XCTAssertEqual(layout.list, CGRect(x: 80, y: 0, width: 372, height: 836))
        XCTAssertEqual(layout.splitter, CGRect(x: 452, y: 0, width: 8, height: 836))
        // The pane the mockup's pictures show: 972 wide from 460, where its keys still show beside its words.
        XCTAssertEqual(layout.pane, CGRect(x: 460, y: 0, width: 972, height: 836))
        XCTAssertNil(layout.full)
    }

    func testTheColumnsWithTheRailOpen() {
        let layout = ShellLayout(size: wide, railOpen: true, listWidth: 372, mode: .columns)
        XCTAssertEqual(layout.rail?.width, 224)
        XCTAssertEqual(layout.list?.minX, 240)
        XCTAssertEqual(layout.pane, CGRect(x: 620, y: 0, width: 812, height: 836))
    }

    func testAPlaceOfItsOwnSpansTheListAndThePane() {
        let layout = ShellLayout(size: wide, railOpen: false, listWidth: 372, mode: .full)
        XCTAssertEqual(layout.rail?.width, 64)
        XCTAssertEqual(layout.full, CGRect(x: 80, y: 0, width: 1352, height: 836))
        XCTAssertNil(layout.list)
        XCTAssertNil(layout.pane)
        XCTAssertNil(layout.splitter)
    }

    func testFocusReadingShowsOnlyThePane() {
        let layout = ShellLayout(size: wide, railOpen: true, listWidth: 372, mode: .focus)
        XCTAssertNil(layout.rail)
        XCTAssertNil(layout.list)
        XCTAssertNil(layout.splitter)
        XCTAssertEqual(layout.pane, CGRect(x: 8, y: 0, width: 1424, height: 836))
    }

    func testUnder1100WideTheRailIsADrawer() {
        let narrow = ShellLayout(size: CGSize(width: 1100, height: 700), railOpen: true, listWidth: 372, mode: .columns)
        XCTAssertFalse(narrow.docked)
        XCTAssertNil(narrow.rail)
        XCTAssertEqual(narrow.list?.minX, 8)
        XCTAssertEqual(narrow.splitter?.minX, 380)
        XCTAssertEqual(narrow.pane, CGRect(x: 388, y: 0, width: 704, height: 692))
        XCTAssertEqual(narrow.drawer, CGRect(x: 0, y: 0, width: 290, height: 700))
        let tiny = ShellLayout(size: CGSize(width: 300, height: 700), railOpen: false, listWidth: 372, mode: .columns)
        XCTAssertEqual(tiny.drawer.width, 258, "86% of a window narrower than 290 / 0.86")
        XCTAssertTrue(ShellLayout.docks(1101))
        XCTAssertFalse(ShellLayout.docks(1100))
        let full = ShellLayout(size: CGSize(width: 1000, height: 700), railOpen: true, listWidth: 372, mode: .full)
        XCTAssertEqual(full.full, CGRect(x: 8, y: 0, width: 984, height: 692))
    }

    /// At the window's least width with the list dragged wide, and just over 1100 with the rail open, the list gives
    /// way so the pane keeps room for its bar folded as far as it goes, and everything stays inside the window.
    func testTheListGivesWaySoThePaneKeepsItsLeast() {
        let windows: [(size: CGSize, railOpen: Bool, list: CGFloat)] = [
            (CGSize(width: 900, height: 536), false, 560), (CGSize(width: 1101, height: 536), true, 560),
            (CGSize(width: 1101, height: 536), false, 560), (CGSize(width: 900, height: 536), false, 300),
        ]
        for (size, railOpen, wanted) in windows {
            let layout = ShellLayout(size: size, railOpen: railOpen, listWidth: wanted, mode: .columns)
            let said = "\(size.width) wide, the rail \(railOpen ? "open" : "folded"), the list at \(wanted)"
            let pane = layout.pane, list = layout.list
            XCTAssertGreaterThanOrEqual(pane?.width ?? 0, ShellLayout.paneLeast, said)
            XCTAssertGreaterThanOrEqual(list?.width ?? 0, ShellLayout.listMin, said)
            XCTAssertEqual(pane?.maxX, size.width - ShellLayout.gap, said + ": the pane ends inside the window")
            XCTAssertEqual(pane?.minX, (list?.maxX ?? 0) + ShellLayout.gap, said + ": beside the list, not over it")
        }
        let least = ShellLayout(size: CGSize(width: 900, height: 536), railOpen: false, listWidth: 560, mode: .columns)
        XCTAssertEqual(least.list?.width, 476, "as wide as it can be beside the pane's least")
        XCTAssertEqual(least.splitter?.minX, least.list?.maxX, "the splitter stays at the list's edge")
        XCTAssertEqual(ShellLayout(size: wide, railOpen: false, listWidth: 560, mode: .columns).list?.width, 560,
                       "a wide window keeps the width the list was dragged to")
    }

    /// Folded as far as it goes, the pane's bar fits the narrowest pane the shell leaves beside the list.
    func testThePanesBarFoldedFitsTheNarrowestPane() async throws {
        let widths = try await barWidths()
        let folded = try XCTUnwrap(widths.last)
        XCTAssertLessThanOrEqual(folded, ShellLayout.paneLeast)
    }

    /// The top bar keeps the traffic lights' room at its left in a window, not in full screen, where they leave it, and
    /// hears its window go in and come out.
    func testTheTopBarKeepsTheLightsRoomOnlyWhereTheyAre() async {
        XCTAssertEqual(MeadowTopBar.leading(lights: WindowLights.room, fullScreen: false), WindowLights.room)
        XCTAssertEqual(MeadowTopBar.leading(lights: WindowLights.room, fullScreen: true), 10)
        XCTAssertEqual(MeadowTopBar.leading(lights: 0, fullScreen: false), 10, "a picture with no window's lights")

        var heard: [Bool] = []
        let watch = FullScreenWatchView { heard.append($0) }
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 200, height: 100), styleMask: [.titled],
                              backing: .buffered, defer: true)
        window.isReleasedWhenClosed = false
        window.contentView?.addSubview(watch)
        await until { heard == [false] }
        NotificationCenter.default.post(name: NSWindow.willEnterFullScreenNotification, object: window)
        NotificationCenter.default.post(name: NSWindow.didExitFullScreenNotification, object: window)
        XCTAssertEqual(heard, [false, true, false])
        watch.removeFromSuperview()
        NotificationCenter.default.post(name: NSWindow.willEnterFullScreenNotification, object: window)
        XCTAssertEqual(heard, [false, true, false], "out of its window, it hears no more")
    }

    // MARK: The list's width

    func testTheListKeepsWithinTheSplittersLimits() {
        XCTAssertEqual(ShellLayout.clampList(200), 300)
        XCTAssertEqual(ShellLayout.clampList(900), 560)
        XCTAssertEqual(ShellLayout.clampList(401.4), 401)
        XCTAssertEqual(ShellLayout.clampList(.nan), 372)
        XCTAssertEqual(ShellLayout(size: wide, railOpen: false, listWidth: 9000, mode: .columns).list?.width, 560)
    }

    func testTheSplittersKeysAndItsDoubleClick() {
        let shell = MeadowShell(defaults: defaults)
        XCTAssertEqual(shell.listWidth, 372)
        shell.nudgeList(by: 1)
        XCTAssertEqual(shell.listWidth, 396)
        shell.nudgeList(by: -4)
        XCTAssertEqual(shell.listWidth, 300)
        shell.nudgeList(by: -1)
        XCTAssertEqual(shell.listWidth, 300, "no narrower than 300")
        shell.setListWidth(10_000)
        XCTAssertEqual(shell.listWidth, 560)
        shell.resetList()
        XCTAssertEqual(shell.listWidth, 372)
    }

    /// A window that narrows the list below the width it was dragged to: the splitter moves the list from the width
    /// shown, so its first drag or key moves it at once, and the width dragged to stays for a window wide enough.
    func testTheSplitterMovesTheListFromTheWidthShown() {
        let shell = MeadowShell(defaults: defaults)
        shell.setListWidth(560)
        shell.width = 900
        XCTAssertEqual(shell.shownListWidth, 476, "narrowed beside the pane's least")
        XCTAssertEqual(shell.shownListWidth, ShellLayout(size: CGSize(width: 900, height: 536), railOpen: false,
                                                         listWidth: 560, mode: .columns).list?.width,
                       "as the window lays it out")

        // A drag: its first move takes the list from 476, not from 560, where 10 points narrower showed no change.
        let start = shell.dragList(from: nil, by: -10)
        XCTAssertEqual(start, 476)
        XCTAssertEqual(shell.shownListWidth, 466)
        shell.dragList(from: start, by: -30)
        XCTAssertEqual(shell.shownListWidth, 446, "each move from where the drag began")
        shell.dragList(from: start, by: 200)
        XCTAssertEqual(shell.shownListWidth, 476, "no wider than the pane leaves room for")

        // A key: a step narrower than shown.
        shell.setListWidth(560)
        shell.nudgeList(by: -1)
        XCTAssertEqual(shell.listWidth, 452)
        XCTAssertEqual(shell.shownListWidth, 452)

        // Untouched, the width dragged to comes back in a window wide enough.
        shell.setListWidth(560)
        shell.width = 1440
        XCTAssertEqual(shell.shownListWidth, 560)
    }

    func testTheRailAndTheListAreRememberedOnThisMac() {
        let shell = MeadowShell(defaults: defaults)
        XCTAssertFalse(shell.railOpen, "the rail starts folded, as the mockup's does")
        shell.toggleRail()
        shell.setListWidth(450)
        let again = MeadowShell(defaults: defaults)
        XCTAssertTrue(again.railOpen)
        XCTAssertEqual(again.listWidth, 450)
        defaults.set(5000.0, forKey: MeadowShell.listKey)
        XCTAssertEqual(MeadowShell(defaults: defaults).listWidth, 560,
                       "a width saved out of bounds comes back within them")
    }

    // MARK: The rail, the drawer and focus

    func testTheSidebarButtonOpensTheRailOrTheDrawer() {
        let shell = MeadowShell(defaults: defaults)
        shell.toggleRail()
        XCTAssertTrue(shell.railOpen)
        XCTAssertFalse(shell.drawerOpen)
        shell.width = 1000
        XCTAssertFalse(shell.docked)
        shell.toggleRail()
        XCTAssertTrue(shell.drawerOpen)
        XCTAssertTrue(shell.railOpen, "a narrow window's drawer leaves the wide rail as it was")
        shell.width = 1300
        XCTAssertFalse(shell.drawerOpen, "a window grown wide shuts the drawer")
    }

    func testEscClosesTheDrawerThenLeavesFocus() {
        let shell = MeadowShell(defaults: defaults)
        XCTAssertFalse(shell.escape())
        shell.toggleFocus()
        XCTAssertTrue(shell.focus)
        XCTAssertEqual(shell.mode(spanning: false), .focus)
        XCTAssertEqual(shell.mode(spanning: true), .full, "a place of its own isn't read in focus")
        shell.width = 1000
        shell.drawerOpen = true
        XCTAssertTrue(shell.escape())
        XCTAssertFalse(shell.drawerOpen)
        XCTAssertTrue(shell.focus)
        XCTAssertTrue(shell.escape())
        XCTAssertFalse(shell.focus)
        XCTAssertEqual(shell.mode(spanning: false), .columns)
    }

    func testAskingForTheMailboxesLeavesFocus() {
        let shell = MeadowShell(defaults: defaults)
        shell.toggleFocus()
        shell.toggleRail()
        XCTAssertFalse(shell.focus)
        shell.width = 1000
        shell.drawerOpen = true
        shell.toggleFocus()
        XCTAssertTrue(shell.focus)
        XCTAssertFalse(shell.drawerOpen, "focus reading shuts the drawer")
    }

    func testWhereEachPlaceStands() {
        XCTAssertEqual(Place.desk(.brief).listed, .allInboxes, "the brief stands beside the Inbox's list")
        XCTAssertEqual(Place.desk(.ask).listed, .desk(.ask))
        XCTAssertTrue(Place.desk(.screener(account: "ada@example.test")).spansTheWindow)
        XCTAssertTrue(Place.search(Search(q: "category:receipt", lane: .papertrail)).spansTheWindow)
        XCTAssertFalse(Place.search(Search(q: "category:newsletter", lane: .feed)).spansTheWindow)
        XCTAssertFalse(Place.allInboxes.spansTheWindow)
        XCTAssertFalse(Place.desk(.brief).spansTheWindow)
    }

    // MARK: The list's head

    /// The lines a head's sentence and what follows it are drawn on, `width` points wide.
    private static func lines(_ head: some View, width: CGFloat) -> [String] {
        let data = NSMutableData()
        ImageRenderer(content: head.frame(width: width, alignment: .leading).font(.meadow(.ui, 12.5)).meadowLight(.day))
            .render { size, draw in
                var box = CGRect(origin: .zero, size: size)
                guard let consumer = CGDataConsumer(data: data as CFMutableData),
                      let pdf = CGContext(consumer: consumer, mediaBox: &box, nil) else { return }
                pdf.beginPDFPage(nil)
                draw(pdf)
                pdf.endPDFPage()
                pdf.closePDF()
            }
        return (PDFDocument(data: data as Data)?.string ?? "").split(whereSeparator: \.isNewline).map(String.init)
    }

    /// Trash's sentence and its count run on one line where they fit; in a narrow list the count takes a line of its
    /// own, and no line starts with the "·" between them.
    func testAHeadsSentenceAndCountShareALineOrTakeOneEach() throws {
        let about = try XCTUnwrap(MeadowListHead.about(.folder(account: Fixture.ada, folder: "Trash"), role: "trash"))
        let head = MeadowHeadLine(sentence: about) { Text("3 conversations") }
        let wide = Self.lines(head, width: 900)
        XCTAssertEqual(wide.count, 1, "\(wide)")
        XCTAssertTrue(wide[0].contains("·"), "\(wide)")
        let narrow = Self.lines(head, width: 340)
        XCTAssertGreaterThan(narrow.count, 1, "\(narrow)")
        XCTAssertEqual(narrow.last, "3 conversations", "the count on a line of its own")
        XCTAssertFalse(narrow.contains { $0.hasPrefix("·") }, "\(narrow)")
        XCTAssertFalse(narrow.joined().contains("·"))
    }

    /// The Feed's sentence and its scope: beside each other where they fit, else the scope under the sentence, never
    /// squeezed beside a sentence wrapped to two lines.
    func testALanesScopeStandsUnderItsSentenceWhereItDoesntFitBeside() {
        let sentence = "Newsletters and digests. Nothing here asks anything of you."
        let head = MeadowHeadLine(sentence: sentence) { Text("all accounts") }
        XCTAssertEqual(Self.lines(head, width: 700).count, 1)
        let narrow = Self.lines(head, width: 340)
        XCTAssertEqual(narrow.last, "all accounts", "\(narrow)")
        XCTAssertFalse(narrow.dropLast().contains { $0.contains("all accounts") }, "\(narrow)")
    }

    // MARK: The pane's bar

    func testThePanesBarFoldsAsThePaneNarrows() {
        // What shows at each width: the keys, Later's, All's and Forward's words, every word, the second buttons.
        func shown(_ width: CGFloat) -> [Bool] {
            let fold = PaneBarFold(width: width)
            return [fold.keys, fold.secondLabels, fold.labels, fold.secondary]
        }
        XCTAssertEqual(shown(981), [true, true, true, true])
        XCTAssertEqual(shown(972), [false, true, true, true], "the mockup's pane beside the folded rail")
        XCTAssertEqual(shown(812), [false, false, true, true], "beside the open rail")
        XCTAssertEqual(shown(720), [false, false, false, true])
        XCTAssertEqual(shown(481), [false, false, false, true])
        XCTAssertEqual(shown(480), [false, false, false, false])
    }

    func testThePanesBarGivesThingsUpInTurnUntilItFits() {
        func shown(_ folds: [PaneBarFold]) -> [[Bool]] {
            folds.map { [$0.keys, $0.secondLabels, $0.labels, $0.secondary] }
        }
        XCTAssertEqual(shown(PaneBarFold(width: 1100).further), [
            [true, true, true, true], [false, true, true, true], [false, false, true, true],
            [false, false, false, true], [false, false, false, false],
        ], "the keys, the second words, every word, then the second buttons")
        XCTAssertEqual(PaneBarFold(width: 600).further, [PaneBarFold(width: 600), .folded],
                       "from icons, only the second buttons are left to give up")
        XCTAssertEqual(PaneBarFold(width: 300).further, [.folded])
    }

    /// Measured in Meadow's faces over an open message, each further fold of the bar is narrower than the one before,
    /// so the first that fits is the widest that does.
    func testEachFoldOfThePanesBarIsNarrowerThanTheOneBefore() async throws {
        let widths = try await barWidths()
        for (wider, narrower) in zip(widths, widths.dropFirst()) {
            XCTAssertLessThan(narrower, wider)
        }
    }

    /// The pane's bar over an open message, as wide as each of its folds from the widest asks.
    private func barWidths() async throws -> [CGFloat] {
        _ = MeadowFonts.register()
        let app = MailApp()
        app.mailbox.wire = Fixture.server().wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.openList()
        await until { app.list?.copy(at: 0) != nil }
        app.mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
        func width(_ fold: PaneBarFold) -> CGFloat {
            NSHostingView(rootView: MeadowPaneBar(app: app, mailbox: app.mailbox,
                                                  writing: PaneWriting(replyTo: app.selection.copy), only: fold)
                .meadowLight(.day)).fittingSize.width
        }
        return PaneBarFold(width: 1100).further.map(width)
    }

    /// The pane's width, which a test changes under the bar.
    @MainActor
    @Observable
    final class PaneWidth {
        var value: CGFloat = 1100
    }

    /// The pane's bar as wide as `width` says, as the frame hands the pane its width.
    private struct BarAt: View {
        let width: PaneWidth
        let app: MailApp

        var body: some View {
            MeadowPaneBar(app: app, mailbox: app.mailbox, writing: PaneWriting(replyTo: app.selection.copy))
                .frame(width: width.value)
                .environment(\.meadowPaneWidth, width.value)
        }
    }

    /// The pane's Snooze says where it stands however the bar folds, wider or narrower, so h hangs its chooser under
    /// it and not in the middle of the window: a fold that goes never takes the place of the one that came.
    func testThePanesSnoozeSaysWhereItStandsHoweverTheBarFolds() async throws {
        _ = MeadowFonts.register()
        let app = MailApp()
        app.mailbox.wire = Fixture.server().wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.openList()
        await until { app.list?.copy(at: 0) != nil }
        app.mailbox.chose([try XCTUnwrap(app.list?.copy(at: 0))])
        let anchors = MeadowAnchors()
        let width = PaneWidth()
        let host = NSHostingView(rootView: BarAt(width: width, app: app)
            .environment(\.meadowAnchors, anchors)
            .meadowLight(.day))
        host.frame = NSRect(x: 0, y: 0, width: 1100, height: 70)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        await until { anchors.frames["snooze"] != nil }
        for wide in [CGFloat(760), 520, 400, 1100, 600, 420, 1000] {
            width.value = wide
            host.frame.size.width = wide
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(40))
            host.layoutSubtreeIfNeeded()
            let snooze = try XCTUnwrap(anchors.frames["snooze"], "at \(wide), the pane's Snooze says where it is")
            XCTAssertGreaterThan(snooze.width, 0, "at \(wide)")
            XCTAssertLessThanOrEqual(snooze.maxX, wide + 1, "at \(wide), inside the bar")
        }
        window.contentView = nil
    }

    /// A brief card's Snooze says where it stands, and the chooser for its message hangs there, not under the clock
    /// of the message's row in the list beside the brief; once the card has gone, under the row's clock again.
    func testABriefCardsSnoozeHangsTheChooserUnderItself() async throws {
        _ = MeadowFonts.register()
        let link = CoreLink(DeskRoutes())
        let notices = DeskNotices()
        let item = DeskFixture.item(140, from: "grace@example.test", name: "Grace Hopper", subject: "Notes from review")
        let copy = BriefModel.copy(of: item)
        let anchors = MeadowAnchors()
        let row = CGRect(x: 300, y: 200, width: 20, height: 20)
        anchors.rowClock = { $0 == copy ? row : nil }
        let defaults = try XCTUnwrap(UserDefaults(suiteName: "mm-card-snooze-\(UUID().uuidString)"))
        let card = NeedCard(item: item, model: BriefModel(link: link, notices: notices, defaults: defaults),
                            attention: AttentionModel(link: link, notices: notices), actions: BriefActions())
        let host = NSHostingView(rootView: card.frame(width: 640).environment(\.meadowAnchors, anchors).meadowLight(.day))
        host.frame = NSRect(x: 0, y: 0, width: 640, height: 200)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        await until { anchors.frames[MeadowAnchors.cardSnooze(copy)] != nil }
        let hung = try XCTUnwrap(anchors.snooze(for: [copy], open: nil))
        XCTAssertNotEqual(hung, row, "the card's own Snooze, not the row's clock")
        XCTAssertGreaterThan(hung.minX, 320, "on the card's right, where its Snooze stands")
        XCTAssertEqual(anchors.snooze(for: [copy, BriefModel.copy(of: DeskFixture.item(141, from: "a@example.test",
                                                                                      name: "A", subject: "B"))],
                                      open: nil), row, "more than its one message: the first row's clock")
        window.contentView = nil
        await until { anchors.frames[MeadowAnchors.cardSnooze(copy)] == nil }
        XCTAssertEqual(anchors.snooze(for: [copy], open: nil), row, "the card gone, the row's clock")
    }
}
