import Foundation
#if os(macOS)
import AppKit
#else
import UIKit
#endif

/// Measure mode, for `just apple measure`. With MAILMIND_MEASURE=start the app prints how long it took from process
/// start to the first rows and quits. With MAILMIND_MEASURE=all it then waits idle, scrolls fast for ten seconds,
/// scrolls through the whole list, jumps around it, prints its memory and the main thread's time per run-loop pass,
/// and quits. MAILMIND_MEASURE=scroll is `just apple smoke`'s check: it jumps nine tenths of the way down and quits
/// once the rows on screen there show mail, or after a minute without. Without it, nothing here runs.
/// It moves the table's scroll view as a trackpad or a finger does, so the table hears of it as it would of a person.
@MainActor
final class Measure {
    static let shared = Measure()
    private let mode = ProcessInfo.processInfo.environment["MAILMIND_MEASURE"]
    private var started = false
    /// Held while measuring. A window nobody can see, as behind a locked screen, lets macOS nap the app, which
    /// about halves its speed; a person scrolling has it in front, where it is never napped.
    private var awake: (any NSObjectProtocol)?

    func firstRowsShown(_ model: ListModel) async {
        guard let mode, !started, let shown = model.firstRowsAt else { return }
        started = true
        awake = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .latencyCritical], reason: "measuring")
        emit("first_rows_ms", shown.timeIntervalSince(Self.processStart()) * 1000)
        if mode == "start" { exit(0) }
        if mode == "scroll" { await scrollFar(model) }

        try? await Task.sleep(for: .seconds(3))
        emit("idle_footprint_mb", Self.footprintMB())
        await pause("idle")

        // A fast flick's speed, held for ten seconds: the frames a person sees while scrolling.
        emit("list_height_points", Double(contentHeight))
        let fling = MainThreadBusy()
        fling.start()
        // Ten seconds of ticks, or twenty of wall time when the main thread cannot keep up.
        let deadline = ContinuousClock.now + .seconds(20)
        var ticks = 0
        while ticks < 600, ContinuousClock.now < deadline {
            scroll(by: 200)
            ticks += 1
            try? await Task.sleep(for: .milliseconds(16))
        }
        fling.stop()
        emit("scroll_ticks", Double(ticks))
        fling.report("scroll")
        emit("after_fast_scroll_footprint_mb", Self.footprintMB())

        // Then the whole list, fifty rows a step, so every page has been on screen once.
        let sweep = MainThreadBusy()
        sweep.start()
        var row = 0
        while row < model.total {
            row += 50
            scroll(toRow: row, rows: model.total)
            try? await Task.sleep(for: .milliseconds(16))
        }
        sweep.stop()
        sweep.report("sweep")
        try? await Task.sleep(for: .seconds(1))
        emit("after_scroll_footprint_mb", Self.footprintMB())

        // Jumps far up and down, as a jump to a date would make them: the row straight to the top.
        let jumps = MainThreadBusy()
        jumps.start()
        for target in [0, 99_950, 50_000, 12_345, 87_654, 25_000, 75_000, 1_000, 99_000, 60_000, 33_333, 0] {
            scroll(toRow: min(target, model.total - 1), rows: model.total)
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

    /// The smoke check: one jump far down, as dragging the scroll bar makes, then wait for the rows on screen to show
    /// what the core sent for them. Its exit code is the result: 0 once they do, 3 after a minute without.
    private func scrollFar(_ model: ListModel) async -> Never {
        let target = model.total * 9 / 10
        let started = ContinuousClock.now
        scroll(toRow: target, rows: model.total)
        while ContinuousClock.now - started < .seconds(60) {
            try? await Task.sleep(for: .milliseconds(50))
            let shown = shownRows()
            if let first = shown.map(\.position).min(), first >= model.total * 85 / 100, shown.allSatisfy(\.real) {
                emit("scrolled_ms", Double((ContinuousClock.now - started) / .milliseconds(1)))
                emit("shown_rows", Double(shown.count))
                exit(0)
            }
        }
        let shown = shownRows()
        print("stuck: \(shown.filter(\.real).count) of \(shown.count) rows on screen show mail,"
              + " the first at \(shown.map(\.position).min() ?? -1) of \(model.total)")
        fflush(stdout)
        exit(3)
    }

    #if os(macOS)
    /// The list's scroll view. Moving its clip view is what a trackpad does, so scrolling is measured the way a
    /// person scrolls.
    private lazy var scrollView: NSScrollView? = NSApp.windows.compactMap { window in window.contentView.flatMap { Self.tallest(in: $0) } }.first

    private static func tallest(in view: NSView) -> NSScrollView? {
        if let scroll = view as? NSScrollView, (scroll.documentView?.frame.height ?? 0) > 100_000 { return scroll }
        for child in view.subviews { if let found = tallest(in: child) { return found } }
        return nil
    }

    private var contentHeight: CGFloat { scrollView?.documentView?.frame.height ?? 0 }

    private func scroll(by points: CGFloat) {
        guard let scrollView else { return }
        let clip = scrollView.contentView
        move(clip.bounds.origin.y + points)
    }

    /// The row at the top, where the table itself says the row is.
    private func scroll(toRow row: Int, rows: Int) {
        guard let scrollView, let document = scrollView.documentView else { return }
        let y = (document as? NSTableView)?.rect(ofRow: min(row, rows - 1)).minY
            ?? document.frame.height * CGFloat(row) / CGFloat(max(rows, 1))
        move(y)
    }

    /// Each row on screen, and whether its cell shows mail rather than a placeholder.
    private func shownRows() -> [(position: Int, real: Bool)] {
        guard let table = scrollView?.documentView as? NSTableView else { return [] }
        let shown = table.rows(in: table.visibleRect)
        return (shown.location..<(shown.location + shown.length)).map { row in
            (row, (table.view(atColumn: 0, row: row, makeIfNecessary: false) as? MessageCell)?.row != nil)
        }
    }

    private func move(_ y: CGFloat) {
        guard let scrollView, let document = scrollView.documentView else { return }
        let clip = scrollView.contentView
        let bottom = max(0, document.frame.height - clip.bounds.height)
        clip.scroll(to: NSPoint(x: 0, y: min(bottom, max(0, y))))
        scrollView.reflectScrolledClipView(clip)
    }
    #else
    /// The list's scroll view. Setting its offset is what a finger's drag does.
    private lazy var scrollView: UIScrollView? = UIApplication.shared.connectedScenes
        .compactMap { $0 as? UIWindowScene }.flatMap(\.windows).compactMap { Self.tallest(in: $0) }.first

    private static func tallest(in view: UIView) -> UIScrollView? {
        if let scroll = view as? UIScrollView, scroll.contentSize.height > 100_000 { return scroll }
        for child in view.subviews { if let found = tallest(in: child) { return found } }
        return nil
    }

    private var contentHeight: CGFloat { scrollView?.contentSize.height ?? 0 }

    private func scroll(by points: CGFloat) {
        guard let scrollView else { return }
        move(scrollView.contentOffset.y + points)
    }

    /// The row at the top, where the collection view itself says the row is.
    private func scroll(toRow row: Int, rows: Int) {
        guard let scrollView else { return }
        let item = IndexPath(item: min(row, rows - 1), section: 0)
        let y = (scrollView as? UICollectionView)?.layoutAttributesForItem(at: item)?.frame.minY
            ?? scrollView.contentSize.height * CGFloat(row) / CGFloat(max(rows, 1))
        move(y)
    }

    /// Each row on screen, and whether its cell shows mail rather than a placeholder.
    private func shownRows() -> [(position: Int, real: Bool)] {
        guard let list = scrollView as? UICollectionView else { return [] }
        return list.indexPathsForVisibleItems.map { path in
            (path.item, (list.cellForItem(at: path) as? MessageCell)?.row != nil)
        }
    }

    private func move(_ y: CGFloat) {
        guard let scrollView else { return }
        let top = -scrollView.adjustedContentInset.top
        let bottom = max(top, scrollView.contentSize.height + scrollView.adjustedContentInset.bottom - scrollView.bounds.height)
        scrollView.contentOffset.y = min(bottom, max(top, y))
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
