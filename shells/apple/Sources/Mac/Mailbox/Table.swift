#if os(macOS)
import AppKit
import SwiftUI

/// The rows of the window's list: a native table over the place's `MailListModel`, each row as tall as what it shows
/// (the mockup's `.row`, drawn by `MailRowCell`), today's brief first in the Inbox. Rows are chosen as a Mac list
/// chooses them (click, ⇧-click, ⌘-click, ⌘A, or a row's check box), and acted on from the toolbar, the menus, a
/// row's context menu and quiet buttons, a swipe, or the page's single keys while the list has the focus.
struct MailboxRowsTable: NSViewRepresentable {
    let list: MailListModel
    let mailbox: Mailbox
    /// The rows chosen as the mailbox holds them: the table follows a choice made elsewhere (a key, the palette, a verb
    /// moving on to the next row).
    let chosen: [MailCopy]
    /// The row the keyboard is on while rows are marked: the message open beside them. Nil while none are marked.
    var cursor: MailCopy?
    /// Whether each row says its account to VoiceOver: everywhere but one account's own folder.
    let showsAccount: Bool
    let look: RowLook
    /// Today's brief over the Inbox's rows, or nil.
    let brief: BriefLine?
    /// The brief is open in the pane, so its row is the chosen one.
    let briefChosen: Bool
    /// The list stands in days, with a head over each day's first row.
    let sections: Bool
    /// The day it is (`AppStatus.today`): as it moves on, the heads are worked out again.
    var day = Calendar.current.startOfDay(for: Date())
    @Environment(MeadowShell.self) private var shell: MeadowShell?

    func makeCoordinator() -> MailboxTableController {
        MailboxTableController(list: list, mailbox: mailbox, look: look, sections: sections)
    }

    func makeNSView(context: Context) -> NSScrollView {
        let controller = context.coordinator
        controller.setBrief(brief, chosen: briefChosen)
        return controller.scrollView
    }

    func updateNSView(_ scrollView: NSScrollView, context: Context) {
        let controller = context.coordinator
        controller.anchors = context.environment.meadowAnchors
        controller.shell = shell
        controller.setShowsAccount(showsAccount)
        controller.setSections(sections)
        controller.setDay(day)
        controller.setLook(look)
        controller.setBrief(brief, chosen: briefChosen)
        controller.follow(chosen)
        controller.setCursor(cursor)
    }

    static func dismantleNSView(_ scrollView: NSScrollView, coordinator: MailboxTableController) {
        coordinator.stop()
    }
}

/// The table's data source, delegate and context menu. It tells the list which rows the scroll position shows, once a
/// frame at most, redraws only the shown rows a page changed, gives each row the height its shape needs and keeps
/// the rows on screen where they were when rows above them change height, and keeps the rows chosen by their copies
/// as rows move.
@MainActor
final class MailboxTableController: NSObject, NSTableViewDataSource, NSTableViewDelegate, NSMenuDelegate, ListDisplay {
    let scrollView = NSScrollView()
    let table = MailboxTableView()
    private let list: MailListModel
    private weak var mailbox: Mailbox?
    /// Each row says its account to VoiceOver: everywhere but one account's own folder.
    private(set) var showsAccount = true
    private(set) var sections: Bool
    private(set) var look: RowLook
    private var brief: BriefLine?
    /// The brief is open in the pane, so its row is the chosen one.
    private(set) var briefChosen = false
    /// Each position's shape as last worked out (`RowShape.packed`), zero where it isn't yet.
    private var shapes: [UInt8] = []
    /// Each position's summary's width on one line, where its shape was worked out and it has a summary: whether it
    /// fits on one line is worked out again from this when the column's width changes, with no text set.
    private var gistWidths: [Float] = []
    /// The positions whose shape was worked out since every row's height was last noted: the only ones whose height
    /// can be other than a waiting row's.
    private var measured = IndexSet()
    /// How wide the rows are, which the summaries' lines were worked out for: the column's width.
    private var rowWidth: CGFloat = 0
    /// The brief's summary's width on one line.
    private var briefWidth: Float?
    /// The day the heads were worked out on, and the day the window last said it is (`setDay`).
    private var now = Date()
    private var day: Date?
    /// The clock the heads are worked out by; a test sets its own.
    var clock: () -> Date = Date.init
    /// Whether the list stood at its top as it last settled, its height then, whether its height has changed since,
    /// and whether it settles at the end of this turn (`keepAtTop`).
    private var stoodAtTop = true
    private var heightSeen: CGFloat = 0
    private var resized = false
    private var settling = false
    /// The row under the pointer, and which of its parts.
    private var hovered: (row: Int, target: RowTarget?)?
    /// A choice the mailbox asked for is being shown: the table doesn't tell it back.
    private var following = false
    /// The row the keyboard is on while rows are marked, as the window last said: scrolled into view as it moves.
    private(set) var cursor: MailCopy?
    /// Where the window's buttons stand, for what opens beside them: a row's snooze clock says where it is, so the
    /// chooser it opens hangs from it and not from the pane's Snooze, which is the open message's. A row's clock
    /// is where h hangs it too for rows the pane doesn't show (`MeadowAnchors.snooze(for:open:)`).
    weak var anchors: MeadowAnchors? {
        didSet { anchors?.rowClock = { [weak self] copy in self?.clockFrame(of: copy) } }
    }
    /// The name a row's clock is remembered under while its chooser is open.
    static let rowSnooze = "snooze-row"
    /// The window's shell, whose drawer and focus reading Esc undoes before the list's own Esc.
    weak var shell: MeadowShell?
    private lazy var report = OncePerFrame { [weak self] in self?.reportVisible() }

