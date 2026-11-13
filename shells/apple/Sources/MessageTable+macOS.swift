#if os(macOS)
import AppKit
import SwiftUI

/// The message list on the Mac: a view-based `NSTableView` with one fixed row height, behind a thin representable.
/// SwiftUI's `List` kept state for every row it had shown and spent up to 300 ms a frame deep in a long list;
/// a table with fixed rows knows where every row is without asking, and reuses a screenful of cells.
struct MessageTable: NSViewRepresentable {
    let model: ListModel

    func makeCoordinator() -> MessageTableController {
        MessageTableController(model)
    }

    func makeNSView(context: Context) -> NSScrollView {
        context.coordinator.scrollView
    }

    func updateNSView(_ scrollView: NSScrollView, context: Context) {}

    static func dismantleNSView(_ scrollView: NSScrollView, coordinator: MessageTableController) {
        coordinator.stop()
    }
}

/// The table's data source and delegate. It tells the model which rows the scroll position shows, once a frame at
/// most, and redraws only the shown rows a batch changed.
@MainActor
final class MessageTableController: NSObject, NSTableViewDataSource, NSTableViewDelegate, ListDisplay {
    let scrollView = NSScrollView()
    private let table = NSTableView()
    private let model: ListModel
    private lazy var report = OncePerFrame { [weak self] in self?.reportVisible() }

    init(_ model: ListModel) {
        self.model = model
        super.init()
        table.style = .plain
        table.headerView = nil
        table.usesAutomaticRowHeights = false
        table.rowSizeStyle = .custom
        table.rowHeight = MessageCell.height
        // Rows touch, so row n starts at n times the row height.
        table.intercellSpacing = .zero
        table.gridStyleMask = []
        // Step 0 opens nothing, so rows are not selected, as the list before did not select them.
        table.selectionHighlightStyle = .none
        table.allowsColumnReordering = false
        table.allowsColumnResizing = false
        table.focusRingType = .none
        let column = NSTableColumn(identifier: MessageCell.identifier)
        column.resizingMask = .autoresizingMask
        table.addTableColumn(column)
        table.columnAutoresizingStyle = .lastColumnOnlyAutoresizingStyle
        table.dataSource = self
        table.delegate = self

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
        model.display = self
    }

    func stop() {
        NotificationCenter.default.removeObserver(self)
        if model.display === self { model.display = nil }
    }

    // MARK: The scroll position, once a frame

    @objc private func moved(_ note: Notification) {
        requestReport()
    }

    private func requestReport() {
        report.request(framesPerSecond: scrollView.window?.screen?.maximumFramesPerSecond ?? 60)
    }

    private func reportVisible() {
        let shown = table.rows(in: table.visibleRect)
        model.setVisible(shown.location..<(shown.location + shown.length))
    }

    // MARK: Batches

    func rowsChanged(_ positions: IndexSet, totalChanged: Bool) {
        if totalChanged {
            table.noteNumberOfRowsChanged()
            requestReport()
        }
        // Every row the table has made, on screen or prepared just past it; the rest are drawn fresh when they come.
        table.enumerateAvailableRowViews { rowView, row in
            guard positions.contains(row), let cell = rowView.view(atColumn: 0) as? MessageCell else { return }
            cell.show(model.held[row])
        }
    }

    // MARK: NSTableViewDataSource, NSTableViewDelegate

    func numberOfRows(in tableView: NSTableView) -> Int {
        model.total
    }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let cell = tableView.makeView(withIdentifier: MessageCell.identifier, owner: nil) as? MessageCell ?? MessageCell()
        cell.show(model.held[row])
        return cell
    }

    func selectionShouldChange(in tableView: NSTableView) -> Bool {
        false
    }
}

/// One row: what the core sent for it, or grey bars where its text will go until its batch arrives.
/// It draws itself into one layer with Core Text: as four text fields, a screen of new rows took nearly twice as long
/// to make and draw. Every row is the same height, so nothing needs laying out either.
final class MessageCell: NSView {
    static let identifier = NSUserInterfaceItemIdentifier("message")
    static let height: CGFloat = 56

    private static let body = NSFont.preferredFont(forTextStyle: .body)
    private static let bodyBold = NSFont.systemFont(ofSize: body.pointSize, weight: .semibold)
    private static let subheadline = NSFont.preferredFont(forTextStyle: .subheadline)
    private static let caption = NSFont.preferredFont(forTextStyle: .caption1)
    private static let paperclip = symbol("paperclip", .secondaryLabelColor)
    private static let flag = symbol("flag.fill", .systemOrange)
    private static let ellipsis = Line("\u{2026}", caption)

    private var row: MessageRow?
    private var shown = false
    private var sender: Line?
    private var date: Line?
    private var subject: Line?
    private var snippet: Line?

