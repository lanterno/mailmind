// What crossing into the core costs from Swift: a call that does nothing, and a batch of 50 rows.
// measure.sh compiles it with -O against the Mac slice of the core and the generated bindings.

import Foundation

@main
enum Probe {
    /// Nanoseconds per call for each of `runs` timed runs, after a warm-up, sorted.
    static func timed(_ iterations: Int, runs: Int = 7, _ body: () -> Void) -> [Double] {
        for _ in 0..<(iterations / 10) { body() }
        var out: [Double] = []
        for _ in 0..<runs {
            let start = DispatchTime.now().uptimeNanoseconds
            for _ in 0..<iterations { body() }
            let end = DispatchTime.now().uptimeNanoseconds
            out.append(Double(end - start) / Double(iterations))
        }
        return out.sorted()
    }

    static func main() {
        // Kept so the optimizer cannot drop calls whose results nothing reads.
        var sink = 0
        let noop = timed(100_000) { probeNoop() }
        let rows = timed(10_000) {
            let batch = probeRows(count: 50)
            sink &+= batch.count &+ batch[49].snippet.utf8.count
        }
        func fmt(_ v: Double) -> String { String(format: "%.3f", v) }
        print("probe_noop x100000: median \(fmt(noop[noop.count / 2])) ns/call, min \(fmt(noop[0])) ns/call")
        print("probe_rows(50) x10000: median \(fmt(rows[rows.count / 2] / 1000)) us/batch, min \(fmt(rows[0] / 1000)) us/batch")
        if sink == 42 { print("") }
    }
}