    init(list: MailListModel, mailbox: Mailbox, look: RowLook, sections: Bool) {
        self.list = list
        self.mailbox = mailbox
        self.look = look
        // Before the table asks for a height: a row's head is part of its shape.
        self.sections = sections
        super.init()
        table.style = .plain
        table.headerView = nil
        table.usesAutomaticRowHeights = false
        table.rowSizeStyle = .custom
        table.intercellSpacing = .zero
        table.backgroundColor = .clear
        table.selectionHighlightStyle = .none
        table.focusRingType = .none
        table.allowsMultipleSelection = true
        table.allowsEmptySelection = true
        table.allowsColumnReordering = false
        table.allowsColumnResizing = false
        // A letter the list doesn't use does nothing: the table's own type-select would choose a row by it, or none,
        // and lose the place.
        table.allowsTypeSelect = false
        let column = NSTableColumn(identifier: MailRowCell.identifier)
        column.resizingMask = .autoresizingMask
        table.addTableColumn(column)
        rowWidth = column.width
        table.columnAutoresizingStyle = .lastColumnOnlyAutoresizingStyle
        table.dataSource = self
        table.delegate = self
        // A double-click opens the row's message in a window of its own, as a Mac's mail list does.
        table.target = self
        table.doubleAction = #selector(openedTwice)
        // Rows drag onto the rail's folders to move there; into another app they go as their subjects' text.
        table.setDraggingSourceOperationMask(.move, forLocal: true)
        table.setDraggingSourceOperationMask(.copy, forLocal: false)
        table.mailbox = mailbox
        table.controller = self
        table.setAccessibilityLabel(String(localized: "Messages"))
        let menu = NSMenu()
        menu.delegate = self
        table.menu = menu

        scrollView.documentView = table
        scrollView.drawsBackground = false
        scrollView.hasVerticalScroller = true
        scrollView.autohidesScrollers = true
        scrollView.automaticallyAdjustsContentInsets = false
        scrollView.contentInsets = NSEdgeInsets(top: RowMeasure.listTop, left: 0, bottom: RowMeasure.listBottom,
                                                right: 0)
        let clip = scrollView.contentView
        clip.drawsBackground = false
        clip.postsBoundsChangedNotifications = true
        clip.postsFrameChangedNotifications = true
        NotificationCenter.default.addObserver(
            self, selector: #selector(moved), name: NSView.boundsDidChangeNotification, object: clip)
        NotificationCenter.default.addObserver(
            self, selector: #selector(moved), name: NSView.frameDidChangeNotification, object: clip)
        list.display = self
    }

    func stop() {
        NotificationCenter.default.removeObserver(self)
        if list.display === self { list.display = nil }
    }

    // MARK: Positions

    /// The rows before the list's: the brief's, in the Inbox.
    private var lead: Int { brief == nil ? 0 : 1 }

    /// The list's position at a table row, or nil for the brief's.
    func position(_ row: Int) -> Int? {
        row >= lead ? row - lead : nil
    }

    private func row(at position: Int) -> Int {
        position + lead
    }

    // MARK: The look, the brief

    func setLook(_ look: RowLook) {
        let measured = look.measure != self.look.measure
        let painted = look.inks !== self.look.inks || look.accounts != self.look.accounts || look.mine != self.look.mine
        self.look = look
        if measured {
            keepingPlace { table.noteHeightOfRows(withIndexesChanged: IndexSet(integersIn: 0..<table.numberOfRows)) }
        }
        if measured || painted { redrawShown() }
    }

    func setShowsAccount(_ on: Bool) {
        guard on != showsAccount else { return }
        showsAccount = on
        redrawShown()
    }

    /// Days' heads on or off: every row's shape is worked out again.
    func setSections(_ on: Bool) {
        guard on != sections else { return }
        sections = on
        forgetShapes()
        keepingPlace { table.noteHeightOfRows(withIndexesChanged: IndexSet(integersIn: 0..<table.numberOfRows)) }
        redrawShown()
    }

    /// A new day (midnight, or the clock or the zone set): Today's rows are Yesterday's now, so every head and the
    /// height it takes is worked out again.
    func setDay(_ day: Date) {
        guard let was = self.day else {
            self.day = day
            return
        }
        guard day != was else { return }
        self.day = day
        now = clock()
        guard sections else { return }
        forgetShapes()
        keepingPlace { table.noteHeightOfRows(withIndexesChanged: IndexSet(integersIn: 0..<table.numberOfRows)) }
        redrawShown()
    }

    func setBrief(_ line: BriefLine?, chosen: Bool) {
        let had = brief != nil
        let changed = line != brief || chosen != briefChosen
        let height = briefHeight
        if line?.gist != brief?.gist { briefWidth = line.map { RowFaces.gistWidth($0.gist) } }
        brief = line
        briefChosen = chosen
        if had != (line != nil) {
            table.reloadData()
            follow(mailbox?.marked ?? [], scroll: false)
        } else if changed, line != nil {
            // Who needs you may now take one line, or two.
            if briefHeight != height { keepingPlace { table.noteHeightOfRows(withIndexesChanged: [0]) } }
            redraw(rows: IndexSet(integer: 0))
            refreshStates()
        }
    }

    /// The brief's row's height: its summary on one line where it fits.
    private var briefHeight: CGFloat {
        look.measure.height(RowShape.brief.fitting(briefWidth, in: rowWidth))
    }

    // MARK: The scroll position, once a frame

    @objc private func moved(_ note: Notification) {
        keepAtTop()
        report.request(framesPerSecond: scrollView.window?.screen?.maximumFramesPerSecond ?? 60)
        // A row scrolled under a pointer that didn't move is under it all the same.
        if let window = table.window, hovered != nil {
            hover(at: table.convert(window.mouseLocationOutsideOfEventStream, from: nil))
        }
    }

    /// A list at its top stays at its top as it grows or shrinks, as the offline note comes over it or goes: its rows
    /// make room under the note, as the page's do, rather than holding their place on the screen and passing under
    /// the note's edge with the brief's row and the first day's head. A list scrolled down is left where it is.
    /// The frame and the scroll position change in either order as the list's height does, so the list settles once
    /// the turn's changes are in, by where it stood before them. Its first height is no change: a list opened on a
    /// row further down stays scrolled to it.
    private func keepAtTop() {
        let height = scrollView.contentView.frame.height
        if height != heightSeen {
            resized = resized || heightSeen > 0
            heightSeen = height
        }
        guard !settling else { return }
        settling = true
        DispatchQueue.main.async { [weak self] in self?.settle() }
    }

    private func settle() {
        settling = false
        let clip = scrollView.contentView
        let top = -scrollView.contentInsets.top
        if resized, stoodAtTop, clip.bounds.minY != top {
            clip.scroll(to: NSPoint(x: clip.bounds.minX, y: top))
            scrollView.reflectScrolledClipView(clip)
        }
        resized = false
        stoodAtTop = clip.bounds.minY <= top + 0.5
    }

    private func reportVisible() {
        let shown = table.rows(in: table.visibleRect)
        let first = max(shown.location - lead, 0), end = max(shown.location + shown.length - lead, 0)
        list.setVisible(first..<max(first, end))
    }

    // MARK: Pages

    func rowsChanged(_ positions: IndexSet, totalChanged: Bool) {
        now = clock()
        var reshown = positions
        if totalChanged {
            // New mail above moves every row down: their shapes are worked out again as they show. Only a row whose
            // shape was worked out, or whose page just came, can change height: every other row is a waiting row
            // before and after, and the table asks the new rows' heights itself. A folder holds 100,000 rows.
            var remeasured = measured.union(positions)
            remeasured.shift(startingAt: 0, by: lead)
            forgetShapes()
            keepingPlace {
                table.noteNumberOfRowsChanged()
                let rows = table.numberOfRows
                table.noteHeightOfRows(withIndexesChanged: remeasured.filteredIndexSet { $0 < rows })
            }
            moved(Notification(name: NSView.boundsDidChangeNotification))
        } else {
            // A page come: its rows, and the row after it, whose head the page's last row decides.
            var changed = IndexSet()
            if let last = positions.last, last + 1 < list.total { reshown.insert(last + 1) }
            for position in reshown where position < shapes.count {
                let was = shapes[position]
                shapes[position] = 0
                if shape(at: position).packed != was || was == 0 { changed.insert(row(at: position)) }
            }
            if !changed.isEmpty { keepingPlace { table.noteHeightOfRows(withIndexesChanged: changed) } }
        }
        // Every row worked out again is shown again, the one after the page too: a page that came after the one
        // below it gives that row its day's head, and its height already made room for it.
        redraw(rows: IndexSet(reshown.map { row(at: $0) }))
        // New mail above the rows chosen moves them: keep the rows, not the positions.
        follow(mailbox?.marked ?? [], scroll: false)
        table.rowsShown()
    }

    /// Run a change to the rows' heights, then put the rows on screen back where they stood, unless the list is at
    /// its top, where new rows show.
    private func keepingPlace(_ change: () -> Void) {
        let clip = scrollView.contentView
        let top = clip.bounds.minY
        let atTop = top <= -scrollView.contentInsets.top + 0.5
        let first = table.row(at: NSPoint(x: 1, y: max(top, 0)))
        guard !atTop, first >= 0 else { return change() }
        let offset = top - table.rect(ofRow: first).minY
        change()
        guard first < table.numberOfRows else { return }
        clip.scroll(to: NSPoint(x: clip.bounds.minX, y: table.rect(ofRow: first).minY + offset))
        scrollView.reflectScrolledClipView(clip)
    }

    /// A position's shape: as worked out last, else from its row once its page is here.
    private func shape(at position: Int) -> RowShape {
        if shapes.count != list.total {
            shapes = Array(repeating: 0, count: list.total)
            gistWidths = Array(repeating: 0, count: list.total)
        }
        if position < shapes.count, let known = RowShape(packed: shapes[position]) { return known }
        guard let row = list.copy(at: position) else { return .waiting }
        let (shape, gistWidth) = RowShape.of(row, head: head(at: position, row) != nil, rowWidth: rowWidth)
        if position < shapes.count {
            shapes[position] = shape.packed
            gistWidths[position] = gistWidth ?? 0
            measured.insert(position)
        }
        return shape
    }

    /// Forget every row's shape: each is worked out again as it shows.
    private func forgetShapes() {
        shapes = Array(repeating: 0, count: list.total)
        gistWidths = Array(repeating: 0, count: list.total)
        measured = IndexSet()
    }

    /// The rows are as wide as `width` now: a summary that fit on one line may need two, or one that needed two fit
    /// on one. Each is worked out again from its width on one line, with no text set, and the table measures again
    /// only the rows whose lines changed; a row not worked out yet is worked out at this width as it comes.
    private func rewrap(_ width: CGFloat) {
        guard width != rowWidth else { return }
        let was = briefHeight
        rowWidth = width
        var changed = IndexSet()
        for position in measured where position < shapes.count {
            guard let shape = RowShape(packed: shapes[position]), shape.gist else { continue }
            let now = shape.fitting(gistWidths[position], in: width)
            guard now != shape else { continue }
            shapes[position] = now.packed
            changed.insert(row(at: position))
        }
        if brief != nil, briefHeight != was { changed.insert(0) }
        guard !changed.isEmpty else { return }
        keepingPlace { table.noteHeightOfRows(withIndexesChanged: changed) }
    }

    private func head(at position: Int, _ row: API.FolderRow) -> String? {
        guard sections else { return nil }
        return DayGroup.head(row, after: position == 0 ? .none : .some(list.copy(at: position - 1)), now: now)
    }

    func redrawShown() {
        table.enumerateAvailableRowViews { rowView, row in show(rowView.view(atColumn: 0), row: row) }
        refreshStates()
    }

    private func redraw(rows: IndexSet) {
        table.enumerateAvailableRowViews { rowView, row in
            if rows.contains(row) { show(rowView.view(atColumn: 0), row: row) }
        }
    }

    private func show(_ view: Any?, row: Int) {
        if let cell = view as? MailRowCell, let position = position(row) {
            var mail = list.copy(at: position)
            let role = mail.flatMap { mail in
                mailbox?.folders(of: mail.account).first { $0.name == mail.folder }?.role
            }
            // A draft waiting to go later says when, in its time's place.
            if role == "drafts", let draft = mail, let when = mailbox?.draftWhen?(draft) { mail?.when = when }
            let account = showsAccount ? mail.flatMap { mail in mailbox?.name(of: mail.account) } : nil
            cell.show(mail, head: mail.flatMap { head(at: position, $0) }, account: account,
                      acts: RowAct.shown(role: role), look: look)
        } else if let cell = view as? BriefRowCell, let brief {
            cell.show(brief, look: look)
        }
    }

    /// Show `copies` chosen, where their rows are held, and scroll the first into view.
    func follow(_ copies: [MailCopy], scroll: Bool = true) {
        var rows = IndexSet()
        for copy in copies {
            if let position = list.position(of: copy) { rows.insert(row(at: position)) }
        }
        let current = table.selectedRowIndexes
        // Rows chosen that aren't held stay chosen while the list hasn't moved.
        let keep = current.filter { row in
            position(row).map { list.copy(at: $0) == nil } ?? false && !(mailbox?.unheld.isEmpty ?? true)
        }
        rows.formUnion(IndexSet(keep))
        guard rows != current else { return refreshStates() }
        following = true
        table.selectRowIndexes(rows, byExtendingSelection: false)
        following = false
        refreshStates()
        if scroll, let first = rows.first, copies.count == 1 { table.scrollRowToVisible(first) }
    }

    /// Show `copy` as the row the keyboard is on while rows are marked, and scroll it into view as j and k move it.
    func setCursor(_ copy: MailCopy?) {
        guard copy != cursor else { return }
        cursor = copy
        refreshStates()
        if let copy, let position = list.position(of: copy) { table.scrollRowToVisible(row(at: position)) }
    }

    // MARK: How each row stands

    func refreshStates() {
        let chosen = table.selectedRowIndexes
        // Rows marked show their checks, even one marked alone; the row the keyboard is on shows as the chosen one.
        let selecting = mailbox?.marking ?? (chosen.count > 1)
        let keyed = table.keyed && table.window?.firstResponder === table
        let cursor = selecting ? mailbox?.app?.selection.copy : nil
        table.enumerateAvailableRowViews { rowView, row in
            let hover = hovered?.row == row
            if let cell = rowView.view(atColumn: 0) as? MailRowCell {
                let isChosen = chosen.contains(row)
                let here = selecting ? cursor != nil && cell.row.map(MailCopy.init) == cursor : isChosen
                cell.state = RowState(chosen: here, checked: isChosen && selecting, hovering: hover,
                                      target: hover ? hovered?.target : nil, selecting: selecting,
                                      keyed: keyed && here)
            } else if let cell = rowView.view(atColumn: 0) as? BriefRowCell {
                cell.state = RowState(chosen: briefChosen, hovering: hover, keyed: keyed && briefChosen)
            }
        }
    }

    /// The pointer moved to `point` in the table, or left it.
    func hover(at point: NSPoint?) {
        var now: (row: Int, target: RowTarget?)?
        if let point, table.visibleRect.contains(point) {
            let row = table.row(at: point)
            if row >= 0 {
                let cell = table.view(atColumn: 0, row: row, makeIfNecessary: false)
                let mail = cell as? MailRowCell
                if !(mail?.inHead(table.convert(point, to: mail)) ?? false) {
                    now = (row, mail?.target(at: table.convert(point, to: mail)))
                }
            }
        }
        guard now?.row != hovered?.row || now?.target != hovered?.target else { return }
        hovered = now
        let tip = now?.target.flatMap { target -> String? in
            if case .act(let act) = target { return act.title }
            return String(localized: "Select (⌘-click)")
        }
        table.toolTip = tip
        refreshStates()
    }

    /// A row's own button or its check box, pressed.
    func press(_ target: RowTarget, row: Int) {
        guard let mailbox, let position = position(row), let mail = list.copy(at: position) else { return }
        switch target {
        case .check:
            // As x marks the row open, and the table shows the marks the mailbox now holds.
            mailbox.toggleMark(mail)
            follow(mailbox.marked, scroll: false)
        case .act(.file): mailbox.file(.archive, rows: [mail])
        case .act(.delete): mailbox.file(.trash, rows: [mail])
        case .act(.snooze):
            if let clock = clockFrame(row: row) { anchors?.remember(Self.rowSnooze, at: clock) }
            mailbox.snoozes?([mail])
        case .act(.restore), .act(.toInbox): mailbox.restore([mail])
        }
    }

    /// Where the clock of `copy`'s row stands in the window, while the list shows that row.
    func clockFrame(of copy: MailCopy) -> CGRect? {
        guard let position = list.position(of: copy) else { return nil }
        return clockFrame(row: row(at: position))
    }

    /// Where a row's clock stands in the window, top left first, as SwiftUI's global space has it.
    func clockFrame(row: Int) -> CGRect? {
        guard let cell = table.view(atColumn: 0, row: row, makeIfNecessary: false) as? MailRowCell,
              let clock = cell.frame(of: .snooze), let content = table.window?.contentView else { return nil }
        var frame = content.convert(clock, from: cell)
        if !content.isFlipped { frame.origin.y = content.bounds.height - frame.maxY }
        return frame
    }

    /// Esc with several rows chosen: only the one chosen last stays chosen, and opens, so j and k go on from it. One
    /// row marked alone is chosen alone the same way.
    func keepLastChosen() {
        let last = table.selectedRow
        guard last >= 0, let position = position(last), let mail = list.copy(at: position) else {
            mailbox?.clearMarks()
            return
        }
        if table.selectedRowIndexes == IndexSet(integer: last) {
            mailbox?.chose([mail])
            refreshStates()
        } else {
            table.selectRowIndexes(IndexSet(integer: last), byExtendingSelection: false)
        }
    }

    // MARK: NSTableViewDataSource, NSTableViewDelegate

    func numberOfRows(in tableView: NSTableView) -> Int {
        list.total + lead
    }

    func tableView(_ tableView: NSTableView, heightOfRow row: Int) -> CGFloat {
        guard let position = position(row) else { return briefHeight }
        return look.measure.height(shape(at: position))
    }

    /// The splitter moved or the window was resized: the rows are as wide as the column.
    func tableViewColumnDidResize(_ notification: Notification) {
        rewrap(table.tableColumns.first?.width ?? rowWidth)
    }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let view: NSView
        if position(row) == nil {
            view = tableView.makeView(withIdentifier: BriefRowCell.identifier, owner: nil) as? BriefRowCell
                ?? BriefRowCell()
        } else {
            view = tableView.makeView(withIdentifier: MailRowCell.identifier, owner: nil) as? MailRowCell
                ?? MailRowCell()
        }
        show(view, row: row)
        return view
    }

    func tableView(_ tableView: NSTableView, didAdd rowView: NSTableRowView, forRow row: Int) {
        rowView.backgroundColor = .clear
        refreshStates()
    }

    /// The brief's row is never chosen with mail: alone, it shows the brief in the pane. A click opens it, writing
    /// today's if there is none; the arrow keys only pass over it, showing the one written and writing none.
    func tableView(_ tableView: NSTableView,
                   selectionIndexesForProposedSelection proposed: IndexSet) -> IndexSet {
        guard lead > 0, proposed.contains(0) else { return proposed }
        if proposed.count == 1 {
            mailbox?.openBrief(deliberately: !table.keyed)
            return IndexSet()
        }
        var rest = proposed
        rest.remove(0)
        return rest
    }

    func tableViewSelectionDidChange(_ notification: Notification) {
        // Once the mailbox holds the choice, which says whether the rows are marks.
        defer { refreshStates() }
        guard !following, let mailbox else { return }
        var rows: [API.FolderRow] = []
        var unheld = IndexSet()
        for row in table.selectedRowIndexes {
            guard let position = position(row) else { continue }
            if let mail = list.copy(at: position) { rows.append(mail) } else { unheld.insert(position) }
        }
        mailbox.chose(rows, unheld: unheld)
    }

    /// A swipe's actions, as the page's row has them for the pointer: read or unread from the left; archive and
    /// delete from the right.
    func tableView(_ tableView: NSTableView, rowActionsForRow row: Int,
                   edge: NSTableView.RowActionEdge) -> [NSTableViewRowAction] {
        guard let mailbox, let position = position(row), let mail = list.copy(at: position) else { return [] }
        if edge == .leading {
            let title = mail.seen ? String(localized: "Unread") : String(localized: "Read")
            let read = NSTableViewRowAction(style: .regular, title: title) { _, _ in
                mailbox.setSeen(!mail.seen, rows: [mail])
            }
            read.backgroundColor = look.inks.primary
            return [read]
        }
        let delete = NSTableViewRowAction(style: .destructive, title: String(localized: "Delete")) { _, _ in
            mailbox.file(.trash, rows: [mail])
        }
        delete.backgroundColor = look.inks.rust
        let archive = NSTableViewRowAction(style: .regular, title: String(localized: "Archive")) { _, _ in
            mailbox.file(.archive, rows: [mail])
        }
        archive.backgroundColor = look.inks.moss
        return [delete, archive]
    }

    /// A row double-clicked: its message in a window of its own. The brief's row and a row not yet held open nothing.
    @objc func openedTwice() {
        openWindow(row: table.clickedRow)
    }

    func openWindow(row: Int) {
        guard row >= 0, let position = position(row), let mail = list.copy(at: position) else { return }
        mailbox?.opensWindow?(MailCopy(mail))
    }

    // MARK: Dragging rows

    /// A row dragged: the rows chosen when it is one of them, else it alone. The pasteboard holds each one's subject,
    /// for another app; the mailbox holds the rows themselves, which the rail's folders move (`Mailbox.drop`).
    func tableView(_ tableView: NSTableView, pasteboardWriterForRow row: Int) -> (any NSPasteboardWriting)? {
        guard let position = position(row), let mail = list.copy(at: position) else { return nil }
        let item = NSPasteboardItem()
        item.setString(mail.subject, forType: .string)
        return item
    }

    func tableView(_ tableView: NSTableView, draggingSession session: NSDraggingSession,
                   willBeginAt screenPoint: NSPoint, forRowIndexes rowIndexes: IndexSet)
    {
        mailbox?.dragging = rowIndexes.compactMap { position($0).flatMap { list.copy(at: $0) } }
    }

    func tableView(_ tableView: NSTableView, draggingSession session: NSDraggingSession, endedAt screenPoint: NSPoint,
                   operation: NSDragOperation)
    {
        mailbox?.dragging = []
    }

    // MARK: The context menu

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let clicked = table.clickedRow
        guard clicked >= 0, let position = position(clicked), let mailbox else { return }
        // A row's menu acts on it (`RowMenu.rows`) and chooses nothing: the marks, the rows chosen and the message
        // open stay as they were, as a right-click leaves them in Mail.
        for item in RowMenu.items(for: mailbox, clicked: list.copy(at: position)) { menu.addItem(item) }
    }
}

