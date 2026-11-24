import AppKit
import SwiftUI
import XCTest

/// Keys typed into the composer's text in a real window: with a signature set, what is typed goes above it, a blank
/// line between, as Mail has it, for a new message and a reply; what the send builds holds the words; and From still
/// swaps the signature once words are typed.
@MainActor
final class ComposeTypingTests: XCTestCase {
    private static let ada = "ada@example.test"
    private static let work = "ada@work.example.test"
    private var host: FakeHost!
    private var made: [Composer] = []
    private var windows: [NSWindow] = []

    override func setUp() async throws {
        _ = NSApplication.shared
        host = FakeHost()
        host.signatures = [Self.ada: "Ada at the Guild"]
    }

    override func tearDown() async throws {
        for composer in made { composer.close(force: true) }
        made = []
        for window in windows {
            window.orderOut(nil)
            window.contentView = nil
        }
        windows = []
    }

    private func composer(_ seed: ComposeSeed = ComposeSeed()) -> Composer {
        let composer = Composer(seed, host: host)
        made.append(composer)
        return composer
    }

    /// The composer's window as the app draws it, and the text view in it.
    private func hosted(_ composer: Composer) async throws -> (NSWindow, BodyTextView) {
        let view = NSHostingView(rootView: ComposerForm(composer: composer, host: host, aliases: [:], lights: false) { _ in }
            .frame(width: 720, height: 560)
            .meadowLight(.day))
        view.frame = CGRect(x: 0, y: 0, width: 720, height: 560)
        let window = NSWindow(contentRect: view.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = view
        windows.append(window)
        var found: BodyTextView?
        for _ in 0..<50 where found == nil {
            view.layoutSubtreeIfNeeded()
            found = Self.views(BodyTextView.self, in: view).first
            try await Task.sleep(for: .milliseconds(10))
        }
        return (window, try XCTUnwrap(found, "the message's text"))
    }

    /// Let SwiftUI draw what changed, as it would between two keys.
    private func settle(_ window: NSWindow) async {
        window.contentView?.layoutSubtreeIfNeeded()
        try? await Task.sleep(for: .milliseconds(10))
    }

    /// Each character as its own key press, sent through the window to whatever has the keyboard, SwiftUI given a
    /// turn between some of them.
    private func type(_ text: String, in window: NSWindow) async throws {
        for (index, character) in text.enumerated() {
            let key = String(character)
            window.sendEvent(try XCTUnwrap(NSEvent.keyEvent(
                with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: window.windowNumber,
                context: nil, characters: key, charactersIgnoringModifiers: key, isARepeat: false, keyCode: 0)))
            if index % 3 == 0 { await settle(window) }
        }
        await settle(window)
    }

    private static func views<V: NSView>(_ kind: V.Type, in view: NSView) -> [V] {
        ((view as? V).map { [$0] } ?? []) + view.subviews.flatMap { views(kind, in: $0) }
    }

    /// ⌘N, Tab to the text, and type: the words go above the signature, and that is what is sent.
    func testWordsTypedInANewMessageGoAboveTheSignature() async throws {
        let fresh = composer()
        let (window, text) = try await hosted(fresh)
        XCTAssertEqual(text.string, "\n\n-- \nAda at the Guild")
        XCTAssertTrue(window.makeFirstResponder(text), "Tab brings the keyboard to the text")
        XCTAssertEqual(text.selectedRange(), NSRange(location: 0, length: 0), "the caret is at the top")

        try await type("Got it, thanks.", in: window)
        XCTAssertEqual(text.string, "Got it, thanks.\n\n-- \nAda at the Guild")
        fresh.commitChip(.to, "bo@example.test")
        fresh.setSubject("Plans")
        fresh.pressSend()
        XCTAssertEqual(host.sent.first?.body.text, "Got it, thanks.\n\n-- \nAda at the Guild",
                       "the words are the message, the signature below them")
    }

    /// r on a message, and type: the reply's words go above the signature too.
    func testWordsTypedInAReplyGoAboveTheSignature() async throws {
        let reply = composer(Seeds.reply(opened(), all: false, mine: host.mine))
        let (window, text) = try await hosted(reply)
        for _ in 0..<50 where window.firstResponder !== text { await settle(window) }
        XCTAssertTrue(window.firstResponder === text, "a reply opens with the keyboard in its text")
        XCTAssertEqual(text.selectedRange(), NSRange(location: 0, length: 0), "the caret is at the top")

        try await type("Got it, thanks.", in: window)
        reply.pressSend()
        XCTAssertEqual(host.sent.first?.body.text, "Got it, thanks.\n\n-- \nAda at the Guild")
    }

    /// Words typed, then another account chosen in From: its signature takes the place of the first, under the words.
    func testChangingFromAfterTypingSwapsTheSignature() async throws {
        host.senders = [Self.ada, Self.work]
        host.signatures[Self.work] = "Ada Lovelace, Work"
        let fresh = composer()
        let (window, text) = try await hosted(fresh)
        XCTAssertTrue(window.makeFirstResponder(text))
        try await type("Hi", in: window)

        fresh.setFrom(Self.work)
        await settle(window)
        XCTAssertEqual(fresh.body, "Hi\n\n-- \nAda Lovelace, Work")
        XCTAssertEqual(text.string, "Hi\n\n-- \nAda Lovelace, Work", "the text shows it")
        XCTAssertEqual(text.selectedRange(), NSRange(location: 2, length: 0), "the caret stays after the words")
    }

    /// The text a key just changed is never put back as it was before it: words the composer hasn't heard of yet are
    /// the person's, and it takes them.
    func testTheTextNeverPutsOlderWordsBackOverTyping() async throws {
        let fresh = composer()
        let (window, text) = try await hosted(fresh)
        XCTAssertTrue(window.makeFirstResponder(text))
        // A change the composer never heard of, as if its notice were still to come.
        text.textStorage?.replaceCharacters(in: NSRange(location: 0, length: 0), with: "Typed")
        fresh.setSubject("Plans")   // something else drawn again meanwhile
        await settle(window)
        await eventually("the composer takes the words") { fresh.body == "Typed\n\n-- \nAda at the Guild" }
        XCTAssertEqual(text.string, "Typed\n\n-- \nAda at the Guild", "the words stay in the text")
    }
}
