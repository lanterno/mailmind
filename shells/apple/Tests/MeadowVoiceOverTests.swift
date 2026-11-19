import AppKit
import SwiftUI
import XCTest

/// What VoiceOver finds in Meadow's window: the AI's titles, the summary's among them, as headings.
@MainActor
final class MeadowVoiceOverTests: XCTestCase {
    private var suite = ""

    override func setUp() async throws {
        _ = NSApplication.shared
        suite = "meadow-voiceover-\(UUID().uuidString)"
        // SwiftUI builds what VoiceOver reads only once an assistive app asks; this is how one asks.
        Self.assistive(true)
    }

    override func tearDown() async throws {
        Self.assistive(false)
        UserDefaults().removePersistentDomain(forName: suite)
    }

    func testTheSummarysTitleIsAHeading() async throws {
        let read = try await windowRead()
        XCTAssertTrue(read.contains { $0.role == "AXHeading" && $0.label == "Summary" },
                      "the AI's title, as a section's label is:\n\(Self.said(read))")
    }

    /// The mail window at the mockup's 1440 by 900 on the fixture's mail, its first message open under the pane's bar
    /// with its summary, as VoiceOver reads it.
    private func windowRead() async throws -> [Read] {
        let app = MailApp()
        app.mailbox.wire = Fixture.server().wire
        await app.mailbox.refresh()
        app.mailbox.tab = .all
        app.openList()
        await until { app.list?.copy(at: 0) != nil }
        let row = try XCTUnwrap(app.list?.copy(at: 0))
        app.mailbox.chose([row])
        let desk = Desk()
        let shell = MeadowShell(defaults: try XCTUnwrap(UserDefaults(suiteName: suite)))
        let frame = MeadowFrame(app: app, mailbox: app.mailbox, shell: shell, status: AppStatus.shared, lights: false) {
            VStack(spacing: 0) {
                MeadowPaneBar(app: app, mailbox: app.mailbox, writing: PaneWriting(replyTo: MailCopy(row)))
                SummaryCard(copy: MailCopy(row), stored: ("Lunch on Thursday, at the noodle place.", "Say yes"),
                            cards: desk.summaries, author: "Lin Mori", authorAddress: "lin@example.test")
                    .padding(24)
                Spacer(minLength: 0)
            }
        }
        .environment(desk)
        let host = NSHostingView(rootView: frame.meadowLight(.day).frame(width: 1440, height: 900))
        host.sizingOptions = []
        host.frame = CGRect(x: 0, y: 0, width: 1440, height: 900)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        window.layoutIfNeeded()
        try await Task.sleep(for: .milliseconds(600))
        let read = Self.read(host)
        XCTAssertTrue(read.contains { $0.role == NSAccessibility.Role.button.rawValue && $0.label == "File" },
                      "the walk reached the pane's bar:\n\(Self.said(read))")
        return read
    }

    // MARK: Reading the tree

    struct Read: CustomStringConvertible {
        let role: String
        let label: String
        let size: CGSize
        let depth: Int

        var description: String {
            String(repeating: " ", count: depth) + "\(role) \"\(label)\" \(Int(size.width))x\(Int(size.height))"
        }
    }

    /// Every element under `element` as VoiceOver goes through them.
    static func read(_ element: Any, depth: Int = 0) -> [Read] {
        let object = element as AnyObject
        let role = (object.accessibilityRole?() ?? nil)?.rawValue ?? ""
        let label = [(object.accessibilityLabel?() ?? nil), (object.accessibilityTitle?() ?? nil)]
            .compactMap { $0 }.first { !$0.isEmpty } ?? ""
        let size = object.accessibilityFrame?().size ?? .zero
        let kids = (object.accessibilityChildren?() ?? nil) ?? []
        return [Read(role: role, label: label, size: size, depth: depth)]
            + kids.flatMap { read($0, depth: depth + 1) }
    }

    static func said(_ read: [Read]) -> String { read.map(\.description).joined(separator: "\n") }

    /// Tell the app an assistive app is (or no longer is) reading it, as VoiceOver does.
    static func assistive(_ on: Bool) {
        _ = NSApp.perform(NSSelectorFromString("accessibilitySetValue:forAttribute:"), with: NSNumber(value: on),
                          with: "AXEnhancedUserInterface")
    }
}