extension Mailbox {
    /// Put rows back in their account's Inbox: out of Trash, or out of Archive (`.row-acts`' restore). `given` are a
    /// row's own; with none, every row chosen, those whose pages aren't held too, as many as the bulk bar counts.
    func restore(_ given: [API.FolderRow]? = nil) {
        Task {
            let rows = if let given { given } else { await targets(standing: false) }
            guard let first = rows.first else { return saysNothingChosen() }
            let inbox = folders(of: first.account).first { $0.role == "inbox" }
            await moveNow(to: inbox?.name ?? "INBOX", label: inbox.map(Words.role) ?? String(localized: "Inbox"),
                          rows: rows)
        }
    }

    /// Ask when to bring back every row chosen, those whose pages aren't held too (the bulk bar's Snooze).
    func snoozeChosen() {
        Task {
            let rows = await targets(standing: false)
            if rows.isEmpty { saysNothingChosen() } else { snoozes?(rows) }
        }
    }
}

/// The table, taking the page's single keys while it has the focus (app/commands.ts): j and k, e, d, s, u, m, z, g
/// then a letter, and the rest. Typing anywhere else never reaches them, and the setting that turns single keys off
/// (`single_keys`) leaves only Delete, which deletes in every Mac list. It tells its controller where the pointer is,
/// and gives a row's own buttons and check box their clicks: as a button takes one, where it is drawn, acting when
/// the press ends on it.
final class MailboxTableView: NSTableView {
    weak var mailbox: Mailbox?
    weak var controller: MailboxTableController?
    /// The keyboard moved last, so the chosen row shows the focus ring (`:focus-visible`).
    private(set) var keyed = false
    private var chordAt: Date?
    private var tracking: NSTrackingArea?
    /// A row's button or check box held down, which acts if the press ends on it.
    private var pressing: (row: Int, target: RowTarget)?

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking { removeTrackingArea(tracking) }
        let area = NSTrackingArea(rect: .zero, options: [.mouseMoved, .mouseEnteredAndExited, .activeInKeyWindow,
                                                         .inVisibleRect], owner: self)
        addTrackingArea(area)
        tracking = area
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        let center = NotificationCenter.default
        center.removeObserver(self, name: NSWindow.didBecomeKeyNotification, object: nil)
        center.removeObserver(self, name: NSWindow.didResignKeyNotification, object: nil)
        guard let window else { return }
        center.addObserver(self, selector: #selector(keyChanged), name: NSWindow.didBecomeKeyNotification,
                           object: window)
        center.addObserver(self, selector: #selector(keyChanged), name: NSWindow.didResignKeyNotification,
                           object: window)
        // The list has the keyboard as the window opens, as Mail's does, not the search field the window would
        // otherwise start in; and a list come in place of another (g then a letter, a tab, a folder) takes it on.
        window.initialFirstResponder = self
        DispatchQueue.main.async { [weak self] in MainActor.assumeIsolated { self?.takeKeyboardIfNowhere(placed: true) } }
    }

    /// Take the keyboard where nothing in the window has it, unless a dialog is up, or the first run's welcome. A
    /// list just `placed` takes it from the window's SwiftUI too, which a click on a tab or a place in the rail
    /// leaves holding it; never from a field. Placed under the welcome, it takes it once the first account's rows
    /// show (`rowsShown`).
    func takeKeyboardIfNowhere(placed: Bool = false) {
        guard let window, mailbox?.typing != true else { return }
        guard !welcomeUp() else {
            if placed { awaitsRows = true }
            return
        }
        let responder = window.firstResponder
        guard DialogFocus.nowhere(responder, in: window) || (placed && responder === window.contentView) else { return }
        window.makeFirstResponder(self)
    }

    /// Whether the first run's welcome stands over the window.
    var welcomeUp: @MainActor () -> Bool = { AppStatus.shared.firstRun }
    /// The list was placed while the welcome stood over it, and takes the keyboard once rows show.
    private var awaitsRows = false

    /// Rows came: a list placed under the welcome takes the keyboard now that the first account's rows show, as a
    /// list placed takes it, so it never stays where the window put it first (the splitter).
    func rowsShown() {
        guard awaitsRows, !welcomeUp(), numberOfRows > 0 else { return }
        awaitsRows = false
        takeKeyboardIfNowhere(placed: true)
    }

    /// The window came to the front with the pointer already resting on a row, which moved no mouse: the row shows
    /// its buttons at once, as the page's do under the pointer. A window in the back shows none.
    @objc private func keyChanged(_ note: Notification) {
        guard let window, window.isKeyWindow else {
            controller?.hover(at: nil)
            return
        }
        controller?.hover(at: convert(window.mouseLocationOutsideOfEventStream, from: nil))
    }

    override func mouseEntered(with event: NSEvent) {
        super.mouseEntered(with: event)
        controller?.hover(at: convert(event.locationInWindow, from: nil))
    }

    override func mouseMoved(with event: NSEvent) {
        super.mouseMoved(with: event)
        controller?.hover(at: convert(event.locationInWindow, from: nil))
    }

    override func mouseExited(with event: NSEvent) {
        super.mouseExited(with: event)
        controller?.hover(at: nil)
    }

    override func mouseDown(with event: NSEvent) {
        setKeyed(false)
        let point = convert(event.locationInWindow, from: nil)
        // The day's head over a row chooses nothing.
        if let (_, cell) = mailCell(at: point), cell.inHead(convert(point, to: cell)) { return }
        if event.modifierFlags.intersection([.command, .shift]).isEmpty, beginPress(at: point) {
            window?.makeFirstResponder(self)
            return
        }
        super.mouseDown(with: event)
    }

    override func mouseDragged(with event: NSEvent) {
        guard pressing != nil else { return super.mouseDragged(with: event) }
        // The button pressed shows it while the pointer is on it, and not once it is dragged off.
        controller?.hover(at: convert(event.locationInWindow, from: nil))
    }

    override func mouseUp(with event: NSEvent) {
        guard pressing != nil else { return super.mouseUp(with: event) }
        endPress(at: convert(event.locationInWindow, from: nil))
    }

    /// A press at `point`, in the table: on a button or a check box the row has drawn, it is held until the press
    /// ends. Whether it was one; anywhere else the table takes the click and chooses the row.
    func beginPress(at point: NSPoint) -> Bool {
        pressing = nil
        guard let (row, cell) = mailCell(at: point), let target = cell.shownTarget(at: convert(point, to: cell)) else {
            return false
        }
        pressing = (row, target)
        return true
    }

    /// The press held ends at `point`: the button acts if the pointer is still on it. Dragged off, nothing happens.
    func endPress(at point: NSPoint) {
        guard let held = pressing else { return }
        pressing = nil
        guard let (row, cell) = mailCell(at: point), row == held.row,
              cell.target(at: convert(point, to: cell)) == held.target else { return }
        controller?.press(held.target, row: row)
    }

    /// The mail row at `point`, and its cell, where the table shows one.
    private func mailCell(at point: NSPoint) -> (row: Int, cell: MailRowCell)? {
        let row = row(at: point)
        guard row >= 0, let cell = view(atColumn: 0, row: row, makeIfNecessary: false) as? MailRowCell else {
            return nil
        }
        return (row, cell)
    }

    override func becomeFirstResponder() -> Bool {
        defer { controller?.refreshStates() }
        return super.becomeFirstResponder()
    }

    override func resignFirstResponder() -> Bool {
        defer { controller?.refreshStates() }
        return super.resignFirstResponder()
    }

    private func setKeyed(_ on: Bool) {
        guard keyed != on else { return }
        keyed = on
        controller?.refreshStates()
    }

    override func keyDown(with event: NSEvent) {
        setKeyed(true)
        guard let mailbox, event.modifierFlags.intersection([.command, .control, .option]).isEmpty,
              let key = SingleKey.typed(event) else {
            return super.keyDown(with: event)
        }
        // A dialog or a chooser is up over the list: its keys are its own, and Esc goes on to close it.
        if mailbox.typing {
            if key == "\u{1b}" { super.keyDown(with: event) }
            return
        }
        let scalar = key.unicodeScalars.first.map { Int($0.value) } ?? 0
        if scalar == NSDeleteCharacter || scalar == NSDeleteFunctionKey || scalar == NSBackspaceCharacter {
            return mailbox.file(.trash)
        }
        if key == "\u{1b}" {
            // A file's Quick Look first, put up from the reader while the list has the keyboard: it goes, and the
            // list keeps the keyboard.
            if QuickLookShown.close() { return }
            // The shell's next: the drawer closes, or focus reading ends, while the list stands aside behind it.
            if controller?.shell?.escape() == true { return }
            return escape()
        }
        // Return on the brief's row opens it, as a click does.
        if key == "\r" || key == "\u{3}", controller?.briefChosen == true {
            return mailbox.openBrief(deliberately: true)
        }
        // Space pages the message open beside the list, ⇧Space back up, as Mail's list does.
        if key == " " {
            ReaderPage.turn(in: window, down: !event.modifierFlags.contains(.shift))
            return
        }
        // While rows are marked, ↑ and ↓ move the row the keyboard is on as k and j do, and the marks stay.
        if mailbox.marking, !event.modifierFlags.contains(.shift),
           scalar == NSUpArrowFunctionKey || scalar == NSDownArrowFunctionKey {
            return mailbox.move(by: scalar == NSDownArrowFunctionKey ? 1 : -1)
        }
        // The table's own: the arrows, Page Up and Down, Home and End, Tab.
        if Self.tablesOwn(scalar) { return super.keyDown(with: event) }
        guard mailbox.singleKeys, event.modifierFlags.intersection([.shift]).isEmpty || "#?".contains(key) else {
            return
        }
        let lower = Character(key.lowercased())
        if let at = chordAt, Date().timeIntervalSince(at) < 1.8 {
            chordAt = nil
            return mailbox.go(chord: lower)
        }
        // Any other key does nothing: no row is chosen by its letter, and the place stays.
        _ = RowKeys.run(lower, on: mailbox, chord: { chordAt = Date() })
        // x marks here and now: the table shows the marks without waiting for the window to draw again.
        if lower == "x" { controller?.follow(mailbox.marked, scroll: false) }
    }

    /// Esc in the list, once nothing over it wants it: rows marked lose their marks, the message open beside them
    /// staying open, or with none open only the one chosen last stays, open; out of a search, back where it was asked.
    func escape() {
        guard let mailbox else { return }
        if mailbox.marking {
            if mailbox.app?.selection.copy != nil || controller == nil {
                mailbox.clearMarks()
                controller?.follow(mailbox.marked, scroll: false)
            } else {
                controller?.keepLastChosen()
            }
        } else {
            mailbox.leaveSearch()
        }
    }

    /// Keys the table moves with itself: the function keys (arrows, Page Up and Down, Home, End), Tab and ⇧Tab.
    /// Return and Enter are the shell's, for focus reading, as the single keys are.
    static func tablesOwn(_ scalar: Int) -> Bool {
        (0xF700...0xF8FF).contains(scalar) || [0x09, 0x19].contains(scalar)
    }
}

