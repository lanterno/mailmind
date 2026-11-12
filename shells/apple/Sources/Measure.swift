import Foundation
import SwiftUI
#if os(macOS)
import AppKit
#endif

/// Measure mode, for `just apple measure`. With MAILMIND_MEASURE=start the app prints how long it took from process
/// start to the first rows and quits. With MAILMIND_MEASURE=all it then waits idle, scrolls fast for ten seconds,
/// scrolls through the whole list, jumps around it, prints its memory and the main thread's time per run-loop pass,
/// and quits. MAILMIND_MEASURE=scroll stops after the fast scroll. Without it, nothing here runs.
@MainActor
final class Measure {
    static let shared = Measure()
    private let mode = ProcessInfo.processInfo.environment["MAILMIND_MEASURE"]
    private var started = false

    func firstRowsShown(_ model: ListModel, _ scroller: ScrollViewProxy) async {
        guard let mode, !started, let shown = model.firstRowsAt else { return }
        started = true
        emit("first_rows_ms", shown.timeIntervalSince(Self.processStart()) * 1000)
        if mode == "start" { exit(0) }

        try? await Task.sleep(for: .seconds(3))
        emit("idle_footprint_mb", Self.footprintMB())
        await pause("idle")

        // A fast flick's speed, held for ten seconds: the frames a person sees while scrolling.
        #if os(macOS)
        emit("list_height_points", Double(scrollView?.documentView?.frame.height ?? 0))
        #endif
        let fling = MainThreadBusy()
        fling.start()
        // Ten seconds of ticks, or twenty of wall time when the main thread cannot keep up.
        let deadline = ContinuousClock.now + .seconds(20)
        var ticks = 0
        while ticks < 600, ContinuousClock.now < deadline {
            scroll(by: 200, rows: model.total, scroller)
            ticks += 1
            try? await Task.sleep(for: .milliseconds(16))
        }
        fling.stop()
        emit("scroll_ticks", Double(ticks))
        fling.report("scroll")
        emit("after_fast_scroll_footprint_mb", Self.footprintMB())
        if mode == "scroll" { exit(0) }

        // Then the whole list, fifty rows a step, so every page has been on screen once.
        let sweep = MainThreadBusy()
        sweep.start()
        var row = 0
        while row < model.total {
            row += 50
            scroll(toRow: row, rows: model.total, scroller)
            try? await Task.sleep(for: .milliseconds(16))
        }
        sweep.stop()
        sweep.report("sweep")
        try? await Task.sleep(for: .seconds(1))
        emit("after_scroll_footprint_mb", Self.footprintMB())

        // Jumps through SwiftUI's own scrollTo, as a jump to a date would make them.
        let jumps = MainThreadBusy()
        jumps.start()
        for target in [0, 99_950, 50_000, 12_345, 87_654, 25_000, 75_000, 1_000, 99_000, 60_000, 33_333, 0] {
            scroller.scrollTo(min(target, model.total - 1), anchor: .top)
            try? await Task.sleep(for: .milliseconds(300))
        }
        jumps.stop()
        jumps.report("jumps")
        try? await Task.sleep(for: .seconds(1))
        emit("after_jumps_footprint_mb", Self.footprintMB())
        emit("held_rows", Double(model.held.range.count))
        await pause("done")
        exit(0)
    }

    #if os(macOS)
    /// The list's scroll view. Moving its clip view is what a trackpad does, so scrolling is measured the way a
    /// person scrolls, not through scrollTo, which looks the row up first.
    private lazy var scrollView: NSScrollView? = NSApp.windows.compactMap { window in window.contentView.flatMap { Self.tallest(in: $0) } }.first

    private static func tallest(in view: NSView) -> NSScrollView? {
        if let scroll = view as? NSScrollView, (scroll.documentView?.frame.height ?? 0) > 100_000 { return scroll }
        for child in view.subviews { if let found = tallest(in: child) { return found } }
        return nil
    }

    private func scroll(by points: CGFloat, rows: Int, _ scroller: ScrollViewProxy) {
        guard let scrollView, let document = scrollView.documentView else { return }
        let clip = scrollView.contentView
        let bottom = max(0, document.frame.height - clip.bounds.height)
        clip.scroll(to: NSPoint(x: 0, y: min(bottom, clip.bounds.origin.y + points)))
        scrollView.reflectScrolledClipView(clip)
    }

