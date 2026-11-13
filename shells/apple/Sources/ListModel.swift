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

    /// The positions whose row is not what `before` had there: rows a batch brought, replaced or dropped.
    func changes(since before: HeldRows) -> IndexSet {
        var changed = IndexSet()
        for index in before.range where before[index] != self[index] {
            changed.insert(index)
        }
        for index in range where !before.range.contains(index) && self[index] != nil {
            changed.insert(index)
        }
        return changed
    }
}

/// What draws the list. It hears which positions a batch changed, so it redraws only those of them it shows.
@MainActor
protocol ListDisplay: AnyObject {
    func rowsChanged(_ positions: IndexSet, totalChanged: Bool)
}

/// One open list as the screen sees it: how many rows there are, and the few near the screen.
/// It decides nothing about mail: the order, the rows and what to keep all come from the core.
@MainActor
@Observable
final class ListModel {
    // The table hears of a batch through `display`, so a batch redraws nothing through SwiftUI.
    @ObservationIgnored private(set) var total = 0
    @ObservationIgnored private(set) var held = HeldRows()
    /// Set once, when the first rows reach the screen; measure mode times it.
    private(set) var firstRowsAt: Date?

    @ObservationIgnored weak var display: (any ListDisplay)?
    @ObservationIgnored private var view: (any ListViewProtocol)?
    @ObservationIgnored private var asked: Range<Int>?

    /// Start showing `view`. The first screen is asked for at once rather than after the table has laid out.
    func attach(_ view: any ListViewProtocol, firstScreen: Int = 40) {
        self.view = view
        total = Int(view.total())
        ask(0..<min(firstScreen, total))
    }

    func apply(_ batch: ViewBatch) {
        let before = held
        let totalChanged = total != Int(batch.total)
        total = Int(batch.total)
        held.apply(batch)
        if firstRowsAt == nil, !batch.ranges.isEmpty {
            firstRowsAt = Date()
        }
        let changed = held.changes(since: before)
        if totalChanged || !changed.isEmpty {
            display?.rowsChanged(changed, totalChanged: totalChanged)
        }
    }

    /// The rows the table's scroll position shows. The table calls this at most once a frame, and the core hears
    /// of a window only when it moves.
    func setVisible(_ rows: Range<Int>) {
        ask(rows.clamped(to: 0..<total))
    }

    private func ask(_ window: Range<Int>) {
        guard window != asked, !window.isEmpty else { return }
        asked = window
        view?.setVisible(first: UInt32(window.lowerBound), count: UInt32(window.count))
    }
}

/// Runs its work soon after a request, and at most once a frame however often it is asked: a table asks on every
/// scroll event, and the core needs the window once a frame. Frames are counted by the clock, not by a display link,
/// which never fires while no display is drawing: a locked or sleeping screen, or a Mac without one.
@MainActor
final class OncePerFrame {
    private let work: @MainActor () -> Void
    private var queued = false
    private var ranAt: UInt64 = 0

    init(_ work: @escaping @MainActor () -> Void) {
        self.work = work
    }

    func request(framesPerSecond: Int) {
        guard !queued else { return }
        queued = true
        let frame = 1_000_000_000 / UInt64(max(framesPerSecond, 1))
        let now = clock_gettime_nsec_np(CLOCK_UPTIME_RAW)
        let wait = ranAt + frame > now ? ranAt + frame - now : 0
        DispatchQueue.main.asyncAfter(deadline: .now() + .nanoseconds(Int(wait))) { [weak self] in
            MainActor.assumeIsolated { self?.run() }
        }
    }

    private func run() {
        queued = false
        ranAt = clock_gettime_nsec_np(CLOCK_UPTIME_RAW)
        work()
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