    init() {
        super.init(frame: NSRect(x: 0, y: 0, width: 400, height: Self.height))
        identifier = Self.identifier
        wantsLayer = true
        layerContentsRedrawPolicy = .onSetNeedsDisplay
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not from a nib") }

    override var isFlipped: Bool { true }

    func show(_ row: MessageRow?) {
        guard !shown || row != self.row else { return }
        shown = true
        self.row = row
        sender = row.map { Line($0.sender, $0.unread ? Self.bodyBold : Self.body) }
        date = row.map { Line(RowDate.text($0.date), Self.caption) }
        subject = row.map { Line($0.subject, Self.subheadline) }
        snippet = row.map { Line($0.snippet, Self.caption) }
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        let rtl = userInterfaceLayoutDirection == .rightToLeft
        // Frames are worked out left to right, and mirrored for a right-to-left language.
        func mirrored(_ rect: NSRect) -> NSRect {
            rtl ? NSRect(x: bounds.width - rect.maxX, y: rect.minY, width: rect.width, height: rect.height) : rect
        }
        let pad: CGFloat = 12
        let lineHeights = [Self.body, Self.subheadline, Self.caption].map(Self.lineHeight)
        let top = ((Self.height - lineHeights.reduce(0, +) - 4) / 2).rounded()
        let textX = pad + 16
        let textWidth = max(0, bounds.width - textX - pad)

        guard let row, let sender, let date, let subject, let snippet else {
            NSColor.quaternaryLabelColor.setFill()
            var y = ((Self.height - 39) / 2).rounded()
            for length in [140, 240, 320] as [CGFloat] {
                let bar = mirrored(NSRect(x: textX, y: y, width: min(length, textWidth), height: 9))
                NSBezierPath(roundedRect: bar, xRadius: 3, yRadius: 3).fill()
                y += 15
            }
            return
        }
        if row.unread {
            NSColor.controlAccentColor.setFill()
            NSBezierPath(ovalIn: mirrored(NSRect(x: pad, y: top + (lineHeights[0] - 8) / 2, width: 8, height: 8))).fill()
        }
        // Core Text draws upward from a baseline; the view is flipped, so the glyphs are flipped back.
        context.textMatrix = CGAffineTransform(scaleX: 1, y: -1)
        let firstBaseline = top + Self.body.ascender

        // The first line: the sender, then from the right the date, a flag and a paperclip.
        var right = bounds.width - pad
        right -= date.width
        date.draw(in: context, x: right, baseline: firstBaseline, width: date.width, color: .secondaryLabelColor,
                  mirrored: mirrored)
        right -= 6
        for (shown, image) in [(row.flagged, Self.flag), (row.attachment, Self.paperclip)] where shown {
            image.draw(in: mirrored(NSRect(x: right - 14, y: top + (lineHeights[0] - 14) / 2, width: 14, height: 14)))
            right -= 14 + 6
        }
        sender.draw(in: context, x: textX, baseline: firstBaseline, width: right - textX, color: .labelColor,
                    mirrored: mirrored)
        subject.draw(in: context, x: textX, baseline: top + lineHeights[0] + 2 + Self.subheadline.ascender,
                     width: textWidth, color: .labelColor, mirrored: mirrored)
        snippet.draw(in: context, x: textX, baseline: top + lineHeights[0] + lineHeights[1] + 4 + Self.caption.ascender,
                     width: textWidth, color: .secondaryLabelColor, mirrored: mirrored)
    }

    // The rows are drawn, not made of text fields, so VoiceOver reads each row as one piece of text.
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .staticText }
    override func accessibilityLabel() -> String? {
        guard let row else { return String(localized: "Loading") }
        return [row.sender, row.subject, RowDate.text(row.date), row.snippet].filter { !$0.isEmpty }.joined(separator: ", ")
    }

    private static func lineHeight(_ font: NSFont) -> CGFloat {
        ceil(font.ascender - font.descender + font.leading)
    }

    private static func symbol(_ name: String, _ color: NSColor) -> NSImage {
        let configuration = NSImage.SymbolConfiguration(pointSize: 11, weight: .regular)
            .applying(NSImage.SymbolConfiguration(paletteColors: [color]))
        return NSImage(systemSymbolName: name, accessibilityDescription: nil)?.withSymbolConfiguration(configuration)
            ?? NSImage()
    }

    /// One line of text, set once when the row is shown and cut short with an ellipsis when it is too wide.
    /// Its colour is taken from the context when it draws, so it follows light and dark.
    @MainActor
    private struct Line {
        let line: CTLine
        let width: CGFloat

        init(_ text: String, _ font: NSFont) {
            let attributes: [NSAttributedString.Key: Any] = [
                .font: font,
                NSAttributedString.Key(kCTForegroundColorFromContextAttributeName as String): true,
            ]
            line = CTLineCreateWithAttributedString(NSAttributedString(string: text, attributes: attributes))
            width = ceil(CTLineGetTypographicBounds(line, nil, nil, nil))
        }

        func draw(in context: CGContext, x: CGFloat, baseline: CGFloat, width available: CGFloat, color: NSColor,
                  mirrored: (NSRect) -> NSRect) {
            guard available > 0 else { return }
            var shown = line
            if width > available {
                guard let cut = CTLineCreateTruncatedLine(line, available, .end, MessageCell.ellipsis.line) else { return }
                shown = cut
            }
            let drawn = width > available ? available : width
            let origin = mirrored(NSRect(x: x, y: baseline, width: drawn, height: 0)).minX
            context.setFillColor(color.cgColor)
            context.textPosition = CGPoint(x: origin, y: baseline)
            CTLineDraw(shown, context)
        }
    }
}
#endif
