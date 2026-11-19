import AppKit
import SwiftUI
import XCTest

/// What VoiceOver finds in Meadow's window: no picture or group without a name (the panels' paper, grain and
/// shadows are scenery), and the AI's titles, the summary's among them, as headings.
@MainActor
final class MeadowVoiceOverTests: XCTestCase {
    private var suite = ""
    /// The window the walk reads, kept open while a test acts on what it found.
    private var window: NSWindow?

    override func setUp() async throws {
        _ = NSApplication.shared
        suite = "meadow-voiceover-\(UUID().uuidString)"
        // SwiftUI builds what VoiceOver reads only once an assistive app asks; this is how one asks.
        Self.assistive(true)
    }

    override func tearDown() async throws {
        window?.close()
        window = nil
        Self.assistive(false)
        UserDefaults().removePersistentDomain(forName: suite)
    }

    func testTheWindowShowsVoiceOverNoNamelessPictureOrGroup() async throws {
        let read = try await windowRead()
        let nameless = read.filter { item in
            item.label.isEmpty && [NSAccessibility.Role.image.rawValue, NSAccessibility.Role.unknown.rawValue,
                                   "AXUnknown"].contains(item.role)
        }
        XCTAssertEqual(nameless.map(\.description), [], "what VoiceOver stops on with nothing to say:\n\(Self.said(read))")
        // The panels are groups by their names, each once.
        for panel in ["Mailboxes", "Message list", "Reading pane"] {
            XCTAssertEqual(read.filter { $0.role == NSAccessibility.Role.group.rawValue && $0.label == panel }.count, 1,
                           "the \(panel) group:\n\(Self.said(read))")
        }
    }

    func testTheSummarysTitleIsAHeading() async throws {
        let read = try await windowRead()
        XCTAssertTrue(read.contains { $0.role == "AXHeading" && $0.label == "Summary" },
                      "the AI's title, as a section's label is:\n\(Self.said(read))")
    }

    /// A message's head is a heading, read whole: who wrote, to whom and when. A conversation's messages are found by
    /// their heads.
    func testAMessagesHeadIsAHeading() async throws {
        let read = try await windowRead()
        let head = try XCTUnwrap(read.first { $0.label.contains("bo@example.test · to you") }, Self.said(read))
        XCTAssertEqual(head.role, "AXHeading", "the head:\n\(Self.said(read))")
        XCTAssertTrue(head.label.contains("Bo"), head.label)
    }

    /// The splitter is a slider VoiceOver can move: named, with the list's width as its value, a step wider or
    /// narrower at each increment or decrement, as the arrow keys move it.
    func testTheSplitterIsASliderThatMovesTheList() async throws {
        let (read, shell) = try await windowReadWithShell()
        let splitter = try XCTUnwrap(read.first { $0.label == "Resize the message list" }, Self.said(read))
        XCTAssertEqual(splitter.role, NSAccessibility.Role.slider.rawValue, "the splitter:\n\(Self.said(read))")
        XCTAssertEqual(splitter.value, "372 points wide")
        XCTAssertEqual(shell.listWidth, 372)

        XCTAssertTrue(splitter.element.accessibilityPerformIncrement?() ?? false, "it can be moved")
        XCTAssertEqual(shell.listWidth, 396)
        XCTAssertTrue(splitter.element.accessibilityPerformDecrement?() ?? false)
        XCTAssertTrue(splitter.element.accessibilityPerformDecrement?() ?? false)
        XCTAssertEqual(shell.listWidth, 348)
        try await Task.sleep(for: .milliseconds(200))
        let again = try XCTUnwrap(Self.read(splitter.root).first { $0.label == "Resize the message list" })
        XCTAssertEqual(again.value, "348 points wide")
    }