/// Space in the list pages the message open in the reading pane, as Mail's does: the reader's document, which every
/// frame of the message scrolls as one, moves a page down or up. With no message open it does nothing.
@MainActor
enum ReaderPage {
    @discardableResult
    static func turn(in window: NSWindow?, down: Bool) -> Bool {
        guard let scroll = document(in: window?.contentView) else { return false }
        return turn(scroll, down: down)
    }

    /// A key that scrolls a reader with nothing else to type in, as a message's own window has it: Space and ⇧Space,
    /// the arrows a line, Page Up and Down, Home and End. Whether it was one; with ⌘, ⌃ or ⌥ held, none is.
    static func scrolls(_ event: NSEvent, in window: NSWindow?) -> Bool {
        guard let move = move(for: event) else { return false }
        if let scroll = document(in: window?.contentView) { move(scroll) }
        return true
    }

    /// What `event` does to a reader when it is one of those keys; nil for any other.
    static func move(for event: NSEvent) -> ((NSScrollView) -> Void)? {
        guard event.modifierFlags.isDisjoint(with: [.command, .control, .option]),
              let key = event.charactersIgnoringModifiers?.unicodeScalars.first.map({ Int($0.value) }) else {
            return nil
        }
        let up = event.modifierFlags.contains(.shift)
        switch key {
        case 0x20: return { turn($0, down: !up) }
        case NSDownArrowFunctionKey: return { by($0, line) }
        case NSUpArrowFunctionKey: return { by($0, -line) }
        case NSPageDownFunctionKey: return { turn($0, down: true) }
        case NSPageUpFunctionKey: return { turn($0, down: false) }
        case NSHomeFunctionKey: return { by($0, -far) }
        case NSEndFunctionKey: return { by($0, far) }
        default: return nil
        }
    }

