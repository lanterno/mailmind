import AppKit
import SwiftUI
import XCTest

/// The recipients' chips in lines: a row is as tall as its chips wrap to, whatever width it is given and however
/// short the window, so no field's chips stand over another's.
@MainActor
final class ChipFlowTests: XCTestCase {
    /// Six chips 100 wide and 30 tall, then the field, at least 140 wide and 34 tall, measured for `proposal`.
    private func measured(_ proposal: ProposedViewSize) throws -> CGSize {
        let flow = ChipFlow(spacing: 4, fieldMin: 140) {
            ForEach(0..<6, id: \.self) { _ in Color.clear.frame(width: 100, height: 30) }
            Color.clear.frame(height: 34)
        }
        let renderer = ImageRenderer(content: flow)
        renderer.proposedSize = proposal
        return try XCTUnwrap(renderer.nsImage?.size)
    }

    /// Given no width, the chips stand on one line at their own widths; given one, they wrap at it, as they are placed.
    func testItsSizeIsTheLinesItsChipsWrapTo() throws {
        XCTAssertEqual(try measured(.unspecified), CGSize(width: 6 * 100 + 140 + 6 * 4, height: 34),
                       "no width: one line, as wide as its chips and the field")
        // 250 holds two chips a line: three lines of chips, then the field on a line of its own.
        XCTAssertEqual(try measured(ProposedViewSize(width: 250, height: nil)),
                       CGSize(width: 250, height: 3 * 30 + 3 * 4 + 34))
        XCTAssertEqual(try measured(ProposedViewSize(width: 250, height: 34)),
                       CGSize(width: 250, height: 3 * 30 + 3 * 4 + 34), "a height it is offered changes nothing")
        XCTAssertEqual(try measured(ProposedViewSize(width: 2000, height: nil)), CGSize(width: 2000, height: 34),
                       "wide: one line, the field taking the rest")
    }

    /// A reply to all with ten people, opened in a window as wide and as short as a composer gets, and roomier ones:
    /// To's, Cc's and Bcc's fields each stand in their own row, below the one before, as their chips wrap.
    func testTenRecipientsWrapInTheirOwnRows() async throws {
        let host = FakeHost()
        var seed = ComposeSeed()
        seed.mode = .replyall
        seed.to = ["dana.desk@example.test"]
        seed.cc = ["eli@example.test", "fay@example.test", "gus@example.test", "hal@example.test", "ivy@example.test",
                   "jo@example.test", "kai@example.test", "lu@example.test", "mo@example.test"]
        let composer = Composer(seed, host: host)
        defer { composer.close(force: true) }
        for (width, height) in [(640.0, 460.0), (720.0, 560.0), (900.0, 800.0)] {
            let view = NSHostingView(rootView: ComposerForm(composer: composer, host: host, aliases: [:], lights: false) { _ in }
                .meadowLight(.day))
            view.frame = CGRect(x: 0, y: 0, width: width, height: height)
            let window = NSWindow(contentRect: view.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.contentView = view
            defer { window.contentView = nil }
            var fields: [String: CGRect] = [:]
            for _ in 0..<50 where fields.count < 3 {
                view.layoutSubtreeIfNeeded()
                for field in Self.views(RecipientField.self, in: view) {
                    fields[field.accessibilityLabel() ?? ""] = field.convert(field.bounds, to: nil)
                }
                try await Task.sleep(for: .milliseconds(10))
            }
            let to = try XCTUnwrap(fields["To"]), cc = try XCTUnwrap(fields["Cc"]), bcc = try XCTUnwrap(fields["Bcc"])
            // The window's points run upwards: a row below another has the smaller y. Each field is a line of a row
            // 34 tall at least, so the next row's field stands a row's height lower, never beside or above it.
            XCTAssertGreaterThanOrEqual(to.minY - cc.maxY, 20, "Cc's field below To's at \(width)×\(height)")
            XCTAssertGreaterThanOrEqual(cc.minY - bcc.maxY, 20, "Bcc's field below Cc's at \(width)×\(height)")
        }
    }

    private static func views<V: NSView>(_ kind: V.Type, in view: NSView) -> [V] {
        ((view as? V).map { [$0] } ?? []) + view.subviews.flatMap { views(kind, in: $0) }
    }
}
