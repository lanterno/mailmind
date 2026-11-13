#if os(iOS)
import SwiftUI
import UIKit

/// The message list on iPhone and iPad: a `UICollectionView` with one fixed row height, behind a thin representable.
/// Its layout places rows by arithmetic and answers only for the rows in a rect, so a list of 100,000 costs what a
/// screenful does, with nothing measured or cached per row.
struct MessageTable: UIViewRepresentable {
    let model: ListModel

    func makeCoordinator() -> MessageTableController {
        MessageTableController(model)
    }

    func makeUIView(context: Context) -> UICollectionView {
        context.coordinator.collectionView
    }

    func updateUIView(_ collectionView: UICollectionView, context: Context) {}

    static func dismantleUIView(_ collectionView: UICollectionView, coordinator: MessageTableController) {
        coordinator.stop()
    }
}

/// The collection view's data source and delegate. It tells the model which rows the scroll position shows, once a
/// frame at most, and redraws only the shown rows a batch changed.
@MainActor
final class MessageTableController: NSObject, UICollectionViewDataSource, UICollectionViewDelegate, ListDisplay {
    let collectionView: UICollectionView
    private let layout = FixedRows(height: MessageCell.height)
    private let model: ListModel
    private lazy var report = OncePerFrame { [weak self] in self?.reportVisible() }

    init(_ model: ListModel) {
        self.model = model
        collectionView = UICollectionView(frame: .zero, collectionViewLayout: layout)
        super.init()
        collectionView.backgroundColor = .systemBackground
        collectionView.alwaysBounceVertical = true
        // Step 0 opens nothing, so rows are not selected, as the list before did not select them.
        collectionView.allowsSelection = false
        collectionView.register(MessageCell.self, forCellWithReuseIdentifier: MessageCell.identifier)
        collectionView.dataSource = self
        collectionView.delegate = self
        model.display = self
    }

    func stop() {
        if model.display === self { model.display = nil }
    }

    // MARK: The scroll position, once a frame

    func scrollViewDidScroll(_ scrollView: UIScrollView) {
        moved()
    }

    private func moved() {
        report.request(framesPerSecond: collectionView.window?.windowScene?.screen.maximumFramesPerSecond ?? 60)
    }

    private func reportVisible() {
        model.setVisible(layout.rows(in: collectionView.bounds))
    }

    // MARK: Batches

    func rowsChanged(_ positions: IndexSet, totalChanged: Bool) {
        if totalChanged {
            collectionView.reloadData()
            moved()
            return
        }
        for path in collectionView.indexPathsForVisibleItems where positions.contains(path.item) {
            (collectionView.cellForItem(at: path) as? MessageCell)?.show(model.held[path.item])
        }
    }

    // MARK: UICollectionViewDataSource, UICollectionViewDelegate

    func collectionView(_ collectionView: UICollectionView, numberOfItemsInSection section: Int) -> Int {
        model.total
    }

    func collectionView(_ collectionView: UICollectionView, cellForItemAt indexPath: IndexPath) -> UICollectionViewCell {
        let cell = collectionView.dequeueReusableCell(withReuseIdentifier: MessageCell.identifier, for: indexPath)
        (cell as? MessageCell)?.show(model.held[indexPath.item])
        return cell
    }

    func collectionView(_ collectionView: UICollectionView, willDisplay cell: UICollectionViewCell,
                        forItemAt indexPath: IndexPath) {
        // A prefetched cell was made before its batch may have arrived, and a row coming on screen moves the window.
        (cell as? MessageCell)?.show(model.held[indexPath.item])
        moved()
    }
}

/// Rows of one height, one under the other: row n is at n times the height, so nothing is measured or cached.
final class FixedRows: UICollectionViewLayout {
    private let height: CGFloat
    private var count = 0

    init(height: CGFloat) {
        self.height = height
        super.init()
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not from a nib") }

    /// The rows any part of `rect` covers.
    func rows(in rect: CGRect) -> Range<Int> {
        let first = max(0, Int((rect.minY / height).rounded(.down)))
        let end = min(count, Int((rect.maxY / height).rounded(.up)))
        return first < end ? first..<end : first..<first
    }

    override func prepare() {
        super.prepare()
        count = collectionView.map { $0.numberOfSections > 0 ? $0.numberOfItems(inSection: 0) : 0 } ?? 0
    }

    override var collectionViewContentSize: CGSize {
        CGSize(width: collectionView?.bounds.width ?? 0, height: CGFloat(count) * height)
    }

    override func layoutAttributesForElements(in rect: CGRect) -> [UICollectionViewLayoutAttributes]? {
        rows(in: rect).map(attributes)
    }

    override func layoutAttributesForItem(at indexPath: IndexPath) -> UICollectionViewLayoutAttributes? {
        indexPath.item < count ? attributes(indexPath.item) : nil
    }

    override func shouldInvalidateLayout(forBoundsChange newBounds: CGRect) -> Bool {
        newBounds.width != collectionView?.bounds.width
    }

    private func attributes(_ row: Int) -> UICollectionViewLayoutAttributes {
        let attributes = UICollectionViewLayoutAttributes(forCellWith: IndexPath(item: row, section: 0))
        attributes.frame = CGRect(x: 0, y: CGFloat(row) * height, width: collectionView?.bounds.width ?? 0, height: height)
        return attributes
    }
}

/// One row: what the core sent for it, or grey bars where its text will go until its batch arrives.
/// Frames are set by hand: every row is the same height, so there is nothing for Auto Layout to solve.
final class MessageCell: UICollectionViewCell {
    static let identifier = "message"

