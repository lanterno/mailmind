import AppKit
import SwiftUI
import XCTest

/// A composer with many recipients in a short window: the recipient rows stop growing and scroll inside, so the head,
/// Send and the text all stay in the window, and the field being typed in stays in sight.
@MainActor
final class ComposeLayoutTests: XCTestCase {
    /// Reply All to a message to thirty people besides you.
    private func replyAll(_ host: FakeHost) -> Composer {
        let many = (1...30).map { "Guild member \($0) <member\($0)@example.test>" }.joined(separator: ", ")
        return Composer(Seeds.reply(opened(to: "ada@example.test, " + many), all: true, mine: host.mine), host: host)
    }

    private static func views<V: NSView>(_ kind: V.Type, in view: NSView) -> [V] {
        ((view as? V).map { [$0] } ?? []) + view.subviews.flatMap { views(kind, in: $0) }
    }

    /// With thirty-one people, at the window's least and at a short window: the head stands above To at the top, the
    /// foot with Send has its room below the text, the text has a line or more inside it, and Cc's typing field is in
    /// sight, above the text.
    func testManyRecipientsLeaveTheHeadSendAndTheTextInTheWindow() async throws {
        for size in [CGSize(width: 640, height: 492), CGSize(width: 720, height: 640)] {
            try await check(size)
        }
    }

    private func check(_ size: CGSize) async throws {
        let host = FakeHost()
        let composer = replyAll(host)
        defer { composer.close(force: true) }
        XCTAssertEqual(composer.recipients(.cc).count, 30)
        let view = NSHostingView(rootView: ComposerForm(composer: composer, host: host, aliases: [:], lights: false) { _ in }
            .meadowLight(.day)
            .frame(width: size.width, height: size.height))
        view.frame = CGRect(origin: .zero, size: size)
        let window = NSWindow(contentRect: view.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = view
        defer { window.contentView = nil }
        var text: BodyTextView?, fields: [RecipientField] = []
        for _ in 0..<50 where text == nil || fields.count < 3 {
            view.layoutSubtreeIfNeeded()
            text = Self.views(BodyTextView.self, in: view).first
            fields = Self.views(RecipientField.self, in: view)
            try await Task.sleep(for: .milliseconds(10))
        }
        try await Task.sleep(for: .milliseconds(100))
        view.layoutSubtreeIfNeeded()
        let at = "at \(Int(size.width))×\(Int(size.height))"

        let body = try XCTUnwrap(text?.enclosingScrollView, "the text")
        let shown = body.convert(body.bounds, to: nil)
        XCTAssertGreaterThanOrEqual(shown.height, 48, "a line of text at least, with its margins, \(at)")
        // The foot, with Send, is 58 points tall below the text and the quote's button.
        XCTAssertGreaterThanOrEqual(shown.minY, 58, "the foot has its room below the text, \(at)")
        XCTAssertLessThanOrEqual(shown.maxY, size.height)

        XCTAssertEqual(fields.count, 3, "To, Cc and Bcc")
        // The head is 62 points tall and From's row 46, above To's field.
        let to = fields[0].convert(fields[0].bounds, to: nil)
        XCTAssertLessThanOrEqual(to.maxY, size.height - 62 - 46, "the head and From stand above To, in the window, \(at)")
        let cc = fields[1]
        let scroll = try XCTUnwrap(cc.enclosingScrollView, "Cc scrolls inside")
        let inScroll = cc.convert(cc.bounds, to: scroll)
        XCTAssertTrue(inScroll.minY >= -1 && inScroll.maxY <= scroll.bounds.maxY + 1,
                      "Cc's typing field is in sight, \(at): \(inScroll) in \(scroll.bounds)")
        XCTAssertGreaterThan(cc.convert(cc.bounds, to: nil).minY, shown.maxY, "and above the text, never under it, \(at)")
    }
}