    private func scroll(toRow row: Int, rows: Int, _ scroller: ScrollViewProxy) {
        guard let scrollView, let document = scrollView.documentView else { return }
        let clip = scrollView.contentView
        let y = document.frame.height * CGFloat(row) / CGFloat(max(rows, 1))
        clip.scroll(to: NSPoint(x: 0, y: min(max(0, document.frame.height - clip.bounds.height), y)))
        scrollView.reflectScrolledClipView(clip)
    }
    #else
    private func scroll(by points: CGFloat, rows: Int, _ scroller: ScrollViewProxy) {}

    private func scroll(toRow row: Int, rows: Int, _ scroller: ScrollViewProxy) {
        scroller.scrollTo(min(row, rows - 1), anchor: .top)
    }
    #endif

    /// Say where the run is and wait, so measure.sh can sample the process from outside.
    private func pause(_ phase: String) async {
        print("phase=\(phase) pid=\(getpid())")
        fflush(stdout)
        try? await Task.sleep(for: .seconds(4))
    }

    private func emit(_ name: String, _ value: Double) {
        print("\(name)=\(String(format: "%.1f", value))")
        fflush(stdout)
    }

    /// When the kernel started this process: the clock a cold start is timed from.
    static func processStart() -> Date {
        var info = kinfo_proc()
        var size = MemoryLayout<kinfo_proc>.stride
        var name: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()]
        guard sysctl(&name, 4, &info, &size, nil, 0) == 0 else { return .now }
        let start = info.kp_proc.p_un.__p_starttime
        return Date(timeIntervalSince1970: TimeInterval(start.tv_sec) + TimeInterval(start.tv_usec) / 1_000_000)
    }

    /// The process's footprint as the system counts it against its memory limit (phys_footprint).
    static func footprintMB() -> Double {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        return result == KERN_SUCCESS ? Double(info.phys_footprint) / 1_048_576 : -1
    }
}

/// The main thread's busy time in each pass of its run loop: from waking to going back to sleep. While the list
/// scrolls, a pass is a frame's worth of work, Core Animation's commit included.
@MainActor
final class MainThreadBusy {
    private var observers: [CFRunLoopObserver] = []
    private var wokeAt: UInt64 = 0
    private var samples: [Double] = []

    func start() {
        let woke = CFRunLoopObserverCreateWithHandler(nil, CFRunLoopActivity.afterWaiting.rawValue, true, .min) { _, _ in
            MainActor.assumeIsolated { self.wokeAt = clock_gettime_nsec_np(CLOCK_UPTIME_RAW) }
        }
        let sleeps = CFRunLoopObserverCreateWithHandler(nil, CFRunLoopActivity.beforeWaiting.rawValue, true, .max) { _, _ in
            MainActor.assumeIsolated {
                guard self.wokeAt != 0 else { return }
                self.samples.append(Double(clock_gettime_nsec_np(CLOCK_UPTIME_RAW) - self.wokeAt) / 1_000_000)
                self.wokeAt = 0
            }
        }
        for observer in [woke, sleeps].compactMap({ $0 }) {
            CFRunLoopAddObserver(CFRunLoopGetMain(), observer, .commonModes)
            observers.append(observer)
        }
    }

    func stop() {
        for observer in observers { CFRunLoopRemoveObserver(CFRunLoopGetMain(), observer, .commonModes) }
        observers.removeAll()
    }

    func report(_ phase: String) {
        let sorted = samples.sorted()
        guard !sorted.isEmpty else { return }
        func at(_ q: Double) -> Double { sorted[min(sorted.count - 1, Int(Double(sorted.count) * q))] }
        let over = sorted.filter { $0 > 8 }.count
        print(String(format: "main_thread_ms_\(phase) passes=%d p50=%.2f p95=%.2f p99=%.2f max=%.2f over_8ms=%d",
                     sorted.count, at(0.5), at(0.95), at(0.99), sorted.last ?? 0, over))
        fflush(stdout)
    }
}
