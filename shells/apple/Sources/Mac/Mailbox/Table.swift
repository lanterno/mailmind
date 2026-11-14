#if os(macOS)
import AppKit
import SwiftUI

/// The window's list of mail: a native table with one row height over the place's `MailListModel`, drawing every
/// column and badge the page's row shows (features/mailbox/MessageList.tsx). Rows are chosen as a Mac list chooses them
/// (click, ⇧-click, ⌘-click, ⌘A), and acted on from the toolbar, the menus, a row's context menu, a swipe, or the page's
/// single keys while the list has the focus.
struct MailboxTable: NSViewRepresentable {
    let list: MailListModel
    let mailbox: Mailbox
    /// The rows chosen as the mailbox holds them: the table follows a choice made elsewhere (a key, the palette, a verb
    /// moving on to the next row).
    let chosen: [MailCopy]
    /// Whether each row says its account: everywhere but one account's own folder.
    let showsAccount: Bool

    func makeCoordinator() -> MailboxTableController {
        MailboxTableController(list: list, mailbox: mailbox)
    }

    func makeNSView(context: Context) -> NSScrollView {
        context.coordinator.scrollView
    }

    func updateNSView(_ scrollView: NSScrollView, context: Context) {
        let controller = context.coordinator
        if controller.showsAccount != showsAccount {
            controller.showsAccount = showsAccount
            controller.redrawShown()
        }
        controller.follow(chosen)
    }

    static func dismantleNSView(_ scrollView: NSScrollView, coordinator: MailboxTableController) {
        coordinator.stop()
    }
}

/// The table's data source, delegate and context menu. It tells the list which rows the scroll position shows, once a
/// frame at most, redraws only the shown rows a page changed, and keeps the rows chosen by their copies as rows move.
@MainActor
final class MailboxTableController: NSObject, NSTableViewDataSource, NSTableViewDelegate, NSMenuDelegate, ListDisplay {
    let scrollView = NSScrollView()
    let table = MailboxTableView()
    private let list: MailListModel
    private weak var mailbox: Mailbox?
    var showsAccount = true
    /// A choice the mailbox asked for is being shown: the table doesn't tell it back.
    private var following = false
    private lazy var report = OncePerFrame { [weak self] in self?.reportVisible() }

