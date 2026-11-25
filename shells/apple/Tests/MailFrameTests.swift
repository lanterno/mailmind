import AppKit
import WebKit
import XCTest

/// A mail body's frame under the policy the core really sends with it: the document comes through the frame's own
/// scheme handler, answered by a test core's `/api/message/body.html` with the core's headers (the sandbox that runs
/// no script among them) and the test's mail as its body. Under that policy no listener or timer runs in any world of
/// the document, so the frame must still measure its content, follow a link a person clicks and say the link under
/// the pointer, while nothing of the mail's own runs.
@MainActor
final class MailFrameTests: XCTestCase {
    private static let key = "8d2b6f0a4c8e2a6d0f4b8c2e6a0d4f8b2c6e0a4d8f2b6c0e4a8d2f6b0c4e8a2d"
    private static let copy = MailCopy(key: 7, folder: "INBOX", uid: 7, account: "ada@example.test")
    private var folder: URL!
    private var core: Core?
    private var shown: [Shown] = []

    override func setUp() async throws {
        _ = NSApplication.shared
        folder = FileManager.default.temporaryDirectory.appending(path: "mailmind-frame-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        core = try await Core.open(CoreHome(folder: folder.path(percentEncoded: false),
                                            variables: ["MAILMIND_DB_KEY": Self.key, "MAILMIND_NOSYNC": "1"],
                                            standIn: folder.appending(path: "keychain.json").path(percentEncoded: false)))
    }

    override func tearDown() async throws {
        for frame in shown {
            MailFrame.dismantleNSView(frame.view, coordinator: frame.walls)
            frame.window.close()
        }
        shown = []
        // A key WebKit gave back stays the app's current event, and AppKit eases a scroll made under one, so the next
        // test's reader wouldn't move at once: an event of no kind, taken from the queue, takes its place.
        if NSApp.currentEvent?.type == .keyDown, let none = NSEvent.otherEvent(
            with: .applicationDefined, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: 0, context: nil,
            subtype: 0, data1: 0, data2: 0)
        {
            NSApp.postEvent(none, atStart: true)
            _ = NSApp.nextEvent(matching: .applicationDefined, until: .distantFuture, inMode: .default, dequeue: true)
        }
        if let core { await core.close() }
        core = nil
        try? FileManager.default.removeItem(at: folder)
    }

    // MARK: The frame

    /// A frame on the screen as the reader shows one, and what it told.
    @MainActor
    private final class Shown {
        let view: FrameWebView
        let walls: FrameGuard
        let window: NSWindow
        var heights: [CGFloat] = []
        var links: [FrameLink] = []
        var hovers: [URL?] = []
        var loaded: Bool?
        /// The reader the frame stands in, once `read` has put it in one.
        var reader: NSScrollView?
        /// How far down the reader's document the frame stands: the message's head above it.
        var head: CGFloat = 0

        init(view: FrameWebView, walls: FrameGuard, window: NSWindow) {
            self.view = view
            self.walls = walls
            self.window = window
        }

        /// The frame as tall as it last told, and the pane's 4 points, as `MessageContent` gives it; and room under
        /// it in the reader's document for the files and the reply box.
        func layOut() {
            guard let reader, let document = reader.documentView else { return }
            let width = reader.contentSize.width
            let tall = heights.last.map { $0 + 4 } ?? 400
            document.frame = NSRect(x: 0, y: 0, width: width, height: head + tall + 200)
            view.frame = NSRect(x: 0, y: head, width: width, height: tall)
        }
    }

    /// The reader's document: flipped, as SwiftUI's is.
    private final class Paper: NSView {
        override var isFlipped: Bool { true }
    }

    /// The document the core makes of a body (`mm_content::frame`): its `<base target="_blank">` and nothing else
    /// in its head.
    private static func document(_ body: String) -> String {
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><base target=\"_blank\"></head><body>"
            + body + "</body></html>"
    }

    /// The frame's route as the core answers it, with `body` in place of the stored mail it would have read: the
    /// core's headers, the policy among them, as it sent them for this very request.
    private func source(_ body: String) throws -> FrameSource {
        let core = try XCTUnwrap(core)
        let document = Data(Self.document(body).utf8)
        return FrameSource { target in
            let answer = await core.send("GET", target, Data())
            return CoreAnswer(status: 200, contentType: answer.contentType, headers: answer.headers, body: document)
        }
    }

    /// The policy the core sends with a frame's document: a sandbox that keeps the origin and allows no script.
    private func policy() async throws -> String {
        let core = try XCTUnwrap(core)
        let target = try XCTUnwrap(ReaderModel.frameURL(Self.copy).flatMap(FrameScheme.target(of:)))
        let answer = await core.send("GET", target, Data())
        return try XCTUnwrap(answer.headers.first { $0.name.lowercased() == "content-security-policy" }?.value)
    }

    /// `body` in a frame `width` points wide, in a window of its own, as the reader makes one: the frame's least
    /// height until it is measured, and what it tells kept. `printed`: the copy File › Print makes of it instead, with
    /// a head and the look on paper.
    private func frame(_ body: String, width: CGFloat = 480, html: Bool = true, printed: Bool = false) async throws
        -> Shown
    {
        let policy = try await policy()
        XCTAssertTrue(policy.hasPrefix("sandbox allow-same-origin;"), policy)
        XCTAssertFalse(policy.contains("allow-scripts"), "the document's sandbox runs no script: \(policy)")
        let url = try XCTUnwrap(ReaderModel.frameURL(Self.copy))
        let look = printed ? MessagePrinter.look(html: html, prefs: ReadingPrefs())
            : MailLook.of(html: html, prefs: .init(), darkAppearance: false, dark: ReaderPalette.of(dark: true),
                          light: ReaderPalette.of(dark: false))
        let walls = FrameGuard(source: try source(body), url: url, look: look, html: html)
        if printed { walls.printHead = "<div id=\"printhead\">Your tickets</div>" }
        let view = MailFrame.webView(walls, title: "Email body")
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: 400), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = view
        window.orderFront(nil)
        let frame = Shown(view: view, walls: walls, window: window)
        walls.told = { [weak frame] message in
            switch message {
            case .height(let height):
                frame?.heights.append(height)
                frame?.layOut()
            case .link(let link): frame?.links.append(link)
            case .hover(let url): frame?.hovers.append(url)
            }
        }
        walls.loaded = { [weak frame] ok in frame?.loaded = ok }
        shown.append(frame)
        return frame
    }

    /// What the test asks the document, in a world of the test's own.
    private func asked(_ frame: Shown, _ script: String) async throws -> Any? {
        try await frame.view.callAsyncJavaScript(script, arguments: [:], in: nil, contentWorld: .world(name: "test"))
    }

    /// `frame` in a reader as the pane lays one out: a scroll view as tall as the window, `head` points of the
    /// message's head above the frame in its document, and the frame as tall as it tells.
    @discardableResult
    private func read(_ frame: Shown, head: CGFloat = 120) -> NSScrollView {
        let reader = NSScrollView(frame: frame.window.contentLayoutRect)
        reader.hasVerticalScroller = true
        reader.documentView = Paper(frame: .zero)
        frame.window.contentView = reader
        reader.documentView?.addSubview(frame.view)
        frame.reader = reader
        frame.head = head
        frame.layOut()
        // The app the tests run in is never active, so no window of it is key; asked to be, this one is where the
        // app sends the keys it is handed (`NSApp.sendEvent`), as it sends a person's to the reader's window.
        frame.window.makeKeyAndOrderFront(nil)
        return reader
    }

    /// How far the frame's document has scrolled itself, down.
    private func scrolledInside(_ frame: Shown) async throws -> Double {
        let read = try await asked(frame, "return window.scrollY;") as? NSNumber
        return try XCTUnwrap(read).doubleValue
    }

    /// Whether `rect`, in the frame's viewport, sits well inside what the reader shows, never at its edge.
    private func wellInside(_ rect: CGRect, of frame: Shown) throws -> Bool {
        let clip = try XCTUnwrap(frame.reader).contentView
        let y = frame.view.isFlipped ? rect.minY : frame.view.bounds.height - rect.maxY
        let inReader = clip.convert(NSRect(x: rect.minX, y: y, width: rect.width, height: rect.height), from: frame.view)
        let shown = clip.bounds
        return rect.height > 0 && inReader.minY >= shown.minY + 60 && inReader.maxY <= shown.maxY - 60
    }

    /// The rectangle `script` returns as `[left, top, width, height]`, in the frame's viewport: nil for none.
    private func rect(_ frame: Shown, _ script: String) async throws -> CGRect? {
        guard let read = try await asked(frame, script) as? [NSNumber], read.count == 4 else { return nil }
        return CGRect(x: read[0].doubleValue, y: read[1].doubleValue, width: read[2].doubleValue,
                      height: read[3].doubleValue)
    }

    /// Where the selection Find made is.
    private func selection(_ frame: Shown) async throws -> CGRect? {
        try await rect(frame, """
            const s = window.getSelection();
            if (!s || s.rangeCount === 0) return null;
            const r = s.getRangeAt(0).getBoundingClientRect();
            return [r.left, r.top, r.width, r.height];
            """)
    }

    /// Where the element `selector` picks is.
    private func place(of selector: String, in frame: Shown) async throws -> CGRect {
        let read = try await rect(frame, """
            const r = document.querySelector("\(selector)").getBoundingClientRect();
            return [r.left, r.top, r.width, r.height];
            """)
        return try XCTUnwrap(read)
    }

    /// The middle of the element `id`, in the frame's viewport.
    private func middle(of id: String, in frame: Shown) async throws -> CGPoint {
        let read = try await asked(frame, """
            const r = document.getElementById("\(id)").getBoundingClientRect();
            return [r.left + r.width / 2, r.top + r.height / 2];
            """) as? [NSNumber]
        let point = try XCTUnwrap(read)
        return CGPoint(x: point[0].doubleValue, y: point[1].doubleValue)
    }

    /// The content's height as the document has it now, at the frame's width.
    private func needs(_ frame: Shown) async throws -> CGFloat {
        let read = try await asked(frame, "return document.body.scrollHeight;") as? NSNumber
        return CGFloat(try XCTUnwrap(read).doubleValue)
    }

    /// Wait until `done`, for as long as a frame may take to load and settle.
    private func until(_ done: () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
        let started = ContinuousClock.now
        while !done() {
            guard ContinuousClock.now - started < .seconds(8) else {
                XCTFail("still waiting after eight seconds", file: file, line: line)
                return
            }
            try? await Task.sleep(for: .milliseconds(10))
        }
    }

    /// Wait until `done` says so, asking again and again, for as long as a frame may take.
    private func eventually(_ what: String, file: StaticString = #filePath, line: UInt = #line,
                            _ done: () async throws -> Bool) async throws
    {
        let started = ContinuousClock.now
        while try await !done() {
            guard ContinuousClock.now - started < .seconds(5) else {
                return XCTFail("still waiting after five seconds: \(what)", file: file, line: line)
            }
            try await Task.sleep(for: .milliseconds(20))
        }
    }

    /// Wait until the frame's document has loaded and the frame shows: a person clicks only what they can see.
    private func loaded(_ frame: Shown, file: StaticString = #filePath, line: UInt = #line) async {
        await until({ frame.loaded != nil && !frame.view.isHidden }, file: file, line: line)
        XCTAssertEqual(frame.loaded, true, file: file, line: line)
    }

    /// Wait until the frame has loaded and told the height its content needs now, taller than `above`, and return it.
    @discardableResult
    private func settled(_ frame: Shown, above: CGFloat = 0, file: StaticString = #filePath, line: UInt = #line)
        async throws -> CGFloat
    {
        await loaded(frame, file: file, line: line)
        let started = ContinuousClock.now
        while true {
            let needs = try await needs(frame)
            if needs > above, frame.heights.last == needs { return needs }
            guard ContinuousClock.now - started < .seconds(8) else {
                XCTFail("the content needs \(needs), and the frame told \(frame.heights)", file: file, line: line)
                return needs
            }
            try await Task.sleep(for: .milliseconds(20))
        }
    }

    // MARK: Events, as AppKit hands them to the frame

    /// A click at `point`, as the kit routes it to the view under the pointer, which is the frame: down there, and up
    /// `moved` points on. (With no drag between: a link dragged would start a drag session, which waits for the
    /// window server's mouse.)
    private func click(at point: CGPoint, in frame: Shown, moved: CGFloat = 0) throws {
        XCTAssertTrue(RealMouse.view(at: frame.view.convert(point, to: nil), in: frame.window) === frame.view,
                      "the frame takes a click on its body")
        let down = frame.view.convert(point, to: nil)
        try RealMouse.send([try RealMouse.event(.leftMouseDown, at: down, in: frame.window),
                            try RealMouse.event(.leftMouseUp, at: CGPoint(x: down.x + moved, y: down.y),
                                                in: frame.window)], in: frame.window)
    }

    private func function(_ key: Int) -> String {
        String(Character(UnicodeScalar(UInt32(key))!))
    }

    // MARK: Its height

    private static let gif = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="

    private static let newsletter = "<h1>The guild's autumn letter</h1>"
        + (1...60).map { "<p>Paragraph \($0): the long table, the menu and who brings the bread.</p>" }.joined()

    /// A 60-paragraph newsletter measures its whole height, thousands of points, not the 400 the reader starts a
    /// frame at; and it shows, handing the wheel on to the reader, which scrolls it as one.
    func testALongBodyMeasuresItsWholeHeight() async throws {
        let frame = try await frame(Self.newsletter)
        let height = try await settled(frame)
        XCTAssertGreaterThan(height, 3000, "the whole letter")
        XCTAssertFalse(frame.view.isHidden)
        XCTAssertTrue(frame.view.forwardsScroll, "the reader scrolls the frame as one")
    }

    /// A narrower frame wraps its lines anew, and measures again, taller.
    func testANewWidthMeasuresAgain() async throws {
        let frame = try await frame(Self.newsletter)
        let wide = try await settled(frame)
        frame.window.setContentSize(NSSize(width: 240, height: 400))
        let narrow = try await settled(frame, above: wide)
        XCTAssertGreaterThan(narrow, wide, "more lines at 240 points")
    }

    /// A folded quote, its `<details>` opened by a click on its summary (natively: no script runs in the frame),
    /// makes the body taller, and the frame measures again.
    func testAQuoteUnfoldedMeasuresAgain() async throws {
        let frame = try await frame("<p>Thursday, then.</p><details id=\"quote\"><summary id=\"fold\">Show quoted text"
                                    + "</summary><div style=\"height:900px\">On Mon, Bo wrote: shall we?</div></details>")
        let folded = try await settled(frame)
        try click(at: try await middle(of: "fold", in: frame), in: frame)
        let unfolded = try await settled(frame, above: folded)
        XCTAssertGreaterThanOrEqual(unfolded, folded + 800, "the quote's height counted")
    }

    /// An image that comes in after the document has loaded (a person showed the images, and one arrives late)
    /// makes the body taller: the frame measures again for a while after it loads.
    func testABodyThatGrowsAfterItLoadedMeasuresAgain() async throws {
        let frame = try await frame("<p>Your tickets:</p><img id=\"code\" alt=\"\" src=\"\(Self.gif)\" "
                                    + "style=\"display:block;width:200px;height:0\"><p>See you there.</p>")
        let before = try await settled(frame)
        try await Task.sleep(for: .milliseconds(600))
        _ = try await asked(frame, "document.getElementById(\"code\").style.height = \"700px\"; return null;")
        let grown = try await settled(frame, above: before)
        XCTAssertGreaterThanOrEqual(grown, before + 600, "the image's height counted")
    }

    // MARK: Its links

    /// A link clicked goes to the opener with its href and its text, the text the mail shows, which `LinkRisk`
    /// checks the address against; the frame itself stays on the message.
    func testALinkClickedGoesToTheOpenerWithItsHrefAndText() async throws {
        let frame = try await frame("<p>Please confirm at <a id=\"bank\" href=\"https://evil.example.test/login\">"
                                    + "<b>bank.example.test</b></a> today.</p>")
        await loaded(frame)
        try click(at: try await middle(of: "bank", in: frame), in: frame)
        await until { !frame.links.isEmpty }
        let link = FrameLink(href: "https://evil.example.test/login", text: "bank.example.test")
        XCTAssertEqual(frame.links, [link])
        guard case .browse(_, let warning?) = LinkOpening.of(href: link.href, text: link.text, senderDomain: "example.test")
        else { return XCTFail("the link's words name another address than it goes to: a warning asks first") }
        XCTAssertTrue(warning.contains("different domain"), warning)
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(frame.links.count, 1, "once")
        XCTAssertEqual(frame.view.url, try XCTUnwrap(ReaderModel.frameURL(Self.copy)), "the frame went nowhere")
    }

    /// Every link in the core's document opens a new tab (`<base target="_blank">`), which the sandbox stops before
    /// anything hears of it: one that says so itself opens through the opener too, as does a middle click, and
    /// Return on a link the keyboard reached. A drag that ends elsewhere, or a press on no link, opens nothing.
    func testATargetBlankLinkOpensAsDoTheMiddleButtonAndReturn() async throws {
        let frame = try await frame("<p><a id=\"menu\" href=\"https://guild.example.test/menu\" target=\"_blank\">"
                                    + "The menu</a></p><p id=\"words\">Thursday at seven.</p>"
                                    + "<p><a id=\"rsvp\" href=\"mailto:bo@example.test?subject=Thursday\">Tell Bo</a></p>")
        await loaded(frame)
        let menu = FrameLink(href: "https://guild.example.test/menu", text: "The menu")
        let rsvp = FrameLink(href: "mailto:bo@example.test?subject=Thursday", text: "Tell Bo")

        try click(at: try await middle(of: "menu", in: frame), in: frame)
        await until { frame.links == [menu] }

        try RealMouse.click(at: try await middle(of: "rsvp", in: frame), in: frame.view, button: .middle)
        await until { frame.links == [menu, rsvp] }

        _ = try await asked(frame, "document.getElementById(\"menu\").focus(); return null;")
        frame.window.makeFirstResponder(frame.view)
        frame.view.keyDown(with: try XCTUnwrap(NSEvent.keyEvent(
            with: .keyDown, location: .zero, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: frame.window.windowNumber, context: nil, characters: "\r", charactersIgnoringModifiers: "\r",
            isARepeat: false, keyCode: 36)))
        await until { frame.links == [menu, rsvp, menu] }

        try click(at: try await middle(of: "menu", in: frame), in: frame, moved: 60)
        try click(at: try await middle(of: "words", in: frame), in: frame)
        try await Task.sleep(for: .milliseconds(300))
        XCTAssertEqual(frame.links, [menu, rsvp, menu], "a drag, and a press on no link, open nothing")
        XCTAssertEqual(frame.view.url, try XCTUnwrap(ReaderModel.frameURL(Self.copy)), "the frame went nowhere")
    }

    // MARK: The link under the pointer

    /// The pointer over a link says its href, once; off it, nothing; back on it, the href again; and leaving
    /// the frame clears it.
    func testTheLinkUnderThePointerIsSaidAndCleared() async throws {
        let frame = try await frame("<p id=\"words\">See <a id=\"menu\" href=\"https://guild.example.test/menu\">"
                                    + "<b id=\"bold\">the menu</b></a> for Thursday.</p>")
        await loaded(frame)
        let menu = URL(string: "https://guild.example.test/menu")
        let onLink = try await middle(of: "bold", in: frame)
        try RealMouse.move(to: onLink, in: frame.view)
        await until { frame.hovers == [menu] }
        try RealMouse.move(to: CGPoint(x: onLink.x + 1, y: onLink.y), in: frame.view)
        try RealMouse.move(to: CGPoint(x: 400, y: 300), in: frame.view)
        await until { frame.hovers == [menu, nil] }
        try RealMouse.move(to: onLink, in: frame.view)
        await until { frame.hovers == [menu, nil, menu] }
        try RealMouse.leave(frame.view)
        await until { frame.hovers == [menu, nil, menu, nil] }
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertEqual(frame.hovers, [menu, nil, menu, nil], "each change once")
    }

    // MARK: The mail's own

    /// Nothing of the mail's own runs: not a `<script>`, not an `onload=` on an image or an SVG, not a `javascript:`
    /// link clicked, which WebKit drops where scripts are off, and which the opener would refuse anyway. A link beside
    /// it still opens. The sanitizer would have taken them all out; the frame's walls hold without it.
    func testNothingOfTheMailsOwnRuns() async throws {
        let frame = try await frame("<script>document.documentElement.dataset.script = \"ran\";</script>"
                                    + "<img alt=\"\" src=\"\(Self.gif)\" onload=\"document.documentElement.dataset.img = 'ran'\">"
                                    + "<svg width=\"10\" height=\"10\" onload=\"document.documentElement.dataset.svg = 'ran'\"></svg>"
                                    + "<p><a id=\"run\" href=\"javascript:document.documentElement.dataset.link = 'ran'\">Run</a></p>"
                                    + "<p><a id=\"menu\" href=\"https://guild.example.test/menu\">The menu</a></p>")
        await loaded(frame)
        try click(at: try await middle(of: "run", in: frame), in: frame)
        try click(at: try await middle(of: "menu", in: frame), in: frame)
        await until { frame.links.contains { $0.href == "https://guild.example.test/menu" } }
        for link in frame.links where link.href != "https://guild.example.test/menu" {
            XCTAssertEqual(LinkOpening.of(href: link.href, text: link.text, senderDomain: nil), .refuse, link.href)
        }
        try await Task.sleep(for: .milliseconds(300))
        let marks = try await asked(frame, "return JSON.stringify(document.documentElement.dataset);") as? String
        XCTAssertEqual(marks, "{}", "nothing of the mail's ran")
    }

    // MARK: Find in Message

    /// The core's fold of a rich mail's quote (`mm_content`'s `FOLD_OPEN`), which its sanitizer writes round the first
    /// quoted reply and all after it.
    private static let coreFold = "<details class=\"mm-quote\"><summary>\u{b7}\u{b7}\u{b7} quoted text</summary>"

    /// A long statement: a heading, forty sections, then a quote the core folded, whose last line is THE LAST LINE.
    private static let statement = "<h1 id=\"heading\">The bank's statement</h1>"
        + (1...40).map { "<p id=\"section\($0)\">Section \($0): the market rules change in spring. Stalls book a week "
            + "ahead, and the hall opens at eight.</p>" }.joined()
        + "<p>On Mon, Bo wrote:</p>" + coreFold
        + (1...20).map { "<p>Quoted line \($0) from the earlier mail about the hall.</p>" }.joined()
        + "<p id=\"last\">THE LAST LINE</p></details>"

    /// Find in a folded quote opens it, as WebKit does: the frame measures again as Find goes, so the match is inside
    /// it and never past its foot, and the reader scrolls so the match sits well inside what it shows.
    func testFindInAFoldedQuoteMeasuresTheFrameAgainAndShowsTheMatch() async throws {
        let frame = try await frame(Self.statement)
        read(frame)
        let folded = try await settled(frame)
        let find = ReaderFind()
        find.add(frame.view)
        find.query = "Quoted line 7"
        await find.next()
        XCTAssertEqual(find.found, true)
        let opened = try await needs(frame)
        XCTAssertGreaterThan(opened, folded + 400, "the match opened the quote")
        XCTAssertEqual(frame.heights.last, opened, "the frame measured it as Find went")
        XCTAssertGreaterThanOrEqual(frame.view.frame.height, opened, "and grew to hold it")
        let selected = try await selection(frame)
        let match = try XCTUnwrap(selected)
        XCTAssertTrue(try wellInside(match, of: frame), "the match well inside the reader, not at its edge: \(match)")
        let inside = try await scrolledInside(frame)
        XCTAssertEqual(inside, 0, "the frame never scrolls itself")
    }

    /// The match at the foot of a quote Find opened: WebKit shows it by scrolling the frame inside itself, before the
    /// frame is as tall as its content. The frame's scroll goes back to its top and the reader shows the match; back
    /// at the reader's top, the body starts at its heading.
    func testFindNeverLeavesTheFrameScrolledInsideItself() async throws {
        let frame = try await frame(Self.statement)
        let reader = read(frame)
        try await settled(frame)
        let find = ReaderFind()
        find.add(frame.view)
        find.query = "THE LAST LINE"
        await find.next()
        XCTAssertEqual(find.found, true)
        try await Task.sleep(for: .milliseconds(300))
        let inside = try await scrolledInside(frame)
        XCTAssertEqual(inside, 0, "the frame never keeps a scroll of its own")
        let selected = try await selection(frame)
        let match = try XCTUnwrap(selected)
        XCTAssertTrue(try wellInside(match, of: frame), "the match well inside the reader: \(match)")

        reader.contentView.scroll(to: .zero)
        reader.reflectScrolledClipView(reader.contentView)
        let heading = try await place(of: "#heading", in: frame)
        XCTAssertTrue((0..<60).contains(heading.minY), "the body starts at its heading: \(heading)")
    }

    // MARK: The keyboard

    /// Once the body has the keyboard (a click in a paragraph), the keys that page and scroll move the reader as they
    /// do from the list: Space a page down and ⇧Space back, Page Down and Up, ↓ and ↑ a line, End and Home. A frame as
    /// tall as its content has nothing of its own to scroll: WebKit gives back every key but Page Up and Down, which it
    /// keeps all the same. The keys go as a person's do (`press`): the function keys offered to WebKit first as key
    /// equivalents, which it claims.
    func testTheKeysThatPageAndScrollMoveTheReaderOnceTheBodyHasTheKeyboard() async throws {
        let frame = try await frame(Self.statement)
        let reader = read(frame)
        try await settled(frame)
        try click(at: try await middle(of: "section1", in: frame), in: frame)
        XCTAssertTrue(frame.window.makeFirstResponder(frame.view), "a click gives the body the keyboard")
        var top: CGFloat { reader.contentView.bounds.minY }
        let shown = reader.contentView.bounds.height
        let page = max(shown - 40, shown / 2)
        let foot = (reader.documentView?.frame.height ?? 0) - shown
        let steps: [(typed: String, code: UInt16, held: NSEvent.ModifierFlags, top: CGFloat, what: String)] = [
            (" ", 49, [], page, "Space pages down"),
            (" ", 49, .shift, 0, "⇧Space back up"),
            (function(NSPageDownFunctionKey), 121, [], page, "Page Down"),
            (function(NSPageUpFunctionKey), 116, [], 0, "Page Up"),
            (function(NSDownArrowFunctionKey), 125, [], ReaderPage.line, "↓, a line"),
            (function(NSUpArrowFunctionKey), 126, [], 0, "↑, a line back"),
            (function(NSEndFunctionKey), 119, [], foot, "End, the foot"),
            (function(NSHomeFunctionKey), 115, [], 0, "Home, the top"),
        ]
        for step in steps {
            try RealKeys.press(step.typed, step.code, held: step.held, in: frame.window)
            try await eventually(step.what) { abs(top - step.top) < 0.5 }
            guard abs(top - step.top) < 0.5 else { return }
        }
        let inside = try await scrolledInside(frame)
        XCTAssertEqual(inside, 0, "the frame itself never scrolled")
        XCTAssertTrue(frame.window.firstResponder === frame.view, "the body keeps the keyboard")
    }

    /// A field in a body keeps the keys typed in it: the core's sanitizer lets none through, but the frame doesn't lean
    /// on it. Space goes into the field as text; ↓ and End move its caret, and End, which WebKit gives back from a
    /// field it can't scroll, still never moves the reader while the focus is in the field. Out of it, the same keys
    /// page again.
    func testAFieldInTheBodyKeepsTheKeysTypedInIt() async throws {
        let frame = try await frame("<p><textarea id=\"note\" rows=\"3\" cols=\"20\">Gate</textarea></p>" + Self.statement)
        let reader = read(frame)
        try await settled(frame)
        try click(at: try await middle(of: "note", in: frame), in: frame)
        XCTAssertTrue(frame.window.makeFirstResponder(frame.view))
        try await eventually("the field has the focus") {
            try await asked(frame, "return document.activeElement.id;") as? String == "note"
        }
        try RealKeys.press(" ", 49, in: frame.window)
        try RealKeys.press(function(NSDownArrowFunctionKey), 125, in: frame.window)
        try RealKeys.press(function(NSEndFunctionKey), 119, in: frame.window)
        try RealKeys.press(function(NSPageDownFunctionKey), 121, in: frame.window)
        try await eventually("Space went into the field") {
            try await asked(frame, "return document.getElementById(\"note\").value;") as? String != "Gate"
        }
        try await Task.sleep(for: .milliseconds(400))
        XCTAssertEqual(reader.contentView.bounds.minY, 0, "the reader stayed while the field had the keys")

        try click(at: try await middle(of: "section1", in: frame), in: frame)
        try await eventually("the field let the focus go") {
            try await asked(frame, "return document.activeElement.id;") as? String != "note"
        }
        try RealKeys.press(" ", 49, in: frame.window)
        try await eventually("Space pages again") { reader.contentView.bounds.minY > 100 }
    }

    /// Tab moves the focus to the quote's toggle at the foot of a long mail: the reader asks the frame where it went
    /// and scrolls to it, as it does to Find's match.
    func testTabShowsWhereTheFocusWentInTheReader() async throws {
        let frame = try await frame(Self.statement)
        let reader = read(frame)
        try await settled(frame)
        try click(at: try await middle(of: "section1", in: frame), in: frame)
        XCTAssertTrue(frame.window.makeFirstResponder(frame.view))
        try RealKeys.press("\t", 48, in: frame.window)
        let toggle = "details.mm-quote > summary"
        try await eventually("the toggle focused and in sight") {
            let focused = try await asked(frame, "return document.activeElement === document.querySelector(\"\(toggle)\");")
            let place = try await place(of: toggle, in: frame)
            return try focused as? Bool == true && wellInside(place, of: frame)
        }
        XCTAssertGreaterThan(reader.contentView.bounds.minY, 1000, "down at the mail's foot")
    }

    // MARK: The quote's fold

    /// The core's fold of a rich mail's quote says only "··· quoted text", open or closed. The look draws what the
    /// plain fold says in its place: Show while it is closed, Hide once it is open. A plain mail's fold says so
    /// itself, and gets nothing drawn beside it.
    func testTheRichQuotesToggleSaysShowOrHide() async throws {
        let frame = try await frame("<p>Thursday, then.</p>" + Self.coreFold + "<p>On Mon, Bo wrote: shall we?</p></details>")
        await loaded(frame)
        let toggle = "document.querySelector(\"details.mm-quote > summary\")"
        func said(_ frame: Shown) async throws -> String? {
            try await asked(frame, "return getComputedStyle(\(toggle), \"::after\").content;") as? String
        }
        var words = try await said(frame)
        XCTAssertEqual(words, "\"··· Show quoted text\"", "closed")
        let own = try await asked(frame, "return getComputedStyle(\(toggle)).fontSize;") as? String
        XCTAssertEqual(own, "0px", "its own words aren't drawn")
        let pill = try await place(of: "details.mm-quote > summary", in: frame)
        XCTAssertGreaterThan(pill.width, 100, "the pill holds the words drawn")
        try click(at: CGPoint(x: pill.midX, y: pill.midY), in: frame)
        try await eventually("the quote opens") { try await asked(frame, "return \(toggle).parentElement.open;") as? Bool == true }
        words = try await said(frame)
        XCTAssertEqual(words, "\"··· Hide quoted text\"", "open")

        let plain = try await self.frame("<pre>Thursday, then.\nOn Mon, Bo wrote:\n&gt; shall we?</pre>", html: false)
        await loaded(plain)
        words = try await said(plain)
        XCTAssertEqual(words, "none", "the plain fold's own words say Show and Hide")
    }

    // MARK: On paper

    /// A printed copy has every `<details>` open, as paper can't unfold one: the core's quote, without its pill, which
    /// only opens it, and the mail's own.
    func testAPrintedCopyHasEveryDetailsOpen() async throws {
        let frame = try await frame("<p>Your tickets are attached.</p><details id=\"own\"><summary>Terms</summary>"
                                    + "<p>No refunds after Friday.</p></details>" + Self.coreFold
                                    + "<p>On Mon, Bo wrote: shall we?</p></details>", printed: true)
        await until { frame.loaded != nil }
        XCTAssertEqual(frame.loaded, true)
        let head = try await place(of: "#printhead", in: frame)
        XCTAssertGreaterThan(head.height, 0, "the printed copy's head")
        let own = try await place(of: "#own", in: frame), terms = try await place(of: "#own > summary", in: frame)
        XCTAssertGreaterThan(own.height, terms.height + 15, "the mail's own details, open on paper")
        let marker = try await asked(frame, "return getComputedStyle(document.querySelector(\"#own > summary\")).listStyleType;")
        XCTAssertEqual(marker as? String, "disclosure-open", "and its marker says so: ▼, not ▶")
        let quote = try await place(of: "details.mm-quote", in: frame)
        let pill = try await place(of: "details.mm-quote > summary", in: frame)
        XCTAssertEqual(pill.height, 0, "no pill on paper")
        XCTAssertGreaterThan(quote.height - pill.height, 15, "the quote, open on paper")
    }
}
