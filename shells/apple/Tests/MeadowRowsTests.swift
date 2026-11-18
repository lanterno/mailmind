import AppKit
import XCTest

/// The rows' shapes and words, the day heads, the brief's row and the table's brief row and heights: what the
/// mockup's `.row` and `rowHTML()` decide, on fictional mail.
@MainActor
final class MeadowRowsTests: XCTestCase {
    // MARK: How tall a row is

    func testEachShapeIsAsTallAsThePageLaysItOut() {
        let comfortable = RowMeasure(.comfortable)
        // 10 + 20.3 + 2 + 20.3 + 4 + 2 × 19.575 + 7 + 22 + 11
        XCTAssertEqual(comfortable.height(RowShape(gist: true, chips: true)), 136)
        XCTAssertEqual(comfortable.height(RowShape(gist: true)), 107)
        // 10 + 20.3 + 2 + 20.3 + 3 + 18.85 + 7 + 22 + 11
        XCTAssertEqual(comfortable.height(RowShape(chips: true)), 114)
        XCTAssertEqual(comfortable.height(RowShape()), 85)
        // A day's head is 31 over the row.
        XCTAssertEqual(comfortable.height(RowShape(head: true, gist: true)), 138)
    }

    func testDensitiesTightenTheRowsAndCompactDropsTheChips() {
        let cozy = RowMeasure(.cozy), compact = RowMeasure(.compact)
        XCTAssertEqual(cozy.padTop, 7)
        XCTAssertEqual(cozy.gistLine, 13.5 * 1.35, accuracy: 0.001)
        XCTAssertLessThan(cozy.height(RowShape(gist: true, chips: true)), 136)
        XCTAssertEqual(compact.gistLines, 1)
        XCTAssertEqual(compact.height(RowShape(gist: true, chips: true)), compact.height(RowShape(gist: true)))
        XCTAssertNil(compact.stops(RowShape(chips: true)).chips)
        // Never shorter than the avatar and its padding.
        XCTAssertGreaterThanOrEqual(compact.height(RowShape()), 4 + 34 + 5)
    }

    func testAShapePacksIntoAByte() {
        for head in [false, true] {
            for gist in [false, true] {
                for chips in [false, true] {
                    for oneLine in [false, true] {
                        let shape = RowShape(head: head, gist: gist, chips: chips, oneLine: oneLine)
                        XCTAssertEqual(RowShape(packed: shape.packed), shape)
                    }
                }
            }
        }
        XCTAssertNil(RowShape(packed: 0), "zero is a row not worked out yet")
    }

    func testASummaryThatFitsOnOneLineTakesOne() {
        let comfortable = RowMeasure(.comfortable), cozy = RowMeasure(.cozy), compact = RowMeasure(.compact)
        // 10 + 20.3 + 2 + 20.3 + 4 + 19.575 + 7 + 22 + 11: the chips right under the one line.
        XCTAssertEqual(comfortable.height(RowShape(gist: true, chips: true, oneLine: true)), 116)
        XCTAssertEqual(comfortable.height(RowShape(head: true, gist: true, oneLine: true)), 31 + 87)
        XCTAssertEqual(cozy.height(RowShape(gist: true, oneLine: true)),
                       cozy.height(RowShape(gist: true)) - (13.5 * 1.35).rounded(), accuracy: 1)
        XCTAssertEqual(compact.height(RowShape(gist: true, oneLine: true)), compact.height(RowShape(gist: true)),
                       "compact shows one line of any summary")
        XCTAssertFalse(RowShape(oneLine: true).oneLine, "only a summary has lines")

        // The summary has the row's width less the list's sides, the avatar and the padding.
        XCTAssertEqual(RowMeasure.room(420), 420 - 12 - 55 - 10)
        XCTAssertTrue(RowMeasure.fits(343, in: 420))
        XCTAssertFalse(RowMeasure.fits(343.5, in: 420))
        let short = RowFaces.gistWidth("Stub summary."), long = RowFaces.gistWidth(Self.middle)
        XCTAssertTrue(RowMeasure.fits(short, in: 300))
        XCTAssertTrue(RowMeasure.fits(long, in: 420))
        XCTAssertFalse(RowMeasure.fits(long, in: 300))
        let row = Fixture.row(1, action: "Stub action", summary: "Stub summary.")
        XCTAssertEqual(RowShape.of(row, head: false, rowWidth: 420).shape,
                       RowShape(gist: true, chips: true, oneLine: true))
        XCTAssertEqual(RowShape.of(row, head: false, rowWidth: 420).gistWidth, short)
        let snippet = Fixture.row(2)
        XCTAssertNil(RowShape.of(snippet, head: false, rowWidth: 420).gistWidth, "a snippet has one line anyway")
    }