    /// What an arrow key scrolls by, and further than any message is long, for Home and End.
    static let line: CGFloat = 40
    static let far: CGFloat = 10_000_000

    /// A page down or up `scroll`, less a little of the last page kept in sight.
    @discardableResult
    static func turn(_ scroll: NSScrollView, down: Bool) -> Bool {
        let height = scroll.contentView.bounds.height
        return by(scroll, max(height - 40, height / 2) * (down ? 1 : -1))
    }

    /// Move `scroll`'s document by `points`, down for more, within its ends. Whether it moved.
    @discardableResult
    static func by(_ scroll: NSScrollView, _ points: CGFloat) -> Bool {
        guard let document = scroll.documentView else { return false }
        let clip = scroll.contentView
        let before = clip.bounds.origin
        var visible = document.convert(clip.bounds, from: clip)
        visible.origin.y += document.isFlipped ? points : -points
        document.scrollToVisible(visible)
        scroll.reflectScrolledClipView(clip)
        return clip.bounds.origin != before
    }

    /// The reader's document: what scrolls the first message frame shown in `view`.
    static func document(in view: NSView?) -> NSScrollView? {
        guard let view, !view.isHiddenOrHasHiddenAncestor else { return nil }
        if let frame = view as? FrameWebView, !frame.visibleRect.isEmpty { return frame.enclosingScrollView }
        for child in view.subviews {
            if let found = document(in: child) { return found }
        }
        return nil
    }
}

