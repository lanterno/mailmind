#if DEBUG && os(macOS)
import Foundation

/// Settled, as docs/test-architecture.md section 7 has it on the Mac: all of these hold at once, on three probes in a
/// row, within a ceiling that fails saying what was still busy.
///
/// 1. Every event stream of the app's callers (the window, the desk, the composers, the status) has been handed every
///    event up to the core's high-water id, and its reader has dealt with each (`CoreWatch`).
/// 2. No route call from the app is out (`CoreWatch.inFlight`: every call goes through `Core.send`).
/// 3. No short delay is pending on the app's clock: each is run at once (`TestClock.runShortDelays`), while no call is
///    out, as the page's settle runs its own.
/// 4. Then the core's `test_settle` (its requests answered and the work they set going) and a pass of each job the app
///    kicked (`test_pump_kicked`); settling goes on while that publishes events.
@MainActor
enum DriverSettle {
    static let probes = 3

    static func settle(ceiling: Double) async throws -> [String: Any] {
        guard let core = MailApp.shared.core else {
            // The core opens as the window first appears: until then there is nothing to settle but the window.
            try await Task.sleep(for: .milliseconds(50))
            return ["ms": 0, "note": "the core isn't open"]
        }
        let host = core.testHost
        let started = ContinuousClock.now
        let deadline = started + .milliseconds(Int64(ceiling * 1000))
        var quiet = 0
        var ranShort: Duration = .zero
        while true {
            var busy: [String] = []
            let calls = CoreWatch.shared.inFlight
            if !calls.isEmpty { busy.append("route calls out: \(calls.joined(separator: ", "))") }
            let short = DevDriver.clock.pendingShortDelays
            if !short.isEmpty, calls.isEmpty {
                if ranShort > .seconds(30) {
                    throw DriverCommands.Failed("the app keeps setting short delays: ran \(ranShort) of them, still \(short.prefix(5))")
                }
                ranShort += await DevDriver.clock.runShortDelays()
                quiet = 0
                continue
            }
            if busy.isEmpty {
                let high = try await DriverCommands.offMain { try host.testHighWater() }
                if let behind = behind(high) { busy.append(behind) }
            }
            if busy.isEmpty {
                quiet += 1
                if quiet >= probes {
                    let before = try await DriverCommands.offMain { try host.testHighWater() }
                    let left = try await DriverCommands.offMain { try host.testSettle(timeoutSeconds: 10) }
                    if left > 0 { busy.append("the core still answers \(left) request(s), or their work") }
                    try await DriverCommands.offMain { try host.testPumpKicked() }
                    let after = try await DriverCommands.offMain { try host.testHighWater() }
                    if busy.isEmpty, after == before {
                        let ms = Double((ContinuousClock.now - started).components.attoseconds) / 1e15
                            + Double((ContinuousClock.now - started).components.seconds) * 1000
                        let moved = Double(ranShort.components.seconds) * 1000
                            + Double(ranShort.components.attoseconds) / 1e15
                        return ["ms": ms, "high_water": after, "clock_moved_ms": moved]
                    }
                    quiet = 0
                }
            } else {
                quiet = 0
            }
            if ContinuousClock.now > deadline {
                throw DriverCommands.Failed("not settled after \(Int(ceiling))s: " + busy.joined(separator: "; "))
            }
            try await Task.sleep(for: .milliseconds(busy.isEmpty ? 15 : 10))
        }
    }

    /// Why a stream hasn't dealt with the core's last event, or nil when every one has.
    static func behind(_ high: String) -> String? {
        let (run, n) = split(high)
        for (index, stream) in CoreWatch.shared.open.enumerated() {
            if stream.taken < stream.heard {
                return "stream \(index + 1) was handed \(stream.heard - stream.taken) event(s) its reader hasn't taken"
            }
            if stream.applied < stream.taken {
                return "stream \(index + 1)'s reader is still dealing with an event"
            }
            let heard = [stream.received, stream.mark].compactMap { $0 }.map(split).filter { $0.0 == run }
            guard let last = heard.map(\.1).max() else {
                return "stream \(index + 1) has heard nothing of this run of the core (it is at \(high))"
            }
            if last < n { return "stream \(index + 1) has heard events up to \(run)-\(last); the core is at \(high)" }
        }
        return nil
    }

    private static func split(_ id: String) -> (String, Int) {
        guard let dash = id.lastIndex(of: "-") else { return (id, 0) }
        return (String(id[..<dash]), Int(id[id.index(after: dash)...]) ?? 0)
    }
}
#endif