    /// A summary that fits on one line of a row 420 wide, and needs two in a row 300 wide.
    private nonisolated static let middle = "Asks which hike you'd like on Saturday morning."

    func testARowDrawsItsSummaryInTheLinesItsHeightMadeRoomFor() throws {
        let inks = RowInks.of(.day, light: .day, highContrast: false)
        // The owner's row: a summary of a few words, then a next step's chip, under a day's head and not.
        let row = Fixture.row(1, action: "Stub action", summary: "Stub summary.")
        for density in [API.Density.comfortable, .cozy] {
            for head in [nil, "Today"] {
                let measure = RowMeasure(density)
                let shape = RowShape.of(row, head: head != nil, rowWidth: 420).shape
                XCTAssertTrue(shape.oneLine)
                let stops = measure.stops(shape)
                let chips = try XCTUnwrap(stops.chips)
                let cell = MailRowCell()
                cell.frame = NSRect(x: 0, y: 0, width: 420, height: stops.height)
                cell.show(row, head: head, acts: [], look: RowLook(inks: inks, measure: measure, accounts: []))
                // The chip's wash, by its left end, stands where the one line's height puts it.
                let x = RowMeasure.listSide + RowMeasure.textX + 4
                XCTAssertTrue(near(try colour(cell, x: x, y: chips + 11), inks.sunWash),
                              "\(density) \(head ?? "no head"): the chip right under the summary's one line")
                XCTAssertFalse(near(try colour(cell, x: x, y: chips - 4), inks.sunWash),
                               "\(density) \(head ?? "no head"): nothing of the chip above it")
            }
        }
    }

    /// The colour `cell` draws at `x`, `y`, as the screen would show it.
    private func colour(_ cell: NSView, x: CGFloat, y: CGFloat) throws -> NSColor {
        let rep = try XCTUnwrap(cell.bitmapImageRepForCachingDisplay(in: cell.bounds))
        cell.cacheDisplay(in: cell.bounds, to: rep)
        let scale = CGFloat(rep.pixelsWide) / cell.bounds.width
        let colour = try XCTUnwrap(rep.colorAt(x: Int(x * scale), y: Int(y * scale)))
        return try XCTUnwrap(colour.usingColorSpace(.sRGB))
    }

    private func near(_ a: NSColor, _ b: NSColor) -> Bool {
        guard let b = b.usingColorSpace(.sRGB) else { return false }
        return abs(a.redComponent - b.redComponent) + abs(a.greenComponent - b.greenComponent)
            + abs(a.blueComponent - b.blueComponent) < 0.06
    }

    // MARK: What a row says

    func testTheThirdLineIsTheSummaryTheAmountOrTheSnippet() {
        var row = Fixture.row(1, summary: "Ada asks to move the launch.")
        XCTAssertEqual(RowWords.third(row), .gist("Ada asks to move the launch."))
        row = Fixture.row(2)
        XCTAssertEqual(RowWords.third(row), .preview("A line of fictional mail, number 2."))
        row.bucket = "receipts/transactional"
        row.snippet = "Order 7 · Total $48.00"
        guard case .amount(let money, let snippet) = RowWords.third(row) else { return XCTFail("no amount") }
        XCTAssertTrue(money.hasSuffix("48.00"), money)
        XCTAssertEqual(snippet, "Order 7 · Total $48.00")
        XCTAssertTrue(RowShape(row, head: false) == RowShape(), "an amount takes the snippet's one line")
    }

    func testChipsAreTheNextStepInSunlightAndAJunkTag() {
        let needs = Fixture.row(1, action: "Reply by Friday")
        XCTAssertEqual(RowWords.chips(needs), [RowWords.Chip(text: "Reply by Friday", tone: .sun)])
        XCTAssertTrue(RowWords.needs(needs))
        let junk = Fixture.row(2, junk: true)
        XCTAssertEqual(RowWords.chips(junk), [RowWords.Chip(text: "promo", tone: .plain)])
        XCTAssertFalse(RowWords.needs(junk))
        XCTAssertEqual(RowWords.chips(Fixture.row(3)), [])
    }

    func testInitialsComeFromTheNameElseTheAddress() {
        XCTAssertEqual(RowWords.initials(Fixture.row(1, from: "Ren Takeda")), "RT")
        var row = Fixture.row(2)
        row.fromName = nil
        row.fromAddr = "news@example.test"
        XCTAssertEqual(RowWords.initials(row), "N")
    }

