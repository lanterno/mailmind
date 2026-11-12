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

    func testReportsTheRowsOnScreenOnceTheyHaveLanded() {
        let view = FakeView(total: 100_000)
        let model = ListModel()
        model.attach(view, firstScreen: 40)
        for index in 300..<320 { model.appeared(index) }
        model.report()
        XCTAssertEqual(view.asked.last, Window(first: 300, count: 20))
        model.report()
        XCTAssertEqual(view.asked.count, 2, "the same window is not asked for twice")
    }

    func testAJumpIgnoresRowsThatHaveNotDisappearedYet() {
        let view = FakeView(total: 100_000)
        let model = ListModel()
        model.attach(view, firstScreen: 40)
        for index in 0..<20 { model.appeared(index) }
        for index in 80_000..<80_020 { model.appeared(index) }
        model.report()
        XCTAssertEqual(view.asked.last, Window(first: 80_000, count: 20))
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

/// A batch keeping `held` and carrying fictional rows for each of `carrying`, each row's key its position.
private func batch(held: Range<Int>, carrying: [Range<Int>], total: UInt32 = 100_000) -> ViewBatch {
    ViewBatch(
        total: total,
        heldFirst: UInt32(held.lowerBound),
        heldCount: UInt32(held.count),
        ranges: carrying.map { part in
            RowRange(first: UInt32(part.lowerBound), rows: part.map { index in
                MessageRow(key: Int64(index), sender: "Ada Example", subject: "Plan", snippet: "", date: 0,
                           unread: false, flagged: false, attachment: false)
            })
        })
}
