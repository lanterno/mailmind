import XCTest

final class HeldRowsTests: XCTestCase {
    func testKeepsWhatABatchCarries() {
        var held = HeldRows()
        held.apply(batch(held: 0..<100, carrying: [0..<100]))
        XCTAssertEqual(held.range, 0..<100)
        XCTAssertEqual(held[42]?.key, 42)
        XCTAssertNil(held[100])
    }

    func testScrollingKeepsTheOverlapAndDropsTheRest() {
        var held = HeldRows()
        held.apply(batch(held: 0..<100, carrying: [0..<100]))
        held.apply(batch(held: 50..<200, carrying: [100..<200]))
        XCTAssertEqual(held.range, 50..<200)
        XCTAssertNil(held[49], "a row outside the held range is dropped")
        XCTAssertEqual(held[50]?.key, 50, "a row it had inside the range is kept, as the core did not resend it")
        XCTAssertEqual(held[199]?.key, 199)
    }

    func testChangesAreWhatWasBroughtReplacedOrDropped() {
        var held = HeldRows()
        held.apply(batch(held: 0..<100, carrying: [0..<100]))
        let before = held
        var renamed = row(10)
        renamed.subject = "Plan, again"
        held.apply(batch(held: 0..<120, carrying: [100..<120], replacing: [10: renamed]))
        var expected = IndexSet(100..<120)
        expected.insert(10)
        XCTAssertEqual(held.changes(since: before), expected)
        XCTAssertEqual(HeldRows().changes(since: held), IndexSet(0..<120), "dropping everything changes every row")
    }

    func testAJumpKeepsNothingOfBefore() {
        var held = HeldRows()
        held.apply(batch(held: 0..<100, carrying: [0..<100]))
        held.apply(batch(held: 50_000..<50_150, carrying: [50_000..<50_150]))
        XCTAssertEqual(held.range, 50_000..<50_150)
        XCTAssertNil(held[10])
        XCTAssertEqual(held[50_100]?.key, 50_100)
    }
}

@MainActor
final class ListModelTests: XCTestCase {
    func testAsksForTheFirstScreenAtOnce() {
        let view = FakeView(total: 100_000)
        let model = ListModel()
        model.attach(view, firstScreen: 40)
        XCTAssertEqual(model.total, 100_000)
        XCTAssertEqual(view.asked.last, Window(first: 0, count: 40))
    }

    func testAsksForTheRowsTheTableShowsOnlyWhenTheyMove() {
        let view = FakeView(total: 100_000)
        let model = ListModel()
        model.attach(view, firstScreen: 40)
        model.setVisible(300..<320)
        XCTAssertEqual(view.asked.last, Window(first: 300, count: 20))
        model.setVisible(300..<320)
        XCTAssertEqual(view.asked.count, 2, "the same window is not asked for twice")
        model.setVisible(80_000..<80_020)
        XCTAssertEqual(view.asked.last, Window(first: 80_000, count: 20), "a jump asks for only where it landed")
    }

    func testAWindowIsCutToTheList() {
        let view = FakeView(total: 100_000)
        let model = ListModel()
        model.attach(view, firstScreen: 40)
        model.setVisible(99_990..<100_010)
        XCTAssertEqual(view.asked.last, Window(first: 99_990, count: 10))
        model.setVisible(200_000..<200_020)
        XCTAssertEqual(view.asked.count, 2, "a window past the end asks for nothing")
    }