    func testARowsButtonsFollowItsFolder() {
        XCTAssertEqual(RowAct.shown(role: "trash"), [.restore])
        XCTAssertEqual(RowAct.shown(role: "archive"), [.toInbox, .delete, .snooze])
        XCTAssertEqual(RowAct.shown(role: "inbox"), [.file, .delete, .snooze])
        XCTAssertEqual(RowAct.shown(role: nil), [.file, .delete, .snooze])
    }

    // MARK: Days

    func testDaysAreTodayYesterdayThePastWeekAndEarlier() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try XCTUnwrap(TimeZone(identifier: "UTC"))
        let now = Date(timeIntervalSince1970: 1_790_000_000)  // a Tuesday afternoon, UTC
        let ts = Int(now.timeIntervalSince1970)
        XCTAssertEqual(DayGroup.of(ts - 60, now: now, calendar: calendar), "Today")
        XCTAssertEqual(DayGroup.of(ts - 86_400, now: now, calendar: calendar), "Yesterday")
        XCTAssertEqual(DayGroup.of(ts - 3 * 86_400, now: now, calendar: calendar), "Past week")
        XCTAssertEqual(DayGroup.of(ts - 9 * 86_400, now: now, calendar: calendar), "Earlier")
    }

    func testAHeadStandsOverEachDaysFirstRowOnly() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try XCTUnwrap(TimeZone(identifier: "UTC"))
        let now = Date(timeIntervalSince1970: 1_790_000_000)
        var first = Fixture.row(1), second = Fixture.row(2), older = Fixture.row(3)
        first.ts = 1_790_000_000 - 60
        second.ts = 1_790_000_000 - 120
        older.ts = 1_790_000_000 - 86_400
        XCTAssertEqual(DayGroup.head(first, after: .none, now: now, calendar: calendar), "Today")
        XCTAssertNil(DayGroup.head(second, after: .some(first), now: now, calendar: calendar))
        XCTAssertEqual(DayGroup.head(older, after: .some(second), now: now, calendar: calendar), "Yesterday")
        XCTAssertNil(DayGroup.head(older, after: .some(nil), now: now, calendar: calendar),
                     "the row before isn't read yet")
        XCTAssertNil(DayGroup.head(first, after: .some(older), now: now, calendar: calendar), "not newest first")
        var search = first
        search.ts = nil
        XCTAssertNil(DayGroup.head(search, after: .none, now: now, calendar: calendar))
        XCTAssertTrue(DayGroup.sections(.allInboxes))
        XCTAssertTrue(DayGroup.sections(.folder(account: Fixture.ada, folder: "INBOX")))
        XCTAssertFalse(DayGroup.sections(.virtual(.snoozed, account: nil)))
        XCTAssertFalse(DayGroup.sections(.search(Search(q: "from:ada"))))
    }

    // MARK: The brief's row

    func testTheBriefsRowTalliesAndNamesWhoNeedsYou() throws {
        let needs = BriefWords.needsYou(DeskFixture.digest().items)
        let line = BriefLine(needs: needs, waiting: 1, canWait: 5, now: Date(timeIntervalSince1970: 1_790_000_000),
                             locale: Locale(identifier: "en_GB"))
        XCTAssertEqual(line.tally, "3 need you · 1 waiting · 5 can wait")
        XCTAssertEqual(line.gist, "Grace, Alan and Riverside need you.")
        XCTAssertTrue(line.needs)
        XCTAssertEqual(BriefLine.who([]), "Nothing needs you. The rest can wait.")
        XCTAssertEqual(BriefLine.who(Array(needs.prefix(1))), "Grace needs you.")
        let more = needs + [DeskFixture.item(110, from: "mira@example.test", name: "Mira Okafor", subject: "Signs")]
        XCTAssertEqual(BriefLine.who(more), "Grace, Alan and 2 others need you.")
    }

    // MARK: The table

    private func table(rows: [API.FolderRow] = Fixture.inbox, brief: BriefLine?) async throws
        -> (MailApp, MailListModel, MailboxTableController) {
        let app = MailApp()
        app.mailbox.wire = Fixture.server(rows: rows).wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.openList()
        let list = try XCTUnwrap(app.list)
        await until { list.copy(at: 0) != nil }
        let look = RowLook(inks: RowInks.of(.day, light: .day, highContrast: false), measure: RowMeasure(.comfortable),
                           accounts: app.mailbox.accounts)
        let controller = MailboxTableController(list: list, mailbox: app.mailbox, look: look, sections: true)
        controller.setBrief(brief, chosen: false)
        controller.scrollView.frame = NSRect(x: 0, y: 0, width: 420, height: 800)
        controller.table.reloadData()
        return (app, list, controller)
    }

    func testTheBriefsRowComesFirstAndOpensTheBrief() async throws {
        let brief = BriefLine(needs: [], waiting: 0, canWait: 0)
        let (app, list, controller) = try await table(brief: brief)
        let table = controller.table
        XCTAssertEqual(table.numberOfRows, list.total + 1)
        XCTAssertNil(controller.position(0))
        XCTAssertEqual(controller.position(1), 0)
        // "Nothing needs you. The rest can wait." takes one line.
        XCTAssertEqual(table.rect(ofRow: 0).height,
                       RowMeasure(.comfortable).height(RowShape(gist: true, oneLine: true)))
        // The first mail row starts a day and needs you: a head, the summary on one line and its chip.
        XCTAssertEqual(table.rect(ofRow: 1).height, RowMeasure(.comfortable).height(
            RowShape(head: true, gist: true, chips: true, oneLine: true)))
        // Choosing the brief's row alone opens the brief and chooses no mail; with mail, it is left out.
        XCTAssertEqual(controller.tableView(table, selectionIndexesForProposedSelection: IndexSet(integer: 0)), [])
        XCTAssertEqual(app.place, .desk(.brief))
        XCTAssertEqual(controller.tableView(table, selectionIndexesForProposedSelection: IndexSet([0, 1, 2])),
                       IndexSet([1, 2]))
    }

    func testTheBriefBesideAnAccountsInboxKeepsItsList() async throws {
        let app = MailApp()
        app.mailbox.wire = Fixture.server().wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        let work = Place.folder(account: Fixture.work, folder: "INBOX")
        app.place = work
        await until { app.list?.copy(at: 0) != nil }
        let list = try XCTUnwrap(app.list)
        app.place = .desk(.brief)
        XCTAssertEqual(app.listed, work)
        XCTAssertTrue(app.list === list, "the list, and where it was scrolled, stay as they were")
        app.mailbox.chose([try XCTUnwrap(list.copy(at: 2))])
        XCTAssertEqual(app.place, work, "a row chosen beside the brief goes back to the Inbox it is in")
        app.place = .folder(account: Fixture.work, folder: "[Gmail]/Sent Mail")
        app.place = .desk(.brief)
        XCTAssertEqual(app.listed, .allInboxes, "from any other list the brief stands beside every Inbox")
    }

    func testArrowingOntoTheBriefsRowShowsItAndOnlyAClickOrReturnOpensIt() async throws {
        let brief = BriefLine(needs: [], waiting: 0, canWait: 0)
        let (app, _, controller) = try await table(brief: brief)
        let window = shown(controller)
        defer { window.close() }
        let table = controller.table
        var opened: [Bool] = []
        app.mailbox.opensBrief = { deliberately in
            opened.append(deliberately)
            app.place = .desk(.brief)
            controller.setBrief(brief, chosen: true)
        }
        func key(_ characters: String, _ code: UInt16, _ flags: NSEvent.ModifierFlags = []) throws -> NSEvent {
            try XCTUnwrap(NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: flags, timestamp: 0,
                                           windowNumber: window.windowNumber, context: nil, characters: characters,
                                           charactersIgnoringModifiers: characters, isARepeat: false, keyCode: code))
        }
        // A click proposes the brief's row alone, with no key pressed.
        _ = controller.tableView(table, selectionIndexesForProposedSelection: IndexSet(integer: 0))
        XCTAssertEqual(opened, [true])

        table.selectRowIndexes(IndexSet(integer: 1), byExtendingSelection: false)
        table.keyDown(with: try key("\u{F700}", 126, [.numericPad, .function]))
        XCTAssertEqual(opened, [true, false], "↑ onto the brief's row only shows it")
        table.keyDown(with: try key("\r", 36))
        XCTAssertEqual(opened, [true, false, true], "Return opens it")
    }

    func testChoosingARowTellsTheMailboxItsMail() async throws {
        let (app, list, controller) = try await table(brief: BriefLine(needs: [], waiting: 0, canWait: 0))
        controller.table.selectRowIndexes(IndexSet(integer: 2), byExtendingSelection: false)
        XCTAssertEqual(app.mailbox.chosenRows.map(MailCopy.init), [MailCopy(try XCTUnwrap(list.copy(at: 1)))])
        controller.press(.check, row: 3)
        XCTAssertEqual(app.mailbox.chosenRows.count, 2, "a row's check box adds it to the rows chosen")
        controller.press(.check, row: 3)
        XCTAssertEqual(app.mailbox.chosenRows.count, 1, "and takes it out again")
    }

    func testWithoutTheBriefRowsArePositions() async throws {
        let (_, list, controller) = try await table(brief: nil)
        XCTAssertEqual(controller.table.numberOfRows, list.total)
        XCTAssertEqual(controller.position(0), 0)
    }

    /// A draft waiting to go later says when in its time's place: a draft's row only, and only while it waits.
    func testADraftWaitingToGoLaterSaysWhenInItsTimesPlace() async throws {
        let rows = [Fixture.row(1, folder: "Drafts", subject: "Second try", when: "just now"),
                    Fixture.row(2, folder: "INBOX", subject: "Second try", when: "10:42"),
                    Fixture.row(3, folder: "Drafts", subject: "Half written", when: "4h ago")]
        let (app, _, controller) = try await table(rows: rows, brief: nil)
        app.mailbox.draftWhen = { $0.subject == "Second try" ? "Scheduled · tomorrow 9:00" : nil }
        let window = shown(controller)
        defer { window.close() }
        defer { withExtendedLifetime(app) {} }
        controller.redrawShown()
        func when(_ row: Int) throws -> String? {
            let view = controller.table.view(atColumn: 0, row: row, makeIfNecessary: false)
            return try XCTUnwrap(view as? MailRowCell).row?.when
        }
        XCTAssertEqual(try when(0), "Scheduled · tomorrow 9:00")
        XCTAssertEqual(try when(1), "10:42", "a message with the same subject elsewhere is another")
        XCTAssertEqual(try when(2), "4h ago")
        app.mailbox.draftWhen = nil
        controller.redrawShown()
        XCTAssertEqual(try when(0), "just now", "gone or cancelled, it says when it was saved")
    }

    func testARowSaysItsAccountWhereTheListHoldsSeveral() async throws {
        let (app, _, controller) = try await table(brief: nil)
        let window = shown(controller)
        defer { window.close() }
        // The mailbox names the accounts, and the table holds it weakly: the window's app keeps it.
        defer { withExtendedLifetime(app) {} }
        func said(_ row: Int) throws -> String {
            let view = controller.table.view(atColumn: 0, row: row, makeIfNecessary: false)
            let cell = try XCTUnwrap(view as? MailRowCell)
            return try XCTUnwrap(cell.accessibilityLabel())
        }
        let first = try said(0), second = try said(1)
        XCTAssertTrue(first.contains("Account: Ada"), first)
        XCTAssertTrue(second.contains("Account: work"), "its account is only a coloured dot on screen")
        controller.setShowsAccount(false)
        let alone = try said(0)
        XCTAssertFalse(alone.contains("Account"), "one account's own folder says none")
    }

    func testARowAfterAPageThatCameLateDrawsTheHeadItsHeightMadeRoomFor() async throws {
        let now = Int(Date().timeIntervalSince1970)
        // The first page's mail is from the past week, the rest a month old: row 200 starts "Earlier".
        let rows = (0..<650).map { index in
            var row = Fixture.row(index + 1)
            row.ts = now - (index < MailListModel.pageSize ? 3 : 30) * 86_400 - index
            return row
        }
        let gate = PageGate()
        let list = MailListModel(load: { offset, limit in
            while offset == 0, gate.shut { try await Task.sleep(for: .milliseconds(5)) }
            return MailListModel.Page(total: rows.count, rows: Array(rows.dropFirst(offset).prefix(limit)))
        })
        list.start()
        await until { list.copy(at: 0) != nil }
        let app = MailApp()
        let look = RowLook(inks: RowInks.of(.day, light: .day, highContrast: false), measure: RowMeasure(.comfortable),
                           accounts: [Fixture.ada])
        let controller = MailboxTableController(list: list, mailbox: app.mailbox, look: look, sections: true)
        let window = shown(controller)
        defer { window.close() }
        let table = controller.table

        // Down at the foot the first page is let go; back up, the page under it comes before it does.
        gate.shut = true
        table.scrollRowToVisible(649)
        await until { list.copy(at: 640) != nil && list.copy(at: 0) == nil }
        table.scrollRowToVisible(200)
        await until { list.copy(at: 200) != nil }
        table.layoutSubtreeIfNeeded()
        let before = try XCTUnwrap(table.view(atColumn: 0, row: 200, makeIfNecessary: false) as? MailRowCell)
        XCTAssertNil(before.head, "the row before it isn't here yet")
        gate.shut = false
        await until { list.copy(at: 199) != nil }
        table.layoutSubtreeIfNeeded()

        let cell = try XCTUnwrap(table.view(atColumn: 0, row: 200, makeIfNecessary: false) as? MailRowCell)
        XCTAssertEqual(table.rect(ofRow: 200).height, RowMeasure(.comfortable).height(RowShape(head: true)))
        XCTAssertEqual(cell.head, "Earlier", "it draws the head its height made room for")
        withExtendedLifetime(app) {}
    }

    func testNewMailAboveMeasuresAgainOnlyTheRowsWhoseHeightCanChange() async throws {
        let now = Int(Date().timeIntervalSince1970)
        let total = 100_000
        let above = Locked(0)
        // Three of today's, then mail from days ago: the fourth row starts a day, until new mail comes above it.
        let list = MailListModel(load: { offset, limit in
            let added = above.value
            let rows = (offset..<min(offset + limit, total + added)).map { position in
                let index = position - added
                var row = Fixture.row(index < 0 ? 500_000 + position : index + 1)
                row.ts = index < 3 ? now - 60 - index : now - 3 * 86_400 - index
                return row
            }
            return MailListModel.Page(total: total + added, rows: rows)
        })
        list.start()
        await until { list.copy(at: 0) != nil }
        let app = MailApp()
        let look = RowLook(inks: RowInks.of(.day, light: .day, highContrast: false), measure: RowMeasure(.comfortable),
                           accounts: [Fixture.ada])
        let controller = MailboxTableController(list: list, mailbox: app.mailbox, look: look, sections: true)
        let window = shown(controller)
        defer { window.close() }
        let table = controller.table
        func height(_ position: Int, head: Bool) throws -> CGFloat {
            RowMeasure(.comfortable).height(RowShape(try XCTUnwrap(list.copy(at: position)), head: head))
        }
        let started = try height(3, head: true)
        XCTAssertEqual(table.rect(ofRow: 3).height, started)

        let noted = try await NotedHeights.during {
            above.value = 1
            list.refresh()
            // Each page held is read again at once, and any may come first: the total is new with the first, but the
            // rows measured here are the first page's, so wait for the new mail at its top.
            await until { list.total == total + 1 && list.copy(at: 0)?.key == 500_000 }
            table.layoutSubtreeIfNeeded()
        }
        XCTAssertLessThan(noted.count, 2_000, "the rows measured, not the folder's 100,000")
        let today = try height(3, head: false), days = try height(4, head: true)
        XCTAssertEqual(table.rect(ofRow: 3).height, today, "today's third row now, under no head")
        XCTAssertEqual(table.rect(ofRow: 4).height, days, "the row that starts the days before")
        XCTAssertEqual(table.numberOfRows, total + 1)
        withExtendedLifetime(app) {}
    }

    func testANewWidthMeasuresAgainOnlyTheRowsWhoseSummaryChangesLines() async throws {
        let total = 100_000
        // A short summary, then one that fits on one line 420 wide and needs two 300 wide, all the way down.
        let list = MailListModel(load: { offset, limit in
            MailListModel.Page(total: total, rows: (offset..<min(offset + limit, total)).map { position in
                Fixture.row(position + 1, summary: position % 2 == 0 ? "Stub summary." : Self.middle)
            })
        })
        list.start()
        await until { list.copy(at: 0) != nil }
        let app = MailApp()
        let measure = RowMeasure(.comfortable)
        let look = RowLook(inks: RowInks.of(.day, light: .day, highContrast: false), measure: measure,
                           accounts: [Fixture.ada])
        let controller = MailboxTableController(list: list, mailbox: app.mailbox, look: look, sections: false)
        let window = shown(controller)
        defer { window.close() }
        let table = controller.table
        let one = measure.height(RowShape(gist: true, oneLine: true)), two = measure.height(RowShape(gist: true))
        XCTAssertEqual(table.rect(ofRow: 0).height, one)
        XCTAssertEqual(table.rect(ofRow: 1).height, one)
        let cell = try XCTUnwrap(table.view(atColumn: 0, row: 1, makeIfNecessary: false) as? MailRowCell)
        cell.display()

        // The splitter moves the list narrower: the longer summaries take two lines, and only theirs are measured.
        let noted = await NotedHeights.during {
            window.setContentSize(NSSize(width: 300, height: 800))
            table.layoutSubtreeIfNeeded()
        }
        XCTAssertEqual(table.rect(ofRow: 0).height, one)
        XCTAssertEqual(table.rect(ofRow: 1).height, two)
        XCTAssertFalse(noted.isEmpty)
        XCTAssertLessThan(noted.count, 2_000, "the rows worked out, not the folder's 100,000")
        XCTAssertTrue(noted.allSatisfy { $0 % 2 == 1 }, "only the rows whose summary changed lines")
        XCTAssertEqual(cell.layer?.needsDisplay(), true, "a row on screen draws again at its new width")

        // A row not worked out yet comes at the new width.
        table.scrollRowToVisible(5_001)
        await until { list.copy(at: 5_001) != nil }
        table.layoutSubtreeIfNeeded()
        XCTAssertEqual(table.rect(ofRow: 5_001).height, two)
        XCTAssertEqual(table.rect(ofRow: 5_000).height, one)

        // And wider again, back to one line.
        window.setContentSize(NSSize(width: 420, height: 800))
        table.layoutSubtreeIfNeeded()
        XCTAssertEqual(table.rect(ofRow: 1).height, one)
        XCTAssertEqual(table.rect(ofRow: 5_001).height, one)
        withExtendedLifetime(app) {}
    }

    func testTheBriefsRowTakesTheLinesWhoNeedsYouTakes() async throws {
        let (_, _, controller) = try await table(brief: BriefLine(needs: [], waiting: 0, canWait: 0))
        let window = shown(controller)
        defer { window.close() }
        let table = controller.table
        let measure = RowMeasure(.comfortable)
        XCTAssertEqual(table.rect(ofRow: 0).height, measure.height(RowShape(gist: true, oneLine: true)))
        // Who needs you grows past one line: the row grows with it.
        let names = ["Bartholomew-Maximilian", "Wilhelmina-Josephine", "Christabel-Evangeline"]
        let needs = names.enumerated().map { index, name in
            DeskFixture.item(200 + index, from: "p\(index)@example.test", name: "\(name) Example", subject: "Signs")
        }
        let line = BriefLine(needs: needs, waiting: 0, canWait: 0)
        XCTAssertFalse(RowMeasure.fits(RowFaces.gistWidth(line.gist), in: 420), line.gist)
        controller.setBrief(line, chosen: false)
        XCTAssertEqual(table.rect(ofRow: 0).height, measure.height(RowShape(gist: true)))
        let cell = try XCTUnwrap(table.view(atColumn: 0, row: 0, makeIfNecessary: false) as? BriefRowCell)
        cell.display()
        window.setContentSize(NSSize(width: 900, height: 800))
        table.layoutSubtreeIfNeeded()
        XCTAssertEqual(table.rect(ofRow: 0).height, measure.height(RowShape(gist: true, oneLine: true)),
                       "wider, it takes one")
        XCTAssertEqual(cell.layer?.needsDisplay(), true)
    }

    func testARowsDotTakesItsAccountsColourOnceTheAccountsAreRead() throws {
        let inks = RowInks.of(.day, light: .day, highContrast: false)
        let measure = RowMeasure(.comfortable)
        let row = Fixture.row(2, account: Fixture.work)
        let cell = MailRowCell()
        cell.frame = NSRect(x: 0, y: 0, width: 400, height: measure.height(RowShape(row, head: false)))
        // The dot's middle: at the avatar's foot, 4 in from its corner.
        let left = RowMeasure.listSide + RowMeasure.side + RowMeasure.avatar - 4
        let top = measure.padTop + RowMeasure.avatar - 4
        func dot() throws -> NSColor {
            let rep = try XCTUnwrap(cell.bitmapImageRepForCachingDisplay(in: cell.bounds))
            cell.cacheDisplay(in: cell.bounds, to: rep)
            let scale = CGFloat(rep.pixelsWide) / cell.bounds.width
            let colour = try XCTUnwrap(rep.colorAt(x: Int(left * scale), y: Int(top * scale)))
            return try XCTUnwrap(colour.usingColorSpace(.sRGB))
        }
        func near(_ a: NSColor, _ b: NSColor) -> Bool {
            guard let b = b.usingColorSpace(.sRGB) else { return false }
            return abs(a.redComponent - b.redComponent) + abs(a.greenComponent - b.greenComponent)
                + abs(a.blueComponent - b.blueComponent) < 0.06
        }
        XCTAssertFalse(near(try XCTUnwrap(inks.account(0).usingColorSpace(.sRGB)), inks.account(1)))

        // At launch the rows can come before the accounts' order: every dot takes the first colour.
        cell.show(row, head: nil, acts: [], look: RowLook(inks: inks, measure: measure, accounts: []))
        XCTAssertTrue(near(try dot(), inks.account(0)))
        cell.show(row, head: nil, acts: [], look: RowLook(inks: inks, measure: measure,
                                                         accounts: [Fixture.ada, Fixture.work]))
        XCTAssertTrue(near(try dot(), inks.account(1)), "the second account's colour, once the order is read")
    }

    // MARK: A row's buttons

    /// The table in a window that never shows, so its rows on screen have cells.
    private func shown(_ controller: MailboxTableController) -> NSWindow {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 420, height: 800), styleMask: [.borderless],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = controller.scrollView
        window.layoutIfNeeded()
        controller.table.layoutSubtreeIfNeeded()
        return window
    }

    /// The cell's drawing, as the screen would show it.
    private func draw(_ view: NSView) {
        guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return XCTFail("no drawing") }
        view.cacheDisplay(in: view.bounds, to: rep)
    }

    func testARowsButtonsActOnlyWhereDrawnAndWhenThePressEndsOnThem() async throws {
        let (app, list, controller) = try await table(brief: nil)
        var snoozed: [MailCopy] = []
        app.mailbox.snoozes = { snoozed += $0.map(MailCopy.init) }
        let window = shown(controller)
        defer { window.close() }
        let table = controller.table
        let cell = try XCTUnwrap(table.view(atColumn: 0, row: 1, makeIfNecessary: false) as? MailRowCell)
        let button = try XCTUnwrap(cell.frame(of: .snooze))
        let on = table.convert(NSPoint(x: button.midX, y: button.midY), from: cell)
        draw(cell)
        XCTAssertFalse(table.beginPress(at: on), "no button is drawn there: the click chooses the row")
        controller.hover(at: on)
        XCTAssertTrue(cell.state.hovering)
        XCTAssertFalse(table.beginPress(at: on), "the pointer is there, but the screen hasn't shown the buttons yet")
        draw(cell)
        XCTAssertTrue(table.beginPress(at: on))
        table.endPress(at: NSPoint(x: on.x - 160, y: on.y))
        XCTAssertEqual(snoozed, [], "a press dragged off the button does nothing")
        XCTAssertTrue(table.beginPress(at: on))
        table.endPress(at: on)
        XCTAssertEqual(snoozed, [MailCopy(try XCTUnwrap(list.copy(at: 1)))])
        XCTAssertTrue(app.mailbox.chosenRows.isEmpty, "a button chooses no row")
    }

    func testAPointerAlreadyOverARowShowsItsButtons() async throws {
        let (_, _, controller) = try await table(brief: nil)
        let window = shown(controller)
        defer { window.close() }
        let table = controller.table
        let cell = try XCTUnwrap(table.view(atColumn: 0, row: 2, makeIfNecessary: false) as? MailRowCell)
        let at = cell.convert(NSPoint(x: cell.bounds.midX, y: cell.bounds.maxY - 8), to: nil)
        let entered = try XCTUnwrap(NSEvent.enterExitEvent(
            with: .mouseEntered, location: at, modifierFlags: [], timestamp: 0, windowNumber: window.windowNumber,
            context: nil, eventNumber: 0, trackingNumber: 0, userData: nil))
        table.mouseEntered(with: entered)
        XCTAssertTrue(cell.state.hovering, "the pointer came over the list without moving over a row")
        NotificationCenter.default.post(name: NSWindow.didResignKeyNotification, object: window)
        XCTAssertFalse(cell.state.hovering, "a window in the back shows no row's buttons")
    }
}