/// The page's single keys, each as its command runs it (`buildCommands`). Keys of other areas (reply, snooze, help)
/// are theirs to add to `Mailbox.keys`.
@MainActor
enum RowKeys {
    /// Run what `key` does, and say whether it did anything.
    static func run(_ key: Character, on mailbox: Mailbox, chord: () -> Void) -> Bool {
        switch key {
        case "j": mailbox.move(by: 1)
        case "k": mailbox.move(by: -1)
        case "n": mailbox.moveUnread(by: 1)
        case "p": mailbox.moveUnread(by: -1)
        case "e": mailbox.file(.archive)
        case "d", "#": mailbox.file(.trash)
        case "b": mailbox.file(.junk)
        case "m": mailbox.askMove()
        case "s": mailbox.star()
        case "u": mailbox.setSeen(false)
        case "x": mailbox.markOpen()
        case "z": mailbox.undo()
        case "v": if mailbox.app?.place?.search != nil, !mailbox.onSavedView { mailbox.savingView = mailbox.app?.place?.search?.q }
        case "/": mailbox.searchFocus = true
        case "g":
            chord()
            mailbox.say(goHint)
        default:
            guard let other = mailbox.keys[key] else { return false }
            other()
        }
        return true
    }

    /// What g says it waits for (`goHint`).
    static var goHint: String {
        "Go to folder:  " + (Mailbox.roleKeys.keys.sorted { "isatkd".firstIndex(of: $0)! < "isatkd".firstIndex(of: $1)! }
            + Mailbox.virtualKeys.keys.sorted { "zwl".firstIndex(of: $0)! < "zwl".firstIndex(of: $1)! })
            .map(String.init).joined(separator: "·")
    }
}