    init(list: MailListModel, mailbox: Mailbox) {
        self.list = list
        self.mailbox = mailbox
        super.init()
        table.style = .inset
        table.headerView = nil
        table.usesAutomaticRowHeights = false
        table.rowSizeStyle = .custom
        table.rowHeight = MailRowCell.height
        // Rows touch, so row n starts at n times the row height.
        table.intercellSpacing = .zero
        table.allowsMultipleSelection = true
        table.allowsEmptySelection = true
        table.allowsColumnReordering = false
        table.allowsColumnResizing = false
        let column = NSTableColumn(identifier: MailRowCell.identifier)
        column.resizingMask = .autoresizingMask
        table.addTableColumn(column)
        table.columnAutoresizingStyle = .lastColumnOnlyAutoresizingStyle
        table.dataSource = self
        table.delegate = self
        table.mailbox = mailbox
        table.setAccessibilityLabel(String(localized: "Messages"))
        let menu = NSMenu()
        menu.delegate = self
        table.menu = menu

        scrollView.documentView = table
        scrollView.hasVerticalScroller = true
        scrollView.autohidesScrollers = true
        let clip = scrollView.contentView
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

    // MARK: The scroll position, once a frame

    @objc private func moved(_ note: Notification) {
        report.request(framesPerSecond: scrollView.window?.screen?.maximumFramesPerSecond ?? 60)
    }

    private func reportVisible() {
        let shown = table.rows(in: table.visibleRect)
        list.setVisible(shown.location..<(shown.location + shown.length))
    }

    // MARK: Pages

    func rowsChanged(_ positions: IndexSet, totalChanged: Bool) {
        if totalChanged {
            table.noteNumberOfRowsChanged()
            moved(Notification(name: NSView.boundsDidChangeNotification))
        }
        table.enumerateAvailableRowViews { rowView, row in
            guard positions.contains(row), let cell = rowView.view(atColumn: 0) as? MailRowCell else { return }
            cell.show(list.copy(at: row), account: namer)
        }
        // New mail above the rows chosen moves them: keep the rows, not the positions.
        follow(mailbox?.marked ?? [], scroll: false)
    }

    /// How a row names its account, where the list says each row's.
    private var namer: ((String) -> String)? {
        guard showsAccount, let mailbox else { return nil }
        return { mailbox.name(of: $0) }
    }

    func redrawShown() {
        table.enumerateAvailableRowViews { rowView, row in
            (rowView.view(atColumn: 0) as? MailRowCell)?.show(list.copy(at: row), account: namer)
        }
    }

    /// Show `copies` chosen, where their rows are held, and scroll the first into view.
    func follow(_ copies: [MailCopy], scroll: Bool = true) {
        var positions = IndexSet()
        for copy in copies {
            if let position = list.position(of: copy) { positions.insert(position) }
        }
        let current = table.selectedRowIndexes
        // Rows chosen that aren't held stay chosen while the list hasn't moved.
        let keep = current.filter { list.copy(at: $0) == nil && !(mailbox?.unheld.isEmpty ?? true) }
        positions.formUnion(IndexSet(keep))
        guard positions != current else { return }
        following = true
        table.selectRowIndexes(positions, byExtendingSelection: false)
        following = false
        if scroll, let first = positions.first, copies.count == 1 { table.scrollRowToVisible(first) }
    }

    // MARK: NSTableViewDataSource, NSTableViewDelegate

    func numberOfRows(in tableView: NSTableView) -> Int {
        list.total
    }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let cell = tableView.makeView(withIdentifier: MailRowCell.identifier, owner: nil) as? MailRowCell ?? MailRowCell()
        cell.show(list.copy(at: row), account: namer)
        return cell
    }

    func tableViewSelectionDidChange(_ notification: Notification) {
        guard !following, let mailbox else { return }
        var rows: [API.FolderRow] = []
        var unheld = IndexSet()
        for position in table.selectedRowIndexes {
            if let row = list.copy(at: position) { rows.append(row) } else { unheld.insert(position) }
        }
        mailbox.chose(rows, unheld: unheld)
    }

    /// A swipe's actions, as the page's row has them for the pointer: read or unread from the left; archive and
    /// delete from the right.
    func tableView(_ tableView: NSTableView, rowActionsForRow row: Int, edge: NSTableView.RowActionEdge) -> [NSTableViewRowAction] {
        guard let mailbox, let mail = list.copy(at: row) else { return [] }
        if edge == .leading {
            let read = NSTableViewRowAction(style: .regular, title: mail.seen ? String(localized: "Unread") : String(localized: "Read")) { _, _ in
                mailbox.setSeen(!mail.seen, rows: [mail])
            }
            read.backgroundColor = .controlAccentColor
            return [read]
        }
        let delete = NSTableViewRowAction(style: .destructive, title: String(localized: "Delete")) { _, _ in
            mailbox.file(.trash, rows: [mail])
        }
        let archive = NSTableViewRowAction(style: .regular, title: String(localized: "Archive")) { _, _ in
            mailbox.file(.archive, rows: [mail])
        }
        archive.backgroundColor = .systemIndigo
        return [delete, archive]
    }

    // MARK: The context menu

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let clicked = table.clickedRow
        guard clicked >= 0, let mailbox else { return }
        // A row's menu acts on it: on the rows chosen when it is one of them, else on it alone, which it chooses.
        if !table.selectedRowIndexes.contains(clicked) {
            table.selectRowIndexes(IndexSet(integer: clicked), byExtendingSelection: false)
        }
        for item in RowMenu.items(for: mailbox, clicked: list.copy(at: clicked)) { menu.addItem(item) }
    }
}

/// The table, taking the page's single keys while it has the focus (app/commands.ts): j and k, e, d, s, u, m, z, g
/// then a letter, and the rest. Typing anywhere else never reaches them, and the setting that turns single keys off
/// (`single_keys`) leaves only Delete, which deletes in every Mac list.
final class MailboxTableView: NSTableView {
    weak var mailbox: Mailbox?
    private var chordAt: Date?