/// Holds the first page back while it is shut, as a slow read would.
private final class PageGate: @unchecked Sendable {
    private let lock = NSLock()
    private var held = false

    var shut: Bool {
        get { lock.withLock { held } }
        set { lock.withLock { held = newValue } }
    }
}

/// A value the loader reads on its own task while the test sets it.
private final class Locked<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var held: Value

    init(_ value: Value) {
        held = value
    }

    var value: Value {
        get { lock.withLock { held } }
        set { lock.withLock { held = newValue } }
    }
}

/// The rows every table is told changed height while `body` runs, as the table itself is then told.
@MainActor
private enum NotedHeights {
    static func during(_ body: () async throws -> Void) async rethrows -> IndexSet {
        let noted = Locked(IndexSet())
        let selector = #selector(NSTableView.noteHeightOfRows(withIndexesChanged:))
        guard let method = class_getInstanceMethod(NSTableView.self, selector) else { return [] }
        typealias Note = @convention(c) (NSTableView, Selector, NSIndexSet) -> Void
        let original = method_getImplementation(method)
        let note = unsafeBitCast(original, to: Note.self)
        let recording: @convention(block) (NSTableView, NSIndexSet) -> Void = { table, rows in
            noted.value.formUnion(rows as IndexSet)
            note(table, selector, rows)
        }
        method_setImplementation(method, imp_implementationWithBlock(recording))
        defer { method_setImplementation(method, original) }
        try await body()
        return noted.value
    }
}
