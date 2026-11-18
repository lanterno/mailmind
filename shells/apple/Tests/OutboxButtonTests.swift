import AppKit
import SwiftUI
import XCTest

/// The top bar's outbox is there from launch whenever something waits (a send scheduled, one going, one that failed),
/// and not while nothing does. The composing model reads its tray: the button shows nothing while the tray is empty,
/// so a read hung on the button never ran, and the button never came.
@MainActor
final class OutboxButtonTests: XCTestCase {
    private static let nothingHeld = #"{"ok": true, "items": [], "waiting": 0, "stuck": 0}"#
    private static let empty = #"{"ok": true, "items": [], "drafts": []}"#

    /// The outbox holding one send to bo@example.test, in `state`.
    private static func holding(_ state: String, scheduled: Bool = false) -> String {
        #"""
        {"ok": true, "items": [
          {"id": 1, "account": "ada@example.test", "to": ["bo@example.test"], "cc": [], "bcc_count": 0,
           "subject": "The guild dinner", "state": "\#(state)", "attempts": 0, "next_attempt_at": 1900000000,
           "scheduled": \#(scheduled), "retrying": false, "unsure": false, "created_at": "2026-11-02 12:00:00"}],
         "drafts": []}
        """#
    }

    private func composing(_ outbox: String) -> (Composing, FakeRoutes) {
        let routes = FakeRoutes()
        routes.answer(API.Get.Outbox.path, outbox)
        routes.answer(API.Get.Pending.path, Self.nothingHeld)
        let composing = Composing()
        composing.use(routes, digest: Fixture.digest())
        return (composing, routes)
    }

    func testWhatWaitsInTheOutboxIsReadAsTheCoreIsReachedWithNothingOnScreen() async {
        for (state, scheduled) in [("queued", true), ("sending", false), ("error", false)] {
            let (composing, _) = composing(Self.holding(state, scheduled: scheduled))
            await until { composing.outbox.map { $0.active + $0.failed } == 1 }
            XCTAssertEqual(composing.outbox?.items.first?.state, state)
        }
    }

    func testTheButtonComesOnceSomethingIsQueuedThoughTheOutboxStartedEmpty() async {
        let (composing, routes) = composing(Self.empty)
        let host = NSHostingView(rootView: OutboxButton(composing: composing).fixedSize())
        host.frame = CGRect(x: 0, y: 0, width: 200, height: 40)
        await until { !routes.calls(to: API.Get.Pending.path).isEmpty }
        try? await Task.sleep(for: .milliseconds(50))
        host.layoutSubtreeIfNeeded()
        XCTAssertEqual(host.fittingSize.width, 0, "nothing waits: no button")

        // A send scheduled, as the event stream's "outbox" says.
        routes.answer(API.Get.Outbox.path, Self.holding("queued", scheduled: true))
        composing.outboxChanged()
        await until { composing.outbox?.active == 1 }
        host.layoutSubtreeIfNeeded()
        XCTAssertGreaterThan(host.fittingSize.width, 0, "the button shows what waits")

        // It went out: the button goes.
        routes.answer(API.Get.Outbox.path, Self.empty)
        composing.outboxChanged()
        await until { composing.outbox?.active == 0 }
        host.layoutSubtreeIfNeeded()
        XCTAssertEqual(host.fittingSize.width, 0)
    }

    /// Reads in flight together answer in any order: the tray shows the newest one's.
    func testAnOlderReadThatAnswersLateIsLetGo() async {
        let routes = FakeRoutes()
        let gate = Gate()
        routes.answer(API.Get.Pending.path, Self.nothingHeld)
        routes.answer(API.Get.Outbox.path, Self.holding("queued", scheduled: true))
        let tray = OutboxTray(routes: SlowFirst(routes: routes, gate: gate)) { _ in }
        let first = Task { await tray.load() }
        await until { gate.waiting }
        routes.answer(API.Get.Outbox.path, Self.empty)
        await tray.load()
        XCTAssertEqual(tray.active, 0)
        gate.open()
        await first.value
        XCTAssertEqual(tray.active, 0, "the first read's answer came last, and is let go")
    }
}

/// Holds the first outbox read until the test lets it go; every other read goes at once.
@MainActor
private final class Gate {
    private(set) var waiting = false
    private var held: CheckedContinuation<Void, Never>?

    func hold() async {
        waiting = true
        await withCheckedContinuation { held = $0 }
    }

    func open() {
        held?.resume()
        held = nil
    }
}

/// `routes`, its first outbox read answered as it was then but handed back only once `gate` opens.
private final class SlowFirst: Routes, @unchecked Sendable {
    private let routes: FakeRoutes
    private let gate: Gate
    private var first = true

    init(routes: FakeRoutes, gate: Gate) {
        self.routes = routes
        self.gate = gate
    }

    func get<R: GetRoute>(_ route: R.Type, _ query: KeyValuePairs<String, String?>) async throws -> R.Answer
        where R.Answer: Decodable
    {
        let answer = try await routes.get(route, query)
        if R.path == API.Get.Outbox.path, first {
            first = false
            await gate.hold()
        }
        return answer
    }

    func post<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer where R.Answer: Decodable {
        try await routes.post(route, body)
    }

    func postOverLimit<R: PostRoute>(_ route: R.Type, _ body: R.Body) async throws -> R.Answer
        where R.Answer: Decodable
    {
        try await routes.postOverLimit(route, body)
    }
}