/// The single keys nothing in the mail window takes, as after a click on the brief's card or a lane, which leaves the
/// keyboard with the window's SwiftUI and no key of the list's working: they go on to the list, which takes the
/// keyboard with them, and where no list is shown they run as the list runs them, so z, j and k do what the toasts and
/// the legend say. It stands last in the window's chain of responders, so whatever takes a key first keeps it: a
/// field, the gate, the rail's arrows, a dialog. A key typed in a field never reaches it.
struct UntakenKeys: NSViewRepresentable {
    let mailbox: Mailbox

    func makeNSView(context: Context) -> NSView { Probe(mailbox) }

    func updateNSView(_ view: NSView, context: Context) {}

    final class Probe: NSView {
        let mailbox: Mailbox

        init(_ mailbox: Mailbox) {
            self.mailbox = mailbox
            super.init(frame: .zero)
        }

        required init?(coder: NSCoder) { nil }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let window { Catcher.hang(on: window, for: mailbox) }
        }
    }

    /// The responder after the window, once a window, which the window holds: its chain doesn't.
    final class Catcher: NSResponder {
        weak var mailbox: Mailbox?
        weak var window: NSWindow?
        /// When g was pressed with no list to wait for its letter.
        private var chordAt: Date?
        private nonisolated(unsafe) static var held: UInt8 = 0

        /// Stand after `window` in its chain, once.
        @discardableResult
        static func hang(on window: NSWindow, for mailbox: Mailbox) -> Catcher {
            var next = window.nextResponder
            while let responder = next {
                if let held = responder as? Catcher {
                    held.mailbox = mailbox
                    return held
                }
                next = responder.nextResponder
            }
            let catcher = Catcher()
            catcher.mailbox = mailbox
            catcher.window = window
            catcher.nextResponder = window.nextResponder
            window.nextResponder = catcher
            objc_setAssociatedObject(window, &held, catcher, .OBJC_ASSOCIATION_RETAIN_NONATOMIC)
            return catcher
        }

        override func keyDown(with event: NSEvent) {
            guard let mailbox, let window, let key = Self.single(event, in: window, mailbox: mailbox) else {
                return super.keyDown(with: event)
            }
            if let table = ListTable.find(in: window.contentView), !table.isHiddenOrHasHiddenAncestor {
                window.makeFirstResponder(table)
                return table.keyDown(with: event)
            }
            let lower = Character(key.lowercased())
            if let at = chordAt, Date().timeIntervalSince(at) < 1.8 {
                chordAt = nil
                return mailbox.go(chord: lower)
            }
            if !RowKeys.run(lower, on: mailbox, chord: { [weak self] in self?.chordAt = Date() }) {
                super.keyDown(with: event)
            }
        }

        /// The single key `event` is, when it is one the list's keys take and nothing is being typed: a character
        /// with no ⌘, ⌃ or ⌥ (⇧ only for # and ?), never Space, Return, Esc, Delete, Tab or the arrows.
        static func single(_ event: NSEvent, in window: NSWindow, mailbox: Mailbox) -> Character? {
            guard mailbox.singleKeys, !mailbox.typing, !typesText(window.firstResponder),
                  event.modifierFlags.intersection([.command, .control, .option]).isEmpty,
                  let key = SingleKey.typed(event), let scalar = key.unicodeScalars.first,
                  scalar.value > 0x20, scalar.value < 0x7F
            else { return nil }
            guard !event.modifierFlags.contains(.shift) || "#?".contains(key) else { return nil }
            return key
        }

        /// Whether `responder` takes typed text: a field's editor, or a text view that can be edited.
        static func typesText(_ responder: NSResponder?) -> Bool {
            responder is NSTextField || (responder as? NSTextView)?.isEditable == true
        }
    }
}

/// A row's context menu: the mailbox's verbs on the rows chosen, a move to one of the account's folders, and the
/// sender's card and mail.
@MainActor
enum RowMenu {
    /// The rows a row's menu acts on: every row marked when the row clicked is one of them, else the row clicked
    /// alone, whatever is chosen or open. As the list holds them now: a mark since they were chosen changed their
    /// flags, and the words follow it.
    static func rows(for mailbox: Mailbox, clicked: API.FolderRow?) -> [API.FolderRow] {
        let marks = mailbox.marking ? mailbox.chosenRows : []
        guard let clicked else { return mailbox.current(marks) }
        let copy = MailCopy(clicked)
        if marks.contains(where: { MailCopy($0) == copy }) { return mailbox.current(marks) }
        return mailbox.current([clicked])
    }

