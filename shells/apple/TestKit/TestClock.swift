import Foundation

/// A clock that stands still until a test moves it, for the app's clock seam (`AppSeams.clock`): the time it reads, the
/// delays it waits out and the work it runs after one (docs/test-architecture.md section 7). Nothing it holds comes due
/// by itself, so a test never waits for a delay and never misses one: `advance(by:)` moves it and runs what came due,
/// in order, and `runShortDelays()` runs the short ones, as a settle does.
///
/// It is the app's seam's shape without naming it, so the kit stays free of the app as it is of XCTest: the test target
/// and the driver each say it is an `AppClock`.
final class TestClock: @unchecked Sendable {
    /// A delay this long or shorter (a debounce, coalescing, a moment before reading again) counts as the app being
    /// busy, so settling moves through it at once. A longer one (a toast's life, Undo's window, a snooze) moves only
    /// when the test says.
    static let shortest: Duration = .seconds(2)

    /// One delay waiting to come due.
    private struct Waiting {
        let id: Int
        let due: Duration
        /// What it was set for: a delay is short by that, never by how little of it is left.
        let setFor: Duration
        /// One beat of something that repeats, which never counts as busy.
        let ticking: Bool
        let fire: Fire
    }

    private enum Fire {
        case resume(CheckedContinuation<Void, any Error>)
        case run(@MainActor @Sendable () -> Void)
    }

    /// A delay still waiting: how long until it comes due, and what it was set for.
    struct Pending: Equatable, Sendable {
        let dueIn: Duration
        let setFor: Duration
        let ticking: Bool
    }

    private let lock = NSLock()
    private let start: Date
    private let base = ContinuousClock.now
    private var elapsed: Duration = .zero
    private var waiting: [Waiting] = []
    private var cancelledEarly: Set<Int> = []
    private var made = 0

    /// A clock standing at `now`.
    init(now: Date) {
        start = now
    }

    // MARK: What the app reads

    var now: Date {
        lock.withLock { start.addingTimeInterval(Self.seconds(elapsed)) }
    }

    var instant: ContinuousClock.Instant {
        lock.withLock { base + elapsed }
    }

    /// Waits until the clock has moved `duration` on, or the task is cancelled, which throws as `Task.sleep` does. The
    /// caller waits on its own actor, so moving the clock wakes it there, in order.
    func wait(for duration: Duration, ticking: Bool, isolation: isolated (any Actor)?) async throws {
        try Task.checkCancellation()
        let id = lock.withLock { () -> Int in
            made += 1
            return made
        }
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation(isolation: isolation) {
                (continuation: CheckedContinuation<Void, any Error>) in
                let early = lock.withLock { () -> Bool in
                    if cancelledEarly.remove(id) != nil { return true }
                    waiting.append(Waiting(id: id, due: elapsed + Self.kept(duration), setFor: Self.kept(duration),
                                           ticking: ticking, fire: .resume(continuation)))
                    return false
                }
                if early { continuation.resume(throwing: CancellationError()) }
            }
        } onCancel: {
            let gone = lock.withLock { () -> Waiting? in
                guard let at = waiting.firstIndex(where: { $0.id == id }) else {
                    cancelledEarly.insert(id)
                    return nil
                }
                return waiting.remove(at: at)
            }
            if case .resume(let continuation)? = gone?.fire { continuation.resume(throwing: CancellationError()) }
        }
    }

    /// Runs `work` on the main actor once the clock has moved `delay` on.
    func after(_ delay: Duration, _ work: @escaping @MainActor @Sendable () -> Void) {
        lock.withLock {
            made += 1
            waiting.append(Waiting(id: made, due: elapsed + Self.kept(delay), setFor: Self.kept(delay), ticking: false,
                                   fire: .run(work)))
        }
    }

    // MARK: What a test does

    /// Every delay still waiting, the soonest first.
    var pending: [Pending] {
        lock.withLock {
            ordered(waiting).map { Pending(dueIn: $0.due - elapsed, setFor: $0.setFor, ticking: $0.ticking) }
        }
    }

    /// How long until each short delay still waiting comes due, the soonest first: what makes the app busy as a settle
    /// sees it. Empty when the app waits on nothing short.
    var pendingShortDelays: [Duration] {
        pending.filter { !$0.ticking && $0.setFor <= Self.shortest }.map(\.dueIn)
    }

    /// Moves the clock `duration` on, running each delay that comes due on the way in the order they come due (by when,
    /// then by when they were set), with the clock reading each one's time as it runs. A task a delay wakes runs before
    /// the next is looked at, so what it sets in turn runs too if it comes due within `duration`.
    @MainActor
    func advance(by duration: Duration) async {
        // A task started just before (a toast said again) begins its wait before the clock moves.
        await Self.letOthersRun()
        let target = lock.withLock { elapsed + Self.kept(duration) }
        while let next = takeNext(dueBy: target) {
            switch next.fire {
            case .resume(let continuation): continuation.resume()
            case .run(let work): work()
            }
            await Self.letOthersRun()
        }
        lock.withLock { elapsed = max(elapsed, target) }
        await Self.letOthersRun()
    }

    /// Runs the short delays, as a settle does: the clock moves to the next one due and runs it, again and again,
    /// until none is left or it has moved `limit` (a loop that keeps setting short delays stops there). Gives how far
    /// it moved.
    @MainActor
    @discardableResult
    func runShortDelays(limit: Duration = .seconds(30)) async -> Duration {
        var moved: Duration = .zero
        await Self.letOthersRun()
        while let next = pendingShortDelays.first, moved + next <= limit {
            await advance(by: next)
            moved += next
        }
        return moved
    }

    // MARK: Inside

    private func takeNext(dueBy target: Duration) -> Waiting? {
        lock.withLock {
            guard let next = ordered(waiting).first, next.due <= target else { return nil }
            waiting.removeAll { $0.id == next.id }
            elapsed = max(elapsed, next.due)
            return next
        }
    }

    private func ordered(_ all: [Waiting]) -> [Waiting] {
        all.sorted { ($0.due, $0.id) < ($1.due, $1.id) }
    }

    /// Lets the tasks a delay woke, and what they woke in turn, run on the main actor before the clock goes on.
    @MainActor
    private static func letOthersRun() async {
        for _ in 0..<20 { await Task.yield() }
    }

    /// A duration as the clock keeps it: to the nanosecond, as the system's timers do, so 2.6 seconds made from a
    /// `Double` is the 2.6 a test moves by; none when it is negative.
    static func kept(_ duration: Duration) -> Duration {
        let (seconds, attoseconds) = duration.components
        let nanoseconds = (Double(attoseconds) / 1e9).rounded()
        return max(.zero, .seconds(seconds) + .nanoseconds(Int64(nanoseconds)))
    }

    static func seconds(_ duration: Duration) -> TimeInterval {
        let (seconds, attoseconds) = duration.components
        return TimeInterval(seconds) + TimeInterval(attoseconds) / 1e18
    }
}