    // The text size is read once, at launch; the row height follows it so three lines always fit.
    private static let body = UIFont.preferredFont(forTextStyle: .body)
    private static let bodyBold = UIFont.systemFont(ofSize: body.pointSize, weight: .semibold)
    private static let subheadline = UIFont.preferredFont(forTextStyle: .subheadline)
    private static let caption = UIFont.preferredFont(forTextStyle: .caption1)
    private static let lines = [body, subheadline, caption].map { ceil($0.lineHeight) }
    static let height = lines.reduce(0, +) + 4 + 16

    private let dot = MessageCell.fill(.tintColor, radius: 4)
    private let sender = MessageCell.label(body, .label)
    private let date = MessageCell.label(caption, .secondaryLabel)
    private let subject = MessageCell.label(subheadline, .label)
    private let snippet = MessageCell.label(caption, .secondaryLabel)
    private let clip = MessageCell.symbol("paperclip", .secondaryLabel)
    private let flag = MessageCell.symbol("flag.fill", .systemOrange)
    private let bars = [140, 240, 320].map { _ in MessageCell.fill(.quaternarySystemFill, radius: 3) }
    private let separator = MessageCell.fill(.separator, radius: 0)
    private var row: MessageRow?
    private var shown = false

    override init(frame: CGRect) {
        super.init(frame: frame)
        for view in [dot, sender, date, subject, snippet, clip, flag, separator] + bars { contentView.addSubview(view) }
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not from a nib") }

    func show(_ row: MessageRow?) {
        guard !shown || row != self.row else { return }
        shown = true
        self.row = row
        for bar in bars { bar.isHidden = row != nil }
        for view in [sender, date, subject, snippet] { view.isHidden = row == nil }
        if let row {
            dot.isHidden = !row.unread
            sender.text = row.sender
            sender.font = row.unread ? Self.bodyBold : Self.body
            date.text = RowDate.text(row.date)
            subject.text = row.subject
            snippet.text = row.snippet
            clip.isHidden = !row.attachment
            flag.isHidden = !row.flagged
        } else {
            dot.isHidden = true
            clip.isHidden = true
            flag.isHidden = true
        }
        setNeedsLayout()
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        let width = contentView.bounds.width
        // The margins grow with the safe area, as on an iPhone held sideways.
        let leading = max(16, contentView.layoutMargins.left)
        let trailing = max(16, contentView.layoutMargins.right)
        let lines = Self.lines
        let block = lines.reduce(0, +) + 4
        let top = ((Self.height - block) / 2).rounded()
        let textX = leading + 16
        let textWidth = max(0, width - textX - trailing)
        place(dot, CGRect(x: leading, y: top + (lines[0] - 8) / 2, width: 8, height: 8))
        let hairline = 1 / max(traitCollection.displayScale, 1)
        place(separator, CGRect(x: textX, y: Self.height - hairline, width: width - textX, height: hairline))

        if row == nil {
            var y = ((Self.height - 39) / 2).rounded()
            for (bar, length) in zip(bars, [140, 240, 320] as [CGFloat]) {
                place(bar, CGRect(x: textX, y: y, width: min(length, textWidth), height: 9))
                y += 15
            }
            return
        }
        // The first line: the sender, then from the trailing edge the date, a flag and a paperclip.
        var right = width - trailing
        let dateWidth = ceil(date.sizeThatFits(CGSize(width: CGFloat.greatestFiniteMagnitude, height: lines[2])).width)
        place(date, CGRect(x: right - dateWidth, y: top + lines[0] - lines[2] - 1, width: dateWidth, height: lines[2]))
        right -= dateWidth + 6
        for icon in [flag, clip] where !icon.isHidden {
            place(icon, CGRect(x: right - 16, y: top + (lines[0] - 16) / 2, width: 16, height: 16))
            right -= 16 + 6
        }
        place(sender, CGRect(x: textX, y: top, width: max(0, right - textX), height: lines[0]))
        place(subject, CGRect(x: textX, y: top + lines[0] + 2, width: textWidth, height: lines[1]))
        place(snippet, CGRect(x: textX, y: top + lines[0] + lines[1] + 4, width: textWidth, height: lines[2]))
    }

    /// Frames are worked out left to right, and mirrored for a right-to-left language.
    private func place(_ view: UIView, _ frame: CGRect) {
        var frame = frame
        if effectiveUserInterfaceLayoutDirection == .rightToLeft { frame.origin.x = contentView.bounds.width - frame.maxX }
        view.frame = frame
    }

    private static func label(_ font: UIFont, _ color: UIColor) -> UILabel {
        let label = UILabel()
        label.font = font
        label.textColor = color
        label.lineBreakMode = .byTruncatingTail
        label.numberOfLines = 1
        return label
    }

    private static func symbol(_ name: String, _ color: UIColor) -> UIImageView {
        let view = UIImageView(image: UIImage(systemName: name))
        view.tintColor = color
        view.contentMode = .scaleAspectFit
        view.isHidden = true
        return view
    }

    private static func fill(_ color: UIColor, radius: CGFloat) -> UIView {
        let view = UIView()
        view.backgroundColor = color
        view.layer.cornerRadius = radius
        return view
    }
}
#endif
