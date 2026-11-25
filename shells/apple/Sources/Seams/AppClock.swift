import Foundation

/// The time the app reads and the delays it waits out: the system's in a person's run, a test's own under a test, which
/// stands still until the test moves it (docs/test-architecture.md section 7). Everything in the app that reads the
/// time or waits a while goes through `AppSeams.clock`, so a test never waits for a delay and never misses one.
protocol AppClock: Sendable {
    /// The time it is.
    var now: Date { get }
    /// A reading that only moves forward, for how long something took or how long is left.
    var instant: ContinuousClock.Instant { get }
    /// Waits `duration` on the caller's actor: what `sleep(for:ticking:)`, which the app calls, does.
    func wait(for duration: Duration, ticking: Bool, isolation: isolated (any Actor)?) async throws
    /// Runs `work` on the main actor once `delay` has passed, as `DispatchQueue.main.asyncAfter` does.
    func after(_ delay: Duration, _ work: @escaping @MainActor @Sendable () -> Void)
}

extension AppClock {
    /// Waits `duration`, as `Task.sleep(for:)` does: it throws `CancellationError` when the task is cancelled.
    /// `ticking` marks one beat of something that repeats for as long as it runs (a countdown's second, the minute
    /// kept): a test's settle never counts it as the app being busy, as the page's doesn't count a `setInterval`.
    /// The wait stays on the caller's actor, so a test clock that ends it wakes the caller there, in order.
    func sleep(for duration: Duration, ticking: Bool = false,
               isolation: isolated (any Actor)? = #isolation) async throws
    {
        try await wait(for: duration, ticking: ticking, isolation: isolation)
    }
}

/// The system's clock: exactly what the app did before the seam, `Date()`, `ContinuousClock`, `Task.sleep` and the
/// main queue's `asyncAfter`.
struct SystemClock: AppClock {
    var now: Date { Date() }
    var instant: ContinuousClock.Instant { ContinuousClock.now }

    func wait(for duration: Duration, ticking: Bool, isolation: isolated (any Actor)?) async throws {
        try await Task.sleep(for: duration)
    }

    func after(_ delay: Duration, _ work: @escaping @MainActor @Sendable () -> Void) {
        DispatchQueue.main.asyncAfter(deadline: .now() + .nanoseconds(Self.nanoseconds(delay))) {
            MainActor.assumeIsolated { work() }
        }
    }

    /// A duration in whole nanoseconds, none when it is negative.
    static func nanoseconds(_ duration: Duration) -> Int {
        let (seconds, attoseconds) = duration.components
        return max(0, Int(seconds) * 1_000_000_000 + Int(attoseconds / 1_000_000_000))
    }
}
