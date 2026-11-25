import AppKit
import XCTest

/// The test clock (`TestKit/TestClock.swift`): it stands still until moved, runs what comes due in order as it moves,
/// and tells a settle which delays are short (docs/test-architecture.md section 7).
@MainActor
final class TestClockTests: XCTestCase {
    private let start = Date(timeIntervalSince1970: 1_794_739_600)

    func testItStandsStillUntilMoved() async {
        let clock = TestClock(now: start)
        let instant = clock.instant
        try? await Task.sleep(for: .milliseconds(30))
        XCTAssertEqual(clock.now, start, "real time passing moves nothing")
        XCTAssertEqual(clock.instant, instant)
        await clock.advance(by: .seconds(90))
        XCTAssertEqual(clock.now, start.addingTimeInterval(90))
        XCTAssertEqual(clock.instant - instant, .seconds(90))
    }

    func testASleepEndsOnlyOnceTheClockHasMovedPastIt() async {
        let clock = TestClock(now: start)
        let woke = Box(false)
        let sleeper = Task { @MainActor in
            try? await clock.wait(for: .seconds(5), ticking: false, isolation: MainActor.shared)
            woke.value = true
        }
        await clock.advance(by: .zero)
        XCTAssertEqual(clock.pending.map(\.dueIn), [.seconds(5)])
        await clock.advance(by: .seconds(4.9))
        XCTAssertFalse(woke.value)
        await clock.advance(by: .milliseconds(100))
        XCTAssertTrue(woke.value)
        XCTAssertTrue(clock.pending.isEmpty)
        await sleeper.value
    }

    func testMovingRunsWhatCameDueInOrderEachAtItsOwnTime() async {
        let clock = TestClock(now: start)
        let ran = Box<[(String, TimeInterval)]>([])
        let at = { @Sendable [start] in clock.now.timeIntervalSince(start) }
        clock.after(.seconds(3)) { ran.value.append(("three", at())) }
        clock.after(.seconds(1)) {
            ran.value.append(("one", at()))
            // Set as the first runs, and due within the move: it runs in its turn.
            clock.after(.seconds(1)) { ran.value.append(("one and one", at())) }
        }
        clock.after(.seconds(1)) { ran.value.append(("one, set second", at())) }
        clock.after(.seconds(9)) { ran.value.append(("nine", at())) }
        Task { @MainActor in
            try? await clock.wait(for: .milliseconds(2500), ticking: false, isolation: MainActor.shared)
            ran.value.append(("slept", at()))
        }
        await clock.advance(by: .zero)

        await clock.advance(by: .seconds(5))
        XCTAssertEqual(ran.value.map(\.0), ["one", "one, set second", "one and one", "slept", "three"])
        XCTAssertEqual(ran.value.map(\.1), [1, 1, 2, 2.5, 3])
        XCTAssertEqual(clock.now, start.addingTimeInterval(5), "it stands where it was moved to")
        XCTAssertEqual(clock.pending.map(\.dueIn), [.seconds(4)], "nine is still to come")
    }

    func testOnlyAShortDelayCountsAsBusy() async {
        let clock = TestClock(now: start)
        clock.after(.milliseconds(400)) {}
        clock.after(.seconds(2)) {}
        clock.after(.milliseconds(2600)) {}   // a toast's life
        let beat = Task { @MainActor in
            try? await clock.wait(for: .seconds(1), ticking: true, isolation: MainActor.shared)
        }
        await clock.advance(by: .zero)
        XCTAssertEqual(clock.pendingShortDelays, [.milliseconds(400), .seconds(2)], "a beat of a countdown isn't busy")
        XCTAssertEqual(clock.pending.count, 4)

        await clock.advance(by: .milliseconds(2500))
        XCTAssertEqual(clock.pending.map(\.dueIn), [.milliseconds(100)])
        XCTAssertEqual(clock.pendingShortDelays, [], "short by what it was set for, not by how little is left")
        await beat.value
    }

    func testRunningTheShortDelaysLeavesTheLongOnesAndStopsALoopThatNeverRests() async {
        let clock = TestClock(now: start)
        let debounced = Box(0)
        let toastGone = Box(false)
        clock.after(.milliseconds(400)) { debounced.value += 1 }
        clock.after(.milliseconds(2600)) { toastGone.value = true }
        let moved = await clock.runShortDelays()
        XCTAssertEqual(moved, .milliseconds(400))
        XCTAssertEqual(debounced.value, 1)
        XCTAssertFalse(toastGone.value, "a long delay moves only when the test says")

        let polling = Task { @MainActor in
            while !Task.isCancelled {
                try? await clock.wait(for: .milliseconds(500), ticking: false, isolation: MainActor.shared)
            }
        }
        await clock.advance(by: .zero)
        let looped = await clock.runShortDelays(limit: .seconds(3))
        XCTAssertEqual(looped, .seconds(3), "a settle gives up on a loop at its limit")
        polling.cancel()
        await polling.value
    }

    func testACancelledSleepThrowsAndLeavesNothingWaiting() async {
        let clock = TestClock(now: start)
        let sleeper = Task { @MainActor () -> Bool in
            do {
                try await clock.wait(for: .seconds(60), ticking: false, isolation: MainActor.shared)
                return false
            } catch {
                return error is CancellationError
            }
        }
        await clock.advance(by: .zero)
        XCTAssertEqual(clock.pending.count, 1)
        sleeper.cancel()
        let cancelled = await sleeper.value
        XCTAssertTrue(cancelled)
        XCTAssertTrue(clock.pending.isEmpty)

        let already = Task { @MainActor () -> Bool in
            withUnsafeCurrentTask { $0?.cancel() }
            return (try? await clock.wait(for: .seconds(1), ticking: false, isolation: MainActor.shared)) == nil
        }
        let threw = await already.value
        XCTAssertTrue(threw, "a task cancelled before it sleeps doesn't sleep")
        XCTAssertTrue(clock.pending.isEmpty)
    }

    /// The seam: the app reads the clock put in it, and waits on it.
    func testTheAppReadsAndWaitsOnTheClockInItsSeam() async {
        let seams = TestSeams()
        defer { seams.restore() }
        XCTAssertEqual(AppSeams.clock.now, TestSeams.start)
        let woke = Box(false)
        Task { @MainActor in
            try? await AppSeams.clock.sleep(for: .seconds(1))
            woke.value = true
        }
        await seams.clock.advance(by: .zero)
        XCTAssertEqual(seams.clock.pendingShortDelays, [.seconds(1)])
        await seams.clock.runShortDelays()
        XCTAssertTrue(woke.value)
        XCTAssertEqual(AppSeams.clock.now, TestSeams.start.addingTimeInterval(1))
    }
}

/// A value the main actor holds, which a delay's work and the test both reach.
@MainActor
final class Box<Value> {
    var value: Value

    init(_ value: Value) {
        self.value = value
    }
}