    /// What is done on Curate is said on its sheet, which covers the window's toast and holds its Undo out of reach:
    /// a rule's words, and Undo where the toast offers one.
    func testCuratesSheetSaysWhatWasDoneThereWithItsUndo() async throws {
        let mailbox = MailApp().mailbox
        let sheet = CurateSheet(model: Desk().curate, nameOf: { $0 }, open: { _ in }, part: .rules, mailbox: mailbox)
        let host = NSHostingView(rootView: sheet.meadowLight(.day))
        host.sizingOptions = []
        host.frame = CGRect(x: 0, y: 0, width: 620, height: 600)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        self.window = window

        mailbox.say("Blocked deals@spindle.example.test")
        try await Task.sleep(for: .milliseconds(400))
        var read = Self.read(host)
        XCTAssertTrue(read.contains { $0.value == "Blocked deals@spindle.example.test" }, Self.said(read))
        XCTAssertFalse(read.contains { $0.role == NSAccessibility.Role.button.rawValue && $0.label == "Undo" },
                       "a rule has no Undo, as on the page:\n\(Self.said(read))")

        mailbox.say("Blocked · swept 3", undo: "Undo")
        try await Task.sleep(for: .milliseconds(400))
        read = Self.read(host)
        XCTAssertTrue(read.contains { $0.value == "Blocked · swept 3" }, Self.said(read))
        XCTAssertTrue(read.contains { $0.role == NSAccessibility.Role.button.rawValue && $0.label == "Undo" },
                      "a cleanup's Undo:\n\(Self.said(read))")
    }

    /// Block on Curate's Rules, as a person types a sender and presses it, with the window's desk joined: the sheet
    /// says "Blocked …" as the server's answer comes, as the page's toast does, though a block that reads stored mail
    /// again reads the window again too, and the words stay while a person reads them.
    func testCuratesBlockSaysBlockedOnItsSheetAsTheAnswerComes() async throws {
        let app = MailApp()
        let routes = DeskRoutes()
        routes.answer("GET", "/api/rules", API.RuleBook(rules: DeskFixture.rules))
        var blocked = DeskFixture.rules
        blocked.blockSenders.append("offers@spindle.example.test")
        routes.answer("POST", "/api/rules", API.RuleChanged(ok: true, reclassified: 3, rules: blocked,
                                                            token: "offers@spindle.example.test", trashed: false))
        let desk = Desk()
        desk.use(app, routes: routes)
        let sheet = CurateSheet(model: desk.curate, nameOf: { $0 }, open: { _ in }, part: .rules, mailbox: app.mailbox)
        let host = NSHostingView(rootView: sheet.meadowLight(.day))
        host.sizingOptions = []
        host.frame = CGRect(x: 0, y: 0, width: 620, height: 600)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        self.window = window
        await until { desk.curate.rules != nil }
        try await Task.sleep(for: .milliseconds(200))

        // The sender typed in the rule's field, as a person types it.
        let field = try XCTUnwrap(Self.fields(in: host).first)
        XCTAssertTrue(window.makeFirstResponder(field))
        for typed in "offers@spindle.example.test" {
            window.sendEvent(try XCTUnwrap(NSEvent.keyEvent(
                with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: window.windowNumber,
                context: nil, characters: String(typed), charactersIgnoringModifiers: String(typed), isARepeat: false,
                keyCode: 0)))
        }
        try await Task.sleep(for: .milliseconds(200))

        // Block, while the server is still answering: nothing is said before it has.
        routes.hold("POST", "/api/rules")
        let block = try XCTUnwrap(Self.read(host).first {
            $0.role == NSAccessibility.Role.button.rawValue && $0.label == "Block"
        }, Self.said(Self.read(host)))
        XCTAssertTrue(block.element.accessibilityPerformPress?() ?? false)
        await until { !routes.sent("POST", "/api/rules").isEmpty }
        XCTAssertEqual(routes.sent("POST", "/api/rules").first?["verdict"], .string("block"))
        try await Task.sleep(for: .milliseconds(300))
        XCTAssertFalse(Self.read(host).contains { $0.value.hasPrefix("Blocked") }, "said only once it is done")

        let answered = Date()
        routes.release("POST", "/api/rules")
        await until { Self.read(host).contains { $0.value == "Blocked offers@spindle.example.test" } }
        let took = Date().timeIntervalSince(answered)
        XCTAssertLessThan(took, 0.5, "said as the answer comes")
        XCTAssertTrue(Self.read(host).contains { $0.label.contains("offers@spindle.example.test") },
                      "among Always trash:\n\(Self.said(Self.read(host)))")
        // A block that read stored mail again reads the window again, and the words stay.
        try await Task.sleep(for: .milliseconds(1200))
        XCTAssertTrue(Self.read(host).contains { $0.value == "Blocked offers@spindle.example.test" },
                      Self.said(Self.read(host)))
    }

