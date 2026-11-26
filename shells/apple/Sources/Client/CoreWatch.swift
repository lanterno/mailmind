#if DEBUG
import Foundation

/// What the core's callers have in hand, for MailMind Dev's driver to settle on (docs/test-architecture.md section 7):
/// the route calls out now, and for each event stream the events it was given and those its reader has dealt with. It
/// counts only once the driver turns it `on`; a Debug build's other runs and every test leave it off, and a Release
/// build has none of it.
final class CoreWatch: @unchecked Sendable {
    static let shared = CoreWatch()

    /// One stream as its reader takes it.
    struct Stream: Sendable {
        /// The core's high-water id as the stream opened: it has heard all before it.
        var mark: String?
        /// The id of the last event the core handed it.
        var received: String?
        /// How many events the core handed it, how many its reader took, and how many it has dealt with: one is dealt
        /// with once its reader asks for the next.
        var heard = 0
        var taken = 0
        var applied = 0
    }

    private let lock = NSLock()
    private var counting = false
    private var calls: [Int: String] = [:]
    private var streams: [Int: Stream] = [:]
    private var made = 0

    /// Whether it counts: the driver turns it on as it starts, before the core opens.
    var on: Bool {
        get { lock.withLock { counting } }
        set { lock.withLock { counting = newValue } }
    }

    // MARK: Route calls

    /// A call going out: its ticket, or nil when nothing counts.
    func begin(_ what: String) -> Int? {
        lock.withLock {
            guard counting else { return nil }
            made += 1
            calls[made] = what
            return made
        }
    }

    func end(_ ticket: Int?) {
        guard let ticket else { return }
        lock.withLock { _ = calls.removeValue(forKey: ticket) }
    }

    /// The calls out now, as "POST /api/archive".
    var inFlight: [String] { lock.withLock { calls.keys.sorted().compactMap { calls[$0] } } }

    // MARK: Event streams

    /// A stream opening: its tag, or nil when nothing counts.
    func opening() -> Int? {
        lock.withLock {
            guard counting else { return nil }
            made += 1
            streams[made] = Stream()
            return made
        }
    }

    func marked(_ tag: Int, _ mark: String?) {
        lock.withLock { streams[tag]?.mark = mark }
    }

    func received(_ tag: Int, _ id: String) {
        lock.withLock {
            streams[tag]?.heard += 1
            if !id.isEmpty { streams[tag]?.received = id }
        }
    }

    func closed(_ tag: Int) {
        lock.withLock { _ = streams.removeValue(forKey: tag) }
    }

    /// Every stream open now.
    var open: [Stream] { lock.withLock { streams.keys.sorted().compactMap { streams[$0] } } }

    /// `inner` as its reader takes it: an event is dealt with once the reader asks for the one after it, as a
    /// `for await` loop does once its body has run.
    func tracked(_ inner: AsyncStream<CoreEvent>, _ tag: Int) -> AsyncStream<CoreEvent> {
        let reading = Reading(inner.makeAsyncIterator())
        return AsyncStream(unfolding: { [self] in
            lock.withLock {
                if let stream = streams[tag] { streams[tag]?.applied = stream.taken }
            }
            let next = await reading.next()
            lock.withLock {
                if next == nil { _ = streams.removeValue(forKey: tag) } else { streams[tag]?.taken += 1 }
            }
            return next
        }, onCancel: { [self] in closed(tag) })
    }

    /// The inner stream's reader, held where the outer stream's closure can reach it: only that closure reads it, one
    /// call at a time.
    private final class Reading: @unchecked Sendable {
        private var iterator: AsyncStream<CoreEvent>.Iterator

        init(_ iterator: AsyncStream<CoreEvent>.Iterator) {
            self.iterator = iterator
        }

        func next() async -> CoreEvent? {
            await iterator.next(isolation: nil)
        }
    }
}
#endif
