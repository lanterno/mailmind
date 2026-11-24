import AppKit
import PDFKit
import SwiftUI
import XCTest

/// A message in a window of its own shows and does what the pane's reader does for it: the cards the desk puts in a
/// reader, what its verbs say with their Undo, the reader's single keys; it opens clear of the others, and its reply
/// box stands the pane's margin over its foot.
@MainActor
final class OwnWindowTests: XCTestCase {
    private static let key = "5c7e9a1b3d5f7a9c2e4b6d8f0a1c3e5b7d9f2a4c6e8b0d1f3a5c7e9b2d4f6a8c"
    private var folder: URL!
    private var core: Core?
    private var app: MailApp?
    private var desk: Desk?

    override func setUp() async throws {
        _ = NSApplication.shared
        folder = FileManager.default.temporaryDirectory.appending(path: "mailmind-own-window-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    override func tearDown() async throws {
        if let core { await core.close() }
        core = nil
        app = nil
        desk = nil
        try? FileManager.default.removeItem(at: folder)
    }

    /// A core over an empty home of the test's own: the frame's scheme answers through it, and asks it nothing here.
    private func openCore() async throws -> Core {
        let core = try await Core.open(CoreHome(folder: folder.path(percentEncoded: false),
                                                variables: ["MAILMIND_DB_KEY": Self.key, "MAILMIND_NOSYNC": "1"],
                                                standIn: folder.appending(path: "keychain.json")
                                                    .path(percentEncoded: false)))
        self.core = core
        return core
    }

    /// The mailbox over the verbs' fake server, its Inbox read.
    private func mailbox(_ server: FakeServer = OpenedMessageVerbsTests.server()) async -> Mailbox {
        let app = MailApp()
        self.app = app
        app.mailbox.wire = server.wire
        await app.mailbox.refresh()
        return app.mailbox
    }

    private static let copy = OpenedMessageVerbsTests.away

    /// The window's message, opened, with an invite in it.
    private static func invited() -> API.MessageOpened {
        var opened = OpenedMessageVerbsTests.head(copy, thread: nil)
        opened.invite = DeskFixture.invite
        return opened
    }

    /// The window over `copy`, as `MessageWindow` makes it, its reader holding `opened`.
    private func window(_ core: Core, _ mailbox: Mailbox, opened: API.MessageOpened, server: FakeServer,
                        room: ToastRoom? = nil) -> (AnyView, MessageWindowVerbs)
    {
        let reader = ReaderModel()
        reader.hold(Self.copy, opened)
        let verbs = MessageWindowVerbs(copy: Self.copy, mailbox: mailbox, reader: reader, routes: server.wire)
        let content = MessageWindowContent(core: core, copy: Self.copy, reader: reader, find: ReaderFind(), verbs: verbs,
                                           room: room)
        return (MessageWindow.inDesk(of: mailbox, content), verbs)
    }

    /// What `view` says, drawn as a PDF 680 points wide, its lines joined.
    private static func words(_ view: some View) -> String {
        let data = NSMutableData()
        let renderer = ImageRenderer(content: view.frame(width: 680).meadowLight(.day))
        renderer.render { size, draw in
            var box = CGRect(origin: .zero, size: size)
            guard let consumer = CGDataConsumer(data: data as CFMutableData),
                  let pdf = CGContext(consumer: consumer, mediaBox: &box, nil) else { return }
            pdf.beginPDFPage(nil)
            draw(pdf)
            pdf.endPDFPage()
            pdf.closePDF()
        }
        return (PDFDocument(data: data as Data)?.string ?? "").split(whereSeparator: \.isNewline).joined(separator: " ")
    }

    // MARK: Cards

    /// The window stands inside the mail window's desk, as the pane's reader does, so the reader's card slot shows an
    /// invite's card with RSVP there; with no desk joined to the mailbox (no mail window), nothing lends one.
    func testAnInviteInItsOwnWindowShowsItsCardAsThePaneDoes() async throws {
        let core = try await openCore()
        let mailbox = await mailbox()
        let context = ReaderContext(core: core, copy: Self.copy, opened: Self.invited(), row: nil, conversation: nil)
        func shown() -> String {
            Self.words(MessageWindow.inDesk(of: mailbox, ReaderCards(placement: .message, context: context)))
        }
        XCTAssertFalse(shown().contains(DeskFixture.invite.summary), "no desk, no card")

        let desk = Desk()
        self.desk = desk
        desk.use(try XCTUnwrap(app), routes: DeskRoutes())
        let card = shown()
        XCTAssertTrue(card.contains(DeskFixture.invite.summary), "the invite's card: \(card)")
        XCTAssertTrue(card.contains("Accept") && card.contains("Decline"), "with its RSVP: \(card)")

        mailbox.leave()
        XCTAssertFalse(shown().contains(DeskFixture.invite.summary), "the mail window gone, its desk goes with it")
    }

    // MARK: What its verbs say

    /// Delete from the window says so with Undo in the window too, where the person is, as well as in the mail
    /// window's toast.
    func testDeleteSaysSoWithUndoInTheWindowToo() async throws {
        let core = try await openCore()
        let server = OpenedMessageVerbsTests.server()
        let mailbox = await mailbox(server)
        let (view, verbs) = window(core, mailbox, opened: OpenedMessageVerbsTests.head(Self.copy, thread: nil),
                                   server: server)
        verbs.file(.trash)
        await until { !server.asked(API.Post.Trash.path).isEmpty }
        await until { mailbox.toast?.undo != nil }
        XCTAssertTrue(verbs.left, "the message has left: the window closes")
        let said = try XCTUnwrap(mailbox.toast?.text)
        XCTAssertTrue(Self.words(view).contains(said), "the window says “\(said)” too")
    }

    // MARK: Its keys

    /// d deletes the window's message and z undoes it, as they do from the pane.
    func testDDeletesAndZUndoesAsFromThePane() async throws {
        let server = OpenedMessageVerbsTests.server()
        let mailbox = await mailbox(server)
        let reader = ReaderModel()
        reader.hold(Self.copy, OpenedMessageVerbsTests.head(Self.copy, thread: nil))
        let verbs = MessageWindowVerbs(copy: Self.copy, mailbox: mailbox, reader: reader, routes: server.wire)
        XCTAssertTrue(verbs.key("d"))
        await until { !server.asked(API.Post.Trash.path).isEmpty }
        await until { mailbox.toast?.undo != nil }
        XCTAssertTrue(verbs.key("z"))
        await until { !server.asked(API.Post.Undo.path).isEmpty }
    }

    /// The page reader's single keys act on the window's message alone: e, b, s and u at once, m asks where and h
    /// asks when, and nothing else runs while the chooser is up. A key the reader doesn't have is left for the window.
    func testTheReadersSingleKeysActOnTheWindowsMessage() async throws {
        let server = OpenedMessageVerbsTests.server()
        let mailbox = await mailbox(server)
        func verbs() -> MessageWindowVerbs {
            let reader = ReaderModel()
            reader.hold(Self.copy, OpenedMessageVerbsTests.head(Self.copy, thread: nil))
            return MessageWindowVerbs(copy: Self.copy, mailbox: mailbox, reader: reader, routes: server.wire)
        }
        for (key, path) in [("e", API.Post.Archive.path), ("b", API.Post.Junk.path), ("s", API.Post.Flag.path),
                            ("u", API.Post.Seen.path)] as [(Character, String)] {
            let before = server.asked(path).count
            XCTAssertTrue(verbs().key(key), "\(key)")
            await until { server.asked(path).count > before }
            let sent = try XCTUnwrap(server.asked(path).last?.body)
            XCTAssertTrue(String(decoding: sent, as: UTF8.self).contains("\"uid\":90"), "\(key) acts on the window's")
        }
        let moving = verbs()
        XCTAssertTrue(moving.key("m"))
        XCTAssertEqual(moving.moving, MoveAsk(account: Fixture.ada, from: "INBOX"), "m asks where, over the window")
        let snoozing = verbs()
        XCTAssertTrue(snoozing.key("h"))
        XCTAssertTrue(snoozing.choosingSnooze, "h asks when")
        XCTAssertFalse(snoozing.key("e"), "nothing else while the chooser asks")
        XCTAssertFalse(verbs().key("q"), "a key the reader doesn't have")
        XCTAssertFalse(verbs().key("j"), "nor a list's")
    }

    /// The keys come as AppKit hands them: a key nothing in the window took reaches its catcher, after the window, and
    /// runs; one typed in a field, or with ⌘ held, or while the single keys are off, never does.
    func testAKeyNothingTookIsTheReadersAndATypedOneNever() async throws {
        let server = OpenedMessageVerbsTests.server()
        let mailbox = await mailbox(server)
        let reader = ReaderModel()
        reader.hold(Self.copy, OpenedMessageVerbsTests.head(Self.copy, thread: nil))
        let verbs = MessageWindowVerbs(copy: Self.copy, mailbox: mailbox, reader: reader, routes: server.wire)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let content = NSView(frame: window.contentLayoutRect)
        window.contentView = content
        let catcher = MessageWindowKeys.Catcher.hang(on: window)
        XCTAssertTrue(MessageWindowKeys.Catcher.hang(on: window) === catcher, "once a window")
        catcher.verbs = verbs
        func type(_ key: String, _ code: UInt16, held: NSEvent.ModifierFlags = []) throws {
            window.sendEvent(try XCTUnwrap(NSEvent.keyEvent(
                with: .keyDown, location: .zero, modifierFlags: held, timestamp: 0, windowNumber: window.windowNumber,
                context: nil, characters: key, charactersIgnoringModifiers: key, isARepeat: false, keyCode: code)))
        }
        XCTAssertTrue(window.makeFirstResponder(content))
        try type("s", 1, held: .command)
        try type("s", 1)
        await until { server.asked(API.Post.Flag.path).count == 1 }

        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 100, height: 22))
        content.addSubview(field)
        XCTAssertTrue(window.makeFirstResponder(field))
        try type("e", 14)
        XCTAssertEqual(field.currentEditor()?.string, "e", "typed in the field")
        XCTAssertNil(MessageWindowKeys.Catcher.single(try XCTUnwrap(NSEvent.keyEvent(
            with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: 0, context: nil,
            characters: "e", charactersIgnoringModifiers: "e", isARepeat: false, keyCode: 14)), in: window, verbs: verbs),
            "never while a field has the keyboard")
        XCTAssertTrue(window.makeFirstResponder(content))
        verbs.typing = true
        XCTAssertNil(MessageWindowKeys.Catcher.single(try XCTUnwrap(NSEvent.keyEvent(
            with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: 0, context: nil,
            characters: "e", charactersIgnoringModifiers: "e", isARepeat: false, keyCode: 14)), in: window, verbs: verbs),
            "nor while the reply box is typed in")
        verbs.typing = false
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(server.asked(API.Post.Archive.path).isEmpty, "no e reached the message")
        let hash = try XCTUnwrap(NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: .shift, timestamp: 0,
                                                  windowNumber: 0, context: nil, characters: "#",
                                                  charactersIgnoringModifiers: "#", isARepeat: false, keyCode: 20))
        XCTAssertEqual(MessageWindowKeys.Catcher.single(hash, in: window, verbs: verbs), "#", "⇧ for # and ?")
        window.contentView = nil
    }

    /// Under a layout whose letters aren't Latin (Arabic's characters on the US key codes, as macOS hands them over),
    /// a key nothing took is the reader's as the key it stands on: س stars and ث archives, as s and e do. With ⌘ held
    /// it is the menus', and a field gets the Arabic typed in it.
    func testUnderANonLatinLayoutAKeyIsTheOneItStandsOn() async throws {
        let close = try KeyLayouts.arabic()
        defer { close() }
        let server = OpenedMessageVerbsTests.server()
        let mailbox = await mailbox(server)
        let reader = ReaderModel()
        reader.hold(Self.copy, OpenedMessageVerbsTests.head(Self.copy, thread: nil))
        let verbs = MessageWindowVerbs(copy: Self.copy, mailbox: mailbox, reader: reader, routes: server.wire)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        let content = NSView(frame: window.contentLayoutRect)
        window.contentView = content
        defer { window.contentView = nil }
        MessageWindowKeys.Catcher.hang(on: window).verbs = verbs

        XCTAssertTrue(window.makeFirstResponder(content))
        window.sendEvent(try KeyLayouts.event("س", 1, held: .command, in: window))
        window.sendEvent(try KeyLayouts.event("س", 1, in: window))
        await until { server.asked(API.Post.Flag.path).count == 1 }

        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 100, height: 22))
        content.addSubview(field)
        XCTAssertTrue(window.makeFirstResponder(field))
        window.sendEvent(try KeyLayouts.event("ث", 14, in: window))
        XCTAssertEqual(field.currentEditor()?.string, "ث", "typed in the field")
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertTrue(server.asked(API.Post.Archive.path).isEmpty, "no e reached the message")

        XCTAssertTrue(window.makeFirstResponder(content))
        window.sendEvent(try KeyLayouts.event("ث", 14, in: window))
        await until { !server.asked(API.Post.Archive.path).isEmpty }
        XCTAssertEqual(server.asked(API.Post.Flag.path).count, 1, "⌘س starred nothing")
    }

    /// e and d reach the message in its window as s does, past the Edit menu, whose Emoji & Symbols and Start
    /// Dictation the menu bar matches on e and d alone: the keys pressed as a person presses them, the window's content
    /// as it opens, with nothing in it holding the keyboard.
    func testEAndDFileTheMessageInItsWindowPastTheEditMenu() async throws {
        let core = try await openCore()
        let (taken, restore) = RealKeys.withEditMenu()
        defer { restore() }
        for (typed, code, path) in [("e", UInt16(14), API.Post.Archive.path), ("d", 2, API.Post.Trash.path)] {
            let server = OpenedMessageVerbsTests.server()
            let mailbox = await mailbox(server)
            let reader = ReaderModel()
            reader.hold(Self.copy, OpenedMessageVerbsTests.head(Self.copy, thread: nil))
            let verbs = MessageWindowVerbs(copy: Self.copy, mailbox: mailbox, reader: reader, routes: server.wire)
            let content = MessageWindowContent(core: core, copy: Self.copy, reader: reader, find: ReaderFind(),
                                               verbs: verbs)
            let host = NSHostingView(rootView: MessageWindow.inDesk(of: mailbox, content).meadowLight(.day))
            host.frame = CGRect(x: 0, y: 0, width: 820, height: 700)
            let window = NSWindow(contentRect: host.frame, styleMask: [.titled], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.contentView = host
            RealKeys.key(window)
            for _ in 0..<20 {
                host.layoutSubtreeIfNeeded()
                try await Task.sleep(for: .milliseconds(10))
            }
            try RealKeys.press("s", 1, in: window)
            await until { server.asked(API.Post.Flag.path).count == 1 }
            try RealKeys.press(typed, code, in: window)
            await until { !server.asked(path).isEmpty }
            XCTAssertFalse(server.asked(path).isEmpty, "\(typed) files the message")
            window.orderOut(nil)
            window.contentView = nil
        }
        XCTAssertEqual(taken.keys, [], "the Edit menu took neither")
    }

    // MARK: Find

    /// ⌘F in a message's own window finds in its message, as Edit › Find in Message says there, from the window
    /// itself, which hears a key equivalent before the menu bar: whichever window's focused values the menus still
    /// read, Mailbox › Search's ⌘F never takes it, under ABC or Arabic. Before the message has opened there is nothing
    /// to find, and ⌘F goes on.
    func testCommandFFindsInTheMessageFromItsWindowWhateverTheMenusRead() async throws {
        let core = try await openCore()
        let server = OpenedMessageVerbsTests.server()
        let mailbox = await mailbox(server)
        let reader = ReaderModel()
        let find = ReaderFind()
        let verbs = MessageWindowVerbs(copy: Self.copy, mailbox: mailbox, reader: reader, routes: server.wire)
        let content = MessageWindowContent(core: core, copy: Self.copy, reader: reader, find: find, verbs: verbs)
        let host = NSHostingView(rootView: MessageWindow.inDesk(of: mailbox, content).meadowLight(.day))
        host.frame = CGRect(x: 0, y: 0, width: 820, height: 700)
        // The window as a message's opens, made key: far off the screen and see-through.
        let window = NSWindow(contentRect: host.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        window.alphaValue = 0
        window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
        window.makeKeyAndOrderFront(nil)
        defer {
            window.orderOut(nil)
            window.contentView = nil
        }
        func settle() async throws {
            for _ in 0..<20 {
                host.layoutSubtreeIfNeeded()
                try await Task.sleep(for: .milliseconds(10))
            }
        }
        func command(_ typed: String, _ code: UInt16, held: NSEvent.ModifierFlags = .command) throws -> NSEvent {
            try KeyLayouts.event(typed, code, held: held, in: window)
        }
        try await settle()
        XCTAssertFalse(window.performKeyEquivalent(with: try command("f", 3)), "nothing to find before it opens")

        reader.hold(Self.copy, OpenedMessageVerbsTests.head(Self.copy, thread: nil))
        try await settle()
        XCTAssertTrue(window.performKeyEquivalent(with: try command("f", 3)), "the window's own, before the menus")
        XCTAssertTrue(find.open, "Find in Message's bar opens")
        XCTAssertFalse(mailbox.searchFocus, "and the mail window's search is left alone")

        find.close()
        let close = try KeyLayouts.arabic()
        defer { close() }
        XCTAssertTrue(window.performKeyEquivalent(with: try command("ب", 3)), "⌘F where Arabic's ب is F")
        XCTAssertTrue(find.open)
        XCTAssertFalse(MessageWindowKeys.finds(try command("f", 3, held: [.option, .command])), "⌥⌘F is the menu's")
        XCTAssertFalse(MessageWindowKeys.finds(try command("F", 3, held: [.shift, .command])), "⇧⌘F forwards")
        XCTAssertFalse(MessageWindowKeys.finds(try command("ب", 3, held: [])), "and F alone is no find")
    }

    // MARK: Where it opens

    /// A second window over the first opens a step down and to the right of it, a third further on, as a Mac cascades;
    /// one with room where it stands stays there.
    func testAMessagesWindowOpensClearOfTheOthers() {
        let frame = NSRect(x: 200, y: 100, width: 820, height: 760)
        XCTAssertEqual(OwnWindowPlace.topLeft(of: frame, among: []), NSPoint(x: 200, y: 860))
        XCTAssertEqual(OwnWindowPlace.topLeft(of: frame, among: [frame]), NSPoint(x: 222, y: 838))
        XCTAssertEqual(OwnWindowPlace.topLeft(of: frame, among: [frame, frame.offsetBy(dx: 22, dy: -22)]),
                       NSPoint(x: 244, y: 816))
        XCTAssertEqual(OwnWindowPlace.topLeft(of: frame, among: [frame.offsetBy(dx: 300, dy: 0)]),
                       NSPoint(x: 200, y: 860))

        func opened() -> NSWindow {
            let window = NSWindow(contentRect: frame, styleMask: [.titled], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.alphaValue = 0
            window.orderFront(nil)
            OwnWindowPlace.opened(window)
            return window
        }
        let first = opened(), second = opened()
        defer { [first, second].forEach { $0.orderOut(nil) } }
        XCTAssertNotEqual(second.frame.origin, first.frame.origin, "the second doesn't hide the first")
        XCTAssertEqual(second.frame.minX - first.frame.minX, OwnWindowPlace.step)
        XCTAssertEqual(first.frame.maxY - second.frame.maxY, OwnWindowPlace.step)
    }

    // MARK: Its foot

    /// Scrolled to its foot, the window's reply box stands the pane's margin over the window's bottom edge, as it
    /// stands over the pane's at the window's width: a phone's margin in a narrow window, the wide one in a wide window.
    func testTheReplyBoxStandsThePanesMarginOverTheWindowsFoot() async throws {
        let core = try await openCore()
        let server = OpenedMessageVerbsTests.server()
        let mailbox = await mailbox(server)
        let height: CGFloat = 360
        for width in [640, 820] as [CGFloat] {
            let room = ToastRoom()
            let view = window(core, mailbox, opened: OpenedMessageVerbsTests.head(Self.copy, thread: nil),
                              server: server, room: room).0
                .meadowLight(.day)
            let host = NSHostingView(rootView: view.frame(width: width, height: height))
            host.frame = CGRect(x: 0, y: 0, width: width, height: height)
            let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.contentView = host
            func settle() async throws {
                for _ in 0..<30 {
                    host.layoutSubtreeIfNeeded()
                    try await Task.sleep(for: .milliseconds(15))
                }
            }
            try await settle()
            // The body's frame shows once it has measured its document (here the core's short one for a message it
            // doesn't hold), and the window is laid out again at that height before it is scrolled.
            let bodies = Self.frames(host)
            XCTAssertFalse(bodies.isEmpty, "the message's body")
            for _ in 0..<400 where bodies.contains(where: \.isHidden) { try await Task.sleep(for: .milliseconds(10)) }
            try await settle()
            let scroll = try XCTUnwrap(Self.scrolls(host).max { $0.frame.height < $1.frame.height })
            let document = try XCTUnwrap(scroll.documentView)
            XCTAssertGreaterThan(document.bounds.height, scroll.contentView.bounds.height, "the message runs past the foot")
            document.scroll(NSPoint(x: 0, y: document.bounds.height - scroll.contentView.bounds.height))
            try await settle()
            let reply = try XCTUnwrap(room.reply, "\(width)")
            XCTAssertEqual(height - reply.maxY, PaneLayout(width: width, measure: ReadingPrefs(nil).measure).padding.bottom,
                           accuracy: 1, "the pane's margin under it at \(width) points")
            window.contentView = nil
        }
    }

    /// The message bodies' frames under `view`.
    private static func frames(_ view: NSView) -> [FrameWebView] {
        view.subviews.flatMap { sub in (sub as? FrameWebView).map { [$0] } ?? frames(sub) }
    }

    private static func scrolls(_ view: NSView) -> [NSScrollView] {
        view.subviews.flatMap { sub in (sub as? NSScrollView).map { [$0] + scrolls($0) } ?? scrolls(sub) }
    }
}