    func testABatchTellsTheDisplayOnlyThePositionsItChanged() {
        let view = FakeView(total: 100_000)
        let display = FakeDisplay()
        let model = ListModel()
        model.attach(view, firstScreen: 40)
        model.display = display
        model.apply(batch(held: 0..<100, carrying: [0..<100]))
        XCTAssertEqual(display.changes.last, Change(positions: IndexSet(0..<100), totalChanged: false))

        model.apply(batch(held: 50..<200, carrying: [100..<200]))
        var expected = IndexSet(0..<50)
        expected.insert(integersIn: 100..<200)
        XCTAssertEqual(display.changes.last, Change(positions: expected, totalChanged: false),
                       "rows dropped and rows brought; the kept ones are unchanged")

        model.apply(batch(held: 50..<200, carrying: []))
        XCTAssertEqual(display.changes.count, 2, "a batch that changes nothing is not passed on")

        model.apply(batch(held: 50..<200, carrying: [], total: 90_000))
        XCTAssertEqual(display.changes.last, Change(positions: IndexSet(), totalChanged: true))
    }

    func testABatchSetsTheTotalAndTheFirstRowsTime() {
        let model = ListModel()
        XCTAssertNil(model.firstRowsAt)
        model.apply(batch(held: 0..<100, carrying: [0..<100], total: 7_000))
        XCTAssertEqual(model.total, 7_000)
        XCTAssertNotNil(model.firstRowsAt)
        XCTAssertEqual(model.held[3]?.key, 3)
    }
}

@MainActor
final class OncePerFrameTests: XCTestCase {
    func testRequestsWithinAFrameRunOnce() {
        var runs = 0
        let once = OncePerFrame { runs += 1 }
        for _ in 0..<10 { once.request(framesPerSecond: 60) }
        settle(for: 0.05)
        XCTAssertEqual(runs, 1)
    }

    func testTheNextRunWaitsForTheNextFrame() {
        var ran: [UInt64] = []
        let once = OncePerFrame { ran.append(clock_gettime_nsec_np(CLOCK_UPTIME_RAW)) }
        once.request(framesPerSecond: 20)
        settle(for: 0.01)
        once.request(framesPerSecond: 20)
        settle(for: 0.15)
        XCTAssertEqual(ran.count, 2)
        XCTAssertGreaterThanOrEqual(ran[1] - ran[0], 49_000_000, "a 20 fps frame is 50 ms")
    }

    /// Let the main queue run for `seconds`.
    private func settle(for seconds: Double) {
        let passed = expectation(description: "time passed")
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { passed.fulfill() }
        wait(for: [passed], timeout: seconds + 1)
    }
}

struct Change: Equatable {
    let positions: IndexSet
    let totalChanged: Bool
}

/// A display that records what each batch changed.
@MainActor
final class FakeDisplay: ListDisplay {
    private(set) var changes: [Change] = []

    func rowsChanged(_ positions: IndexSet, totalChanged: Bool) {
        changes.append(Change(positions: positions, totalChanged: totalChanged))
    }
}

struct Window: Equatable {
    let first: UInt32
    let count: UInt32
}

/// A list view that records the windows it is asked for.
final class FakeView: ListViewProtocol, @unchecked Sendable {
    private let rows: UInt32
    private(set) var asked: [Window] = []

    init(total: UInt32) {
        rows = total
    }

    func setVisible(first: UInt32, count: UInt32) {
        asked.append(Window(first: first, count: count))
    }

    func total() -> UInt32 {
        rows
    }
}

/// A batch keeping `held` and carrying fictional rows for each of `carrying`, each row's key its position, and
/// the rows in `replacing` one at a time.
private func batch(held: Range<Int>, carrying: [Range<Int>], replacing: [Int: MessageRow] = [:],
                   total: UInt32 = 100_000) -> ViewBatch {
    ViewBatch(
        total: total,
        heldFirst: UInt32(held.lowerBound),
        heldCount: UInt32(held.count),
        ranges: carrying.map { part in RowRange(first: UInt32(part.lowerBound), rows: part.map(row)) }
            + replacing.map { index, row in RowRange(first: UInt32(index), rows: [row]) })
}

private func row(_ index: Int) -> MessageRow {
    MessageRow(key: Int64(index), sender: "Ada Example", subject: "Plan", snippet: "", date: 0,
               unread: false, flagged: false, attachment: false)
}