    private static func fields(in view: NSView) -> [NSTextField] {
        ((view as? NSTextField).map { [$0] } ?? []) + view.subviews.flatMap(fields(in:))
    }

    /// The mail window at the mockup's 1440 by 900 on the fixture's mail, its first message open under the pane's bar
    /// with its summary, as VoiceOver reads it.
    private func windowRead() async throws -> [Read] {
        try await windowReadWithShell().read
    }

    /// The same, and the window's shell.
    private func windowReadWithShell() async throws -> (read: [Read], shell: MeadowShell) {
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
                MessageHead(opened: opened(), mine: [Fixture.ada])
                    .padding(.horizontal, 24)
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
        self.window = window
        try await Task.sleep(for: .milliseconds(600))
        let read = Self.read(host)
        XCTAssertTrue(read.contains { $0.role == NSAccessibility.Role.button.rawValue && $0.label == "File" },
                      "the walk reached the pane's bar:\n\(Self.said(read))")
        return (read, shell)
    }

    // MARK: Reading the tree

    struct Read: CustomStringConvertible {
        let role: String
        let label: String
        /// What it says of itself besides its name, as words.
        let value: String
        let size: CGSize
        let depth: Int
        /// The element itself, to act on, and the walk's root, to read again.
        let element: AnyObject
        let root: AnyObject

        var description: String {
            String(repeating: " ", count: depth) + "\(role) \"\(label)\"\(value.isEmpty ? "" : " = \(value)") "
                + "\(Int(size.width))x\(Int(size.height))"
        }
    }

    /// Every element under `element` as VoiceOver goes through them.
    static func read(_ element: Any, depth: Int = 0, root: AnyObject? = nil) -> [Read] {
        let object = element as AnyObject
        let role = (object.accessibilityRole?() ?? nil)?.rawValue ?? ""
        let label = [(object.accessibilityLabel?() ?? nil), (object.accessibilityTitle?() ?? nil)]
            .compactMap { $0 }.first { !$0.isEmpty } ?? ""
        let value = [(object.accessibilityValueDescription?() ?? nil), Self.value(of: object)]
            .compactMap { $0 }.first { !$0.isEmpty } ?? ""
        let size = object.accessibilityFrame?().size ?? .zero
        let kids = (object.accessibilityChildren?() ?? nil) ?? []
        return [Read(role: role, label: label, value: value, size: size, depth: depth, element: object,
                     root: root ?? object)]
            + kids.flatMap { read($0, depth: depth + 1, root: root ?? object) }
    }

    /// What an element says its value is, as words: nil when it says none, or none in words.
    static func value(of object: AnyObject) -> String? {
        // Read through its getter: the elements SwiftUI makes answer it whatever protocol Swift sees them through.
        guard let object = object as? NSObject, object.responds(to: NSSelectorFromString("accessibilityValue")) else {
            return nil
        }
        let value = object.value(forKey: "accessibilityValue")
        return (value as? String) ?? (value as? NSAttributedString)?.string
    }

    static func said(_ read: [Read]) -> String { read.map(\.description).joined(separator: "\n") }

    /// Tell the app an assistive app is (or no longer is) reading it, as VoiceOver does.
    static func assistive(_ on: Bool) {
        _ = NSApp.perform(NSSelectorFromString("accessibilitySetValue:forAttribute:"), with: NSNumber(value: on),
                          with: "AXEnhancedUserInterface")
    }
}