    override func keyDown(with event: NSEvent) {
        guard let mailbox, event.modifierFlags.intersection([.command, .control, .option]).isEmpty,
              let typed = event.charactersIgnoringModifiers, let key = typed.first else {
            return super.keyDown(with: event)
        }
        let scalar = key.unicodeScalars.first.map { Int($0.value) } ?? 0
        if scalar == NSDeleteCharacter || scalar == NSDeleteFunctionKey || scalar == NSBackspaceCharacter {
            return mailbox.file(.trash)
        }
        if key == "\u{1b}" {
            return mailbox.chosenRows.count > 1 ? mailbox.clearMarks() : mailbox.leaveSearch()
        }
        guard mailbox.singleKeys, event.modifierFlags.intersection([.shift]).isEmpty || "#?".contains(key) else {
            return super.keyDown(with: event)
        }
        let lower = Character(key.lowercased())
        if let at = chordAt, Date().timeIntervalSince(at) < 1.8 {
            chordAt = nil
            return mailbox.go(chord: lower)
        }
        if RowKeys.run(lower, on: mailbox, chord: { chordAt = Date() }) { return }
        super.keyDown(with: event)
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

/// A row's context menu: the mailbox's verbs on the rows chosen, a move to one of the account's folders, and the
/// sender's card and mail.
@MainActor
enum RowMenu {
    static func items(for mailbox: Mailbox, clicked: API.FolderRow?) -> [NSMenuItem] {
        let rows = mailbox.chosenRows.isEmpty ? clicked.map { [$0] } ?? [] : mailbox.chosenRows
        guard let first = rows.first else { return [] }
        var items: [NSMenuItem] = [
            Item(String(localized: "Archive"), "archivebox") { mailbox.file(.archive, rows: rows) },
            Item(String(localized: "Delete"), "trash") { mailbox.file(.trash, rows: rows) },
            Item(String(localized: "Mark as Junk"), "xmark.bin") { mailbox.file(.junk, rows: rows) },
        ]
        if Set(rows.map(\.account)).count == 1 {
            let from = Set(rows.map(\.folder))
            let ask = MoveAsk(account: first.account, from: from.count == 1 ? first.folder : nil)
            let move = NSMenuItem(title: String(localized: "Move To"), action: nil, keyEquivalent: "")
            move.image = NSImage(systemSymbolName: "folder", accessibilityDescription: nil)
            let folders = NSMenu()
            for folder in mailbox.destinations(ask) {
                folders.addItem(Item(Words.role(folder), nil) {
                    mailbox.chose(rows)
                    mailbox.move(to: folder)
                })
            }
            folders.addItem(.separator())
            folders.addItem(Item(String(localized: "Other Folder…"), nil) {
                mailbox.chose(rows)
                mailbox.moving = ask
            })
            move.submenu = folders
            items.append(move)
        }
        items.append(.separator())
        let anyUnread = rows.contains { !$0.seen }
        items.append(Item(anyUnread ? String(localized: "Mark as Read") : String(localized: "Mark as Unread"),
                          anyUnread ? "envelope.open" : "envelope.badge") { mailbox.setSeen(anyUnread, rows: rows) })
        items.append(Item(rows.count == 1 && first.flagged ? String(localized: "Unstar") : String(localized: "Star"), "star") {
            mailbox.star(rows: rows)
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

/// One row, drawn into one layer with Core Text, as the step-0 list's are: the sender's monogram, who and when with the
/// row's badges (a conversation's count, a star, a paperclip, a junk tag, the account), the subject, and the AI's
/// summary or the snippet, after the next step the AI wrote when there is one. Grey bars stand where a row's page
/// hasn't come.
final class MailRowCell: NSTableCellView {
    static let identifier = NSUserInterfaceItemIdentifier("mail")
    static let height: CGFloat = 66

    private static let senderFont = NSFont.preferredFont(forTextStyle: .body)
    private static let senderBold = NSFont.systemFont(ofSize: senderFont.pointSize, weight: .semibold)
    private static let subjectFont = NSFont.preferredFont(forTextStyle: .callout)
    private static let gistFont = NSFont.preferredFont(forTextStyle: .subheadline)
    private static let smallFont = NSFont.systemFont(ofSize: NSFont.preferredFont(forTextStyle: .caption1).pointSize, weight: .medium)
    private static let initialsFont = NSFont.systemFont(ofSize: 12, weight: .semibold)

    private(set) var row: API.FolderRow?
    private var account: String?
    private var shown = false
    private var lines: Lines?

    /// Each line's text, set once when the row is shown.
    private struct Lines {
        var sender: TextLine
        var when: TextLine
        var subject: TextLine
        var gist: TextLine
        var step: TextLine?
        var initials: TextLine
        var count: TextLine?
        var junk: TextLine?
        var account: TextLine?
    }

    init() {
        super.init(frame: NSRect(x: 0, y: 0, width: 400, height: Self.height))
        identifier = Self.identifier
        wantsLayer = true
        layerContentsRedrawPolicy = .onSetNeedsDisplay
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not from a nib") }

    override var isFlipped: Bool { true }

    override var backgroundStyle: NSView.BackgroundStyle {
        didSet { if backgroundStyle != oldValue { needsDisplay = true } }
    }

    /// Show `row`, naming its account with `name` where the list says each row's account.
    func show(_ row: API.FolderRow?, account name: ((String) -> String)?) {
        let account = row.flatMap { row in name.map { $0(row.account) } }
        guard !shown || row != self.row || account != self.account else { return }
        shown = true
        self.row = row
        self.account = account
        lines = row.map { row in
            let step = row.action.trimmingCharacters(in: .whitespaces)
            return Lines(
                sender: TextLine(row.sender, row.seen ? Self.senderFont : Self.senderBold),
                when: TextLine(row.when, Self.gistFont),
                subject: TextLine(row.subject.isEmpty ? String(localized: "(no subject)") : row.subject, Self.subjectFont),
                gist: TextLine(row.gist, Self.gistFont),
                step: step.isEmpty ? nil : TextLine(step, Self.smallFont),
                initials: TextLine(Monogram.initials(name: row.fromName, address: row.fromAddr), Self.initialsFont),
                count: row.conversationCount.map { TextLine(String($0), Self.smallFont) },
                junk: row.junkTag.map { TextLine($0, Self.smallFont) },
                account: account.map { TextLine($0, Self.smallFont) })
        }
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        let rtl = userInterfaceLayoutDirection == .rightToLeft
        // Frames are worked out left to right, and mirrored for a right-to-left language.
        func mirrored(_ rect: NSRect) -> NSRect {
            rtl ? NSRect(x: bounds.width - rect.maxX, y: rect.minY, width: rect.width, height: rect.height) : rect
        }
        let selected = backgroundStyle == .emphasized
        let primary: NSColor = selected ? .alternateSelectedControlTextColor : .labelColor
        let secondary: NSColor = selected ? .alternateSelectedControlTextColor.withAlphaComponent(0.8) : .secondaryLabelColor
        let pad: CGFloat = 8
        let avatar: CGFloat = 32
        let textX = pad + 12 + avatar + 10
        let textWidth = max(0, bounds.width - textX - pad)
        let heights = [Self.senderFont, Self.subjectFont, Self.gistFont].map(Self.lineHeight)
        let top = ((Self.height - heights.reduce(0, +) - 6) / 2).rounded()

        guard let row, let lines else {
            NSColor.quaternaryLabelColor.setFill()
            NSBezierPath(ovalIn: mirrored(NSRect(x: pad + 12, y: (Self.height - avatar) / 2, width: avatar, height: avatar))).fill()
            var y = top + 3
            for length in [140, 240, 320] as [CGFloat] {
                NSBezierPath(roundedRect: mirrored(NSRect(x: textX, y: y, width: min(length, textWidth), height: 9)),
                             xRadius: 3, yRadius: 3).fill()
                y += heights[0] + 2
            }
            return
        }

        if !row.seen {
            (selected ? NSColor.alternateSelectedControlTextColor : .controlAccentColor).setFill()
            NSBezierPath(ovalIn: mirrored(NSRect(x: pad, y: top + (heights[0] - 8) / 2, width: 8, height: 8))).fill()
        }
        // The monogram, in the colour the sender's address always gets.
        let hue = CGFloat(Monogram.hue(row.fromAddr ?? row.fromName)) / 360
        let dark = effectiveAppearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
        NSColor(hue: hue, saturation: 0.38, brightness: dark ? 0.55 : 0.72, alpha: 1).setFill()
        let disc = mirrored(NSRect(x: pad + 12, y: (Self.height - avatar) / 2, width: avatar, height: avatar))
        NSBezierPath(ovalIn: disc).fill()

        // Core Text draws upward from a baseline; the view is flipped, so the glyphs are flipped back.
        context.textMatrix = CGAffineTransform(scaleX: 1, y: -1)
        lines.initials.draw(in: context, x: disc.midX - lines.initials.width / 2,
                            baseline: disc.midY + (Self.initialsFont.capHeight / 2), width: avatar, color: .white,
                            mirrored: { $0 })

        // The first line: the sender, then from the right when it came, the account, a junk tag, a paperclip, a star
        // and a conversation's count.
        let firstBaseline = top + Self.senderFont.ascender
        var right = bounds.width - pad
        right -= lines.when.width
        lines.when.draw(in: context, x: right, baseline: firstBaseline, width: lines.when.width, color: secondary, mirrored: mirrored)
        right -= 6
        for (chip, tint) in [(lines.account, NSColor.secondaryLabelColor), (lines.junk, NSColor.systemRed)] {
            guard let chip else { continue }
            let width = min(chip.width, 110) + 10
            right -= width
            let box = NSRect(x: right, y: top + (heights[0] - 15) / 2, width: width, height: 15)
            (selected ? NSColor.white.withAlphaComponent(0.2) : tint.withAlphaComponent(0.14)).setFill()
            NSBezierPath(roundedRect: mirrored(box), xRadius: 4, yRadius: 4).fill()
            chip.draw(in: context, x: right + 5, baseline: box.minY + 11, width: width - 10,
                      color: selected ? primary : tint, mirrored: mirrored)
            right -= 5
        }
        for (on, symbol) in [(row.hasAttach, Self.paperclip(selected)), (row.flagged, Self.star(selected))] where on {
            symbol.draw(in: mirrored(NSRect(x: right - 13, y: top + (heights[0] - 13) / 2, width: 13, height: 13)))
            right -= 13 + 5
        }
        if let count = lines.count {
            let width = max(count.width + 8, 16)
            right -= width
            let box = NSRect(x: right, y: top + (heights[0] - 15) / 2, width: width, height: 15)
            (selected ? NSColor.white.withAlphaComponent(0.25) : NSColor.quaternaryLabelColor).setFill()
            NSBezierPath(roundedRect: mirrored(box), xRadius: 7.5, yRadius: 7.5).fill()
            count.draw(in: context, x: right + (width - count.width) / 2, baseline: box.minY + 11, width: count.width,
                       color: selected ? primary : .secondaryLabelColor, mirrored: mirrored)
            right -= 5
        }
        lines.sender.draw(in: context, x: textX, baseline: firstBaseline, width: right - textX, color: primary, mirrored: mirrored)

        let subjectBaseline = top + heights[0] + 3 + Self.subjectFont.ascender
        lines.subject.draw(in: context, x: textX, baseline: subjectBaseline, width: textWidth, color: primary, mirrored: mirrored)

        // The third line: the next step the AI wrote, as a chip, then the summary or the snippet.
        let thirdTop = top + heights[0] + heights[1] + 6
        var gistX = textX
        if let step = lines.step {
            let width = min(step.width + 10, textWidth * 0.6)
            let box = NSRect(x: textX, y: thirdTop + (heights[2] - 15) / 2, width: width, height: 15)
            (selected ? NSColor.white.withAlphaComponent(0.25) : NSColor.systemOrange.withAlphaComponent(0.18)).setFill()
            NSBezierPath(roundedRect: mirrored(box), xRadius: 4, yRadius: 4).fill()
            step.draw(in: context, x: textX + 5, baseline: box.minY + 11, width: width - 10,
                      color: selected ? primary : .systemOrange, mirrored: mirrored)
            gistX += width + 6
        }
        lines.gist.draw(in: context, x: gistX, baseline: thirdTop + Self.gistFont.ascender, width: textX + textWidth - gistX,
                        color: secondary, mirrored: mirrored)
    }

    // The row is drawn, not made of text fields, so VoiceOver reads it as one piece of text in the page's order.
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .staticText }
    override func accessibilityLabel() -> String? {
        guard let row else { return String(localized: "Loading") }
        var said: [String] = []
        if !row.seen { said.append(String(localized: "Unread")) }
        said.append(row.sender)
        if let count = row.conversationCount { said.append("\(count) messages") }
        said.append(row.subject)
        said.append(row.when)
        if row.flagged { said.append(String(localized: "Starred")) }
        if row.hasAttach { said.append(String(localized: "Has attachments")) }
        if let junk = row.junkTag { said.append(junk) }
        if let account { said.append(account) }
        if !row.summary.isEmpty { said.append("Summary: \(row.summary)") } else { said.append(row.snippet) }
        if !row.action.isEmpty { said.append("Next step: \(row.action)") }
        return said.filter { !$0.isEmpty }.joined(separator: ", ")
    }

    private static func lineHeight(_ font: NSFont) -> CGFloat {
        ceil(font.ascender - font.descender + font.leading)
    }

    private static func star(_ selected: Bool) -> NSImage {
        symbol("star.fill", selected ? .alternateSelectedControlTextColor : .systemYellow)
    }

    private static func paperclip(_ selected: Bool) -> NSImage {
        symbol("paperclip", selected ? .alternateSelectedControlTextColor : .secondaryLabelColor)
    }

    private static func symbol(_ name: String, _ color: NSColor) -> NSImage {
        let configuration = NSImage.SymbolConfiguration(pointSize: 11, weight: .regular)
            .applying(NSImage.SymbolConfiguration(paletteColors: [color]))
        return NSImage(systemSymbolName: name, accessibilityDescription: nil)?.withSymbolConfiguration(configuration)
            ?? NSImage()
    }
}

/// One line of text, set once when its row is shown and cut short with an ellipsis when it is too wide. Its colour is
/// taken from the context when it draws, so it follows light, dark and a chosen row.
@MainActor
struct TextLine {
    private static let ellipsis = CTLineCreateWithAttributedString(NSAttributedString(string: "\u{2026}"))
    let line: CTLine
    let width: CGFloat

    init(_ text: String, _ font: NSFont) {
        let attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            NSAttributedString.Key(kCTForegroundColorFromContextAttributeName as String): true,
        ]
        // One line: a subject or a snippet with a line break in it is drawn on one.
        let flat = text.replacingOccurrences(of: "\n", with: " ").replacingOccurrences(of: "\r", with: " ")
        line = CTLineCreateWithAttributedString(NSAttributedString(string: flat, attributes: attributes))
        width = ceil(CTLineGetTypographicBounds(line, nil, nil, nil))
    }

    func draw(in context: CGContext, x: CGFloat, baseline: CGFloat, width available: CGFloat, color: NSColor,
              mirrored: (NSRect) -> NSRect) {
        guard available > 0 else { return }
        var shown = line
        if width > available {
            guard let cut = CTLineCreateTruncatedLine(line, available, .end, Self.ellipsis) else { return }
            shown = cut
        }
        let drawn = min(width, available)
        let origin = mirrored(NSRect(x: x, y: baseline, width: drawn, height: 0)).minX
        context.setFillColor(color.cgColor)
        context.textPosition = CGPoint(x: origin, y: baseline)
        CTLineDraw(shown, context)
    }
}
#endif
