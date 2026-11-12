import Foundation
import Observation

/// The rows the core told the shell to keep: a range of positions, and the rows in it the shell has.
/// The core decides the range; this only stores what it sent and forgets what falls outside.
struct HeldRows: Equatable {
    private(set) var first = 0
    private(set) var rows: [MessageRow?] = []

    var range: Range<Int> { first..<(first + rows.count) }

    subscript(index: Int) -> MessageRow? {
        range.contains(index) ? rows[index - first] : nil
    }

    /// Keep what the batch says to keep, add the rows it carries, and drop the rest.
    mutating func apply(_ batch: ViewBatch) {
        let held = Int(batch.heldFirst)..<Int(batch.heldFirst + batch.heldCount)
        var kept = [MessageRow?](repeating: nil, count: held.count)
        for index in held.clamped(to: range) {
            kept[index - held.lowerBound] = rows[index - first]
        }
        for part in batch.ranges {
            for (offset, row) in part.rows.enumerated() {
                let index = Int(part.first) + offset
                if held.contains(index) { kept[index - held.lowerBound] = row }
            }
        }
        first = held.lowerBound
        rows = kept
    }
}

/// One open list as the screen sees it: how many rows there are, and the few near the screen.
/// It decides nothing about mail: the order, the rows and what to keep all come from the core.
@MainActor
@Observable
final class ListModel {
    private(set) var total = 0
    private(set) var held = HeldRows()
    /// Set once, when the first rows reach the screen; measure mode times it.
    private(set) var firstRowsAt: Date?

    @ObservationIgnored private var view: (any ListViewProtocol)?
    @ObservationIgnored private var onScreen = Set<Int>()
    @ObservationIgnored private var lastShown = 0
    @ObservationIgnored private var asked: Range<Int>?
    @ObservationIgnored private var reportQueued = false

    /// No screen shows more rows than this at once. A wider window is rows that appeared before the old
    /// ones disappeared during a jump, so only the rows around the newest one count.
    nonisolated static let widest = 300

    /// Start showing `view`. The first screen is asked for at once rather than after its rows appear.
    func attach(_ view: any ListViewProtocol, firstScreen: Int = 40) {
        self.view = view
        total = Int(view.total())
        ask(0..<min(firstScreen, total))
    }

    func apply(_ batch: ViewBatch) {
        // Setting an observed property redraws whatever reads it, even to the same value: the list reads the total.
        if total != Int(batch.total) { total = Int(batch.total) }
        held.apply(batch)
        if firstRowsAt == nil, !batch.ranges.isEmpty {
            firstRowsAt = Date()
        }
    }

    func appeared(_ index: Int) {
        onScreen.insert(index)
        lastShown = index
        queueReport()
    }

    func disappeared(_ index: Int) {
        onScreen.remove(index)
        queueReport()
    }

    /// Rows appear and disappear one call at a time; the window is reported once they have all landed,
    /// so the core never sees the half-way window of a jump.
    private func queueReport() {
        guard !reportQueued else { return }
        reportQueued = true
        Task { @MainActor [weak self] in self?.report() }
    }

    func report() {
        reportQueued = false
        let near = onScreen.filter { abs($0 - lastShown) < Self.widest }
        guard let low = near.min(), let high = near.max() else { return }
        ask(low..<(high + 1))
    }

    private func ask(_ window: Range<Int>) {
        guard window != asked, !window.isEmpty else { return }
        asked = window
        view?.setVisible(first: UInt32(window.lowerBound), count: UInt32(window.count))
    }
}

/// Hears from the core on the core's thread and hands each batch to the main actor, in the order sent.
final class Relay: ViewObserver {
    weak let model: ListModel?

    init(_ model: ListModel) {
        self.model = model
    }

    func onBatch(batch: ViewBatch) {
        DispatchQueue.main.async { [model] in
            MainActor.assumeIsolated { model?.apply(batch) }
        }
    }

    func onRefusal(refusal: CoreRefusal) {
        // Step 0 has nothing to show for a refused window: the rows stay placeholders.
        print("list refused: \(refusal)")
    }
}