    static func items(for mailbox: Mailbox, clicked: API.FolderRow?) -> [NSMenuItem] {
        let rows = rows(for: mailbox, clicked: clicked)
        guard let first = rows.first else { return [] }
        var items: [NSMenuItem] = []
        if rows.count == 1, let open = mailbox.opensWindow {
            items += [Item(String(localized: "Open in New Window"), "macwindow") { open(MailCopy(first)) }, .separator()]
        }
        items += [
            Item(String(localized: "Archive"), "archivebox") { mailbox.file(.archive, rows: rows) },
            Item(String(localized: "Delete"), "trash") { mailbox.file(.trash, rows: rows) },
            Item(String(localized: "Mark as Junk"), "xmark.bin") { mailbox.file(.junk, rows: rows) },
        ]
        if Set(rows.map(\.account)).count == 1 {
            let from = Set(rows.map(\.folder))
            let ask = MoveAsk(account: first.account, from: from.count == 1 ? first.folder : nil, rows: rows)
            let move = NSMenuItem(title: String(localized: "Move To"), action: nil, keyEquivalent: "")
            move.image = NSImage(systemSymbolName: "folder", accessibilityDescription: nil)
            let folders = NSMenu()
            for folder in mailbox.destinations(ask) {
                folders.addItem(Item(Words.role(folder), nil) {
                    Task { await mailbox.moveNow(to: folder.name, label: Words.role(folder), rows: rows) }
                })
            }
            // No separator before it: in this short menu it read as an empty item that can’t be chosen.
            folders.addItem(Item(String(localized: "Other Folder…"), nil) { mailbox.moving = ask })
            move.submenu = folders
            items.append(move)
        }
        items.append(.separator())
        let anyUnread = rows.contains { !$0.seen }
        items.append(Item(anyUnread ? String(localized: "Mark as Read") : String(localized: "Mark as Unread"),
                          anyUnread ? "envelope.open" : "envelope.badge") { mailbox.setSeen(anyUnread, rows: rows) })
        let unstars = rows.count == 1 && first.flagged
        items.append(Item(unstars ? String(localized: "Unstar") : String(localized: "Star"), "star") {
            mailbox.star(on: !unstars, rows: rows)
        })
        let others = mailbox.rowMenu.flatMap { $0(rows) }
        if !others.isEmpty {
            items.append(.separator())
            items += others
        }
        if rows.count == 1, let address = first.fromAddr, !address.isEmpty {
            items.append(.separator())
            items.append(Item(String(localized: "About This Sender"), "person.crop.circle") { mailbox.contact = address })
            items.append(Item(String(localized: "Search Mail from This Sender"), "magnifyingglass") {
                mailbox.app?.place = .search(Search(q: "from:\(address)"))
            })
            items.append(Item(String(localized: "Copy Sender’s Address"), "doc.on.doc") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(address, forType: .string)
            })
        }
        return items
    }
}

/// A menu item that runs a closure.
final class Item: NSMenuItem {
    private let run: @MainActor () -> Void

    init(_ title: String, _ symbol: String?, run: @escaping @MainActor () -> Void) {
        self.run = run
        super.init(title: title, action: #selector(fire), keyEquivalent: "")
        target = self
        if let symbol { image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil) }
    }

    @available(*, unavailable)
    required init(coder: NSCoder) { fatalError("not from a nib") }

    /// A menu's action comes on the main thread.
    @objc private func fire() {
        let run = run
        MainActor.assumeIsolated { run() }
    }
}

/// One line of text, set once when its row is shown and cut short with an ellipsis when it is too wide. Its colour is
/// taken from the context when it draws, so one line serves every state of its row.
@MainActor
struct TextLine {
    let line: CTLine
    let width: CGFloat

    init(_ text: String, _ font: NSFont, kern: CGFloat = 0) {
        var attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            NSAttributedString.Key(kCTForegroundColorFromContextAttributeName as String): true,
        ]
        if kern != 0 { attributes[.kern] = kern }
        line = CTLineCreateWithAttributedString(NSAttributedString(string: Self.flat(text), attributes: attributes))
        width = ceil(CTLineGetTypographicBounds(line, nil, nil, nil))
    }

    /// A line already set, as a summary's lines are.
    init(_ line: CTLine) {
        self.line = line
        width = ceil(CTLineGetTypographicBounds(line, nil, nil, nil))
    }

    /// One line: a subject or a snippet with a line break in it is drawn on one.
    static func flat(_ text: String) -> String {
        text.replacingOccurrences(of: "\n", with: " ").replacingOccurrences(of: "\r", with: " ")
    }

    /// `text` in at most `lines` lines `width` wide, the last ending in an ellipsis after its last whole word when
    /// there is more (`-webkit-line-clamp`).
    static func clamp(_ text: NSAttributedString, width: CGFloat, lines count: Int) -> [CTLine] {
        let setter = CTTypesetterCreateWithAttributedString(text)
        var lines: [CTLine] = []
        var start = 0
        while start < text.length, lines.count < count {
            let fits = max(CTTypesetterSuggestLineBreak(setter, start, Double(width)), 1)
            guard lines.count == count - 1, start + fits < text.length else {
                lines.append(CTTypesetterCreateLine(setter, CFRange(location: start, length: fits)))
                start += fits
                continue
            }
            // The last line, with more after it: the words that fit beside an ellipsis, then the ellipsis.
            let ellipsis = NSAttributedString(string: "\u{2026}",
                                              attributes: text.attributes(at: start, effectiveRange: nil))
            let room = width - CTLineGetTypographicBounds(CTLineCreateWithAttributedString(ellipsis), nil, nil, nil)
            let words = max(CTTypesetterSuggestLineBreak(setter, start, Double(room)), 1)
            let kept = NSMutableAttributedString(attributedString: text.attributedSubstring(
                from: NSRange(location: start, length: words)))
            while kept.string.last?.isWhitespace == true {
                kept.deleteCharacters(in: NSRange(location: kept.length - 1, length: 1))
            }
            kept.append(ellipsis)
            lines.append(cut(CTLineCreateWithAttributedString(kept), to: width))
            start = text.length
        }
        return lines
    }

    /// `line` cut to `width` with an ellipsis, where it is wider.
    static func cut(_ line: CTLine, to width: CGFloat) -> CTLine {
        guard CTLineGetTypographicBounds(line, nil, nil, nil) > width else { return line }
        // The ellipsis takes the line's own face, so it sits on the line as its letters do.
        let runs = CTLineGetGlyphRuns(line) as? [CTRun] ?? []
        let attributes = runs.first.map { CTRunGetAttributes($0) as? [NSAttributedString.Key: Any] ?? [:] } ?? [:]
        let ellipsis = CTLineCreateWithAttributedString(NSAttributedString(string: "\u{2026}", attributes: attributes))
        return CTLineCreateTruncatedLine(line, Double(width), .end, ellipsis) ?? line
    }

    func draw(in context: CGContext, x: CGFloat, baseline: CGFloat, width available: CGFloat, color: NSColor,
              mirrored: (NSRect) -> NSRect) {
        guard available > 0 else { return }
        let shown = width > available ? Self.cut(line, to: available) : line
        let drawn = min(width, available)
        let origin = mirrored(NSRect(x: x, y: baseline, width: drawn, height: 0)).minX
        context.setFillColor(color.cgColor)
        context.textPosition = CGPoint(x: origin, y: baseline)
        CTLineDraw(shown, context)
    }
}
#endif
