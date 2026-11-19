import AppKit
import PDFKit
import SwiftUI
import WebKit
import XCTest

/// The reading pane's document (#44): where it stands in a pane of each width, the measure, the reading face in the
/// frame and what it refuses, and what the document's parts say.
@MainActor
final class ReaderDocumentTests: XCTestCase {
    // MARK: The layout

    func testTheAsideStandsBesideTheMessageFrom1100() {
        XCTAssertFalse(PaneLayout(width: 1099, measure: 680).twoColumns)
        XCTAssertTrue(PaneLayout(width: 1100, measure: 680).twoColumns)
        XCTAssertTrue(PaneLayout(width: 700, measure: 680).tight)
        XCTAssertFalse(PaneLayout(width: 701, measure: 680).tight)
    }

    func testTheDocumentsMarginsAndColumns() {
        let one = PaneLayout(width: 970, measure: 680)
        XCTAssertEqual(one.padding, EdgeInsets(top: 26, leading: 48, bottom: 96, trailing: 48))
        XCTAssertEqual(one.widest, 776, "the measure and its margins")
        XCTAssertEqual(one.content, 680, "the measure, in a pane wider than it")
        XCTAssertEqual(one.main, 680)

        let narrow = PaneLayout(width: 600, measure: 680)
        XCTAssertEqual(narrow.padding, EdgeInsets(top: 18, leading: 20, bottom: 80, trailing: 20))
        XCTAssertEqual(narrow.content, 560, "a pane narrower than the measure: all of it but the margins")

        let two = PaneLayout(width: 1400, measure: 680)
        XCTAssertEqual(two.widest, 680 + 300 + 44 + 96)
        XCTAssertEqual(two.content, 1024)
        XCTAssertEqual(two.main, 680, "the message keeps the measure, the aside beside it")

        let tightTwo = PaneLayout(width: 1150, measure: 850)
        XCTAssertEqual(tightTwo.content, 1054)
        XCTAssertEqual(tightTwo.main, 710, "where the pane can't hold both, the message gives way")
        XCTAssertEqual(PaneLayout(width: 0, measure: 680).content, 0)
    }

    func testTheAsideIsHeldBelowTheTopButNeverPastItsRow() {
        // Not scrolled to it yet: it stays where it stands.
        XCTAssertEqual(PaneLayout.stuck(scrolled: 0, rowTop: 200, rowHeight: 2000, asideHeight: 500), 0)
        // Scrolled past its top: held 18 below the pane's top.
        XCTAssertEqual(PaneLayout.stuck(scrolled: 400, rowTop: 200, rowHeight: 2000, asideHeight: 500), 218)
        // Never past the foot of the row.
        XCTAssertEqual(PaneLayout.stuck(scrolled: 5000, rowTop: 200, rowHeight: 2000, asideHeight: 500), 1500)
        // Taller than the row: it never moves.
        XCTAssertEqual(PaneLayout.stuck(scrolled: 400, rowTop: 200, rowHeight: 300, asideHeight: 500), 0)
    }

    func testTheMeasureIsTheTextsSizeTimesItsWidths() {
        let sizes: [API.ReadSize] = [.s, .m, .l, .xl]
        XCTAssertEqual(sizes.map(MailLook.size), [15, 17, 19, 21])
        XCTAssertEqual(sizes.map { MailLook.measure(.narrow, size: $0) }, [495, 561, 627, 693])
        XCTAssertEqual(sizes.map { MailLook.measure(.medium, size: $0) }, [600, 680, 760, 840])
        XCTAssertEqual(sizes.map { MailLook.measure(.wide, size: $0) }, [750, 850, 950, 1050])
        var prefs = ReadingPrefs()
        XCTAssertEqual(prefs.measure, 680, "the mockup's --measure, with nothing said")
        prefs.width = .wide
        prefs.look.size = .l
        XCTAssertEqual(prefs.measure, 950)
    }

    // MARK: The reading face in the frame

    func testTheFontRuleIsTheFilesBytesAsADataURLAndNothingElse() throws {
        let face = FrameFont.face
        XCTAssertTrue(face.hasPrefix("@font-face{font-family:\"MailMind Read\";src:url(data:font/ttf;base64,"))
        XCTAssertEqual(face.components(separatedBy: "url(").count, 2, "one source, and it is the data: URL")
        XCTAssertFalse(face.contains("http"))
        XCTAssertFalse(face.contains("mailmind-frame:"))
        XCTAssertGreaterThan(face.count, 100_000, "the whole file")
    }

    func testOnlyATrueTypeFilesBytesBecomeARule() {
        XCTAssertEqual(FrameFont.rule(Data()), "")
        XCTAssertEqual(FrameFont.rule(Data("<style>body{}</style> and more".utf8)), "", "not a font")
        XCTAssertEqual(FrameFont.rule(Data([0x77, 0x4F, 0x46, 0x32] + Array(repeating: 0, count: 40))), "",
                       "a WOFF2 file isn't the TrueType the rule says")
        XCTAssertEqual(FrameFont.rule(Data([0, 1, 0, 0])), "", "too short to be one")
        let tiny = Data([0, 1, 0, 0] + Array(repeating: 0x41, count: 20))
        XCTAssertTrue(FrameFont.rule(tiny).contains("src:url(data:font/ttf;base64,\(tiny.base64EncodedString()))"))
        XCTAssertFalse(FrameFont.rule(Data(Array("true".utf8) + Array(repeating: 0, count: 20))).isEmpty)
    }

    func testTheFrameSetsTheMailInTheReadingFaceUnderItsPolicy() async throws {
        let look = MailLook.of(html: false, prefs: .init(), darkAppearance: false, dark: ReaderPalette.of(dark: true),
                               light: ReaderPalette.of(dark: false))
        let policy = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; base-uri 'none';"
            + " form-action 'none'"
        let document = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta http-equiv="
            + "\"Content-Security-Policy\" content=\"\(policy)\"></head><body><pre>Hi Ada,\n\nThursday, then.</pre>"
            + "</body></html>"
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        let view = WKWebView(frame: CGRect(x: 0, y: 0, width: 480, height: 200), configuration: configuration)
        let loaded = FrameLoaded()
        view.navigationDelegate = loaded
        view.loadHTMLString(look.dress(document), baseURL: nil)
        await loaded.wait()
        // The app's own question, in a world of its own: the mail's runs no script.
        // WebKit names a family with its quotes or without, by version, so they are dropped.
        let faces = try await view.callAsyncJavaScript(
            "await document.fonts.ready; return [...document.fonts]"
                + ".map(f => f.family.replaceAll('\"', '') + ':' + f.status).join(',');",
            contentWorld: .defaultClient) as? String
        XCTAssertEqual(faces, "MailMind Read:loaded", "the reading face loaded from its data: URL")
    }

    func testNightsPlainTextNeverSitsOnTheFramesWhite() async throws {
        let night = ReaderPalette.of(MeadowPalette.night)
        let look = ReaderPalette.look(html: false, prefs: .init(), palette: .night, dark: true)
        XCTAssertFalse(look.sheet, "on the pane's paper")
        let document = "<!doctype html><html><head></head><body><pre>Thursday, then.</pre></body></html>"
        // The frame's own clear switch: on this macOS it is there, and it takes.
        let frame = FrameWebView(frame: CGRect(x: 0, y: 0, width: 200, height: 100),
                                 configuration: WKWebViewConfiguration())
        XCTAssertTrue(frame.clearBackground(), "WebKit still has its private switch: the paper's grain shows")
        if frame.responds(to: NSSelectorFromString("_drawsBackground")) {
            XCTAssertEqual(frame.value(forKey: "_drawsBackground") as? Bool, false)
        }
        // Where it couldn't, the document paints the night's paper over the web view's white.
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        let view = WKWebView(frame: CGRect(x: 0, y: 0, width: 200, height: 100), configuration: configuration)
        let background = { (dressed: String) async throws -> String? in
            let loaded = FrameLoaded()
            view.navigationDelegate = loaded
            view.loadHTMLString(dressed, baseURL: nil)
            await loaded.wait()
            return try await view.callAsyncJavaScript(
                "return getComputedStyle(document.documentElement).backgroundColor;",
                contentWorld: .defaultClient) as? String
        }
        let clear = try await background(look.dress(document))
        XCTAssertEqual(clear, "rgba(0, 0, 0, 0)", "a clear frame's document lets the pane's paper show")
        let painted = try await background(look.dress(document, clear: false))
        XCTAssertEqual(painted, Self.rgb(night.background), "the night's paper, never white")
    }

    /// The frame's own script says the href of the link the pointer comes onto, and an empty one as it leaves for
    /// what is no link or leaves the frame, once each; nothing is clicked, so nothing opens.
    func testTheFramesScriptSaysTheLinkUnderThePointer() async throws {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        let heard = FrameHeard()
        FrameScript.install(in: configuration.userContentController, handler: heard)
        defer { FrameScript.remove(from: configuration.userContentController) }
        let view = WKWebView(frame: CGRect(x: 0, y: 0, width: 480, height: 200), configuration: configuration)
        let loaded = FrameLoaded()
        view.navigationDelegate = loaded
        view.loadHTMLString("<!doctype html><html><body><p id=\"p\">Hi <a id=\"a\" href=\"https://a.example.test/x\">"
                            + "<b id=\"b\">the plan</b></a></p></body></html>", baseURL: nil)
        await loaded.wait()
        func pointer(_ type: String, on id: String, to: String? = nil) async throws {
            let related = to.map { "document.getElementById('\($0)')" } ?? "null"
            _ = try await view.callAsyncJavaScript(
                "document.getElementById('\(id)').dispatchEvent(new MouseEvent('\(type)', {bubbles: true, relatedTarget: \(related)}));",
                contentWorld: FrameScript.world)
        }
        let link = URL(string: "https://a.example.test/x")
        try await pointer("mouseover", on: "b")
        await until { heard.said.last == .hover(link) }
        try await pointer("mouseover", on: "a")
        try await pointer("mouseover", on: "p")
        await until { heard.said.last == .hover(nil) }
        try await pointer("mouseover", on: "a")
        await until { heard.said.last == .hover(link) }
        try await pointer("mouseout", on: "a")
        await until { heard.said.last == .hover(nil) }
        XCTAssertEqual(heard.said, [.hover(link), .hover(nil), .hover(link), .hover(nil)], "each change once")
    }

    /// The status line says where the link goes while the pointer is over one, and nothing once it isn't.
    func testTheStatusLineSaysWhereTheLinkGoes() {
        let hover = LinkHover()
        func said() -> String {
            let data = NSMutableData()
            ImageRenderer(content: LinkStatus(hover: hover).frame(width: 600, height: 60).meadowLight(.day)).render { size, draw in
                var box = CGRect(origin: .zero, size: size)
                guard let consumer = CGDataConsumer(data: data as CFMutableData),
                      let pdf = CGContext(consumer: consumer, mediaBox: &box, nil) else { return }
                pdf.beginPDFPage(nil)
                draw(pdf)
                pdf.endPDFPage()
                pdf.closePDF()
            }
            return PDFDocument(data: data as Data)?.string ?? ""
        }
        XCTAssertEqual(said(), "")
        hover.url = URL(string: "https://a.example.test/plan")
        XCTAssertEqual(said(), "https://a.example.test/plan")
    }

    /// "#RRGGBB" as a computed style says it.
    private static func rgb(_ hex: String) -> String {
        let value = Int(hex.dropFirst(), radix: 16) ?? 0
        return "rgb(\(value >> 16 & 0xFF), \(value >> 8 & 0xFF), \(value & 0xFF))"
    }

    // MARK: The reading settings

    private func key(_ code: UInt16) -> NSEvent {
        NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0, windowNumber: 0,
                         context: nil, characters: "", charactersIgnoringModifiers: "", isARepeat: false,
                         keyCode: code)!
    }

    func testTheReadingSettingsKeysGoFromSettingToSettingAndChooseInOne() {
        let keys = ReadingKeys()
        var steps: [[Int]] = []
        keys.step = { steps.append([$0, $1]) }
        XCTAssertNil(keys.at, "nothing ringed as it opens")
        XCTAssertTrue(keys.key(key(124)))
        XCTAssertEqual(steps, [[0, 1]], "→ with none ringed chooses in the first, and rings it")
        XCTAssertTrue(keys.ringed(0))
        XCTAssertTrue(keys.key(key(125)))
        XCTAssertTrue(keys.key(key(125)))
        XCTAssertEqual(keys.at, 2)
        XCTAssertTrue(keys.key(key(123)))
        XCTAssertEqual(steps.last, [2, -1])
        XCTAssertTrue(keys.key(key(126)))
        XCTAssertTrue(keys.key(key(126)))
        XCTAssertTrue(keys.key(key(126)))
        XCTAssertEqual(keys.at, ReadingKeys.settings - 1, "↑ from the first goes round to the last")
        XCTAssertFalse(keys.key(key(36)), "Return isn't the settings'")
        XCTAssertFalse(keys.key(key(53)), "nor Esc, which closes the popover")
    }

    func testASettingStepsOnlyWhereThereIsAChoiceThatWay() {
        let sizes: [API.ReadSize] = [.s, .m, .l, .xl]
        XCTAssertEqual(ReadingSettings.next(API.ReadSize.m, in: sizes, by: 1), .l)
        XCTAssertEqual(ReadingSettings.next(API.ReadSize.m, in: sizes, by: -1), .s)
        XCTAssertNil(ReadingSettings.next(API.ReadSize.xl, in: sizes, by: 1), "no larger than the largest")
        XCTAssertNil(ReadingSettings.next(API.ReadSize.s, in: sizes, by: -1))
    }

    // MARK: The chips and the place

    func testTheChipsSayTheAccountTheFolderTheSunAndWhereItLies() {
        let rail = [Fixture.folder("INBOX", role: "inbox"), Fixture.folder("Archive", role: "archive"),
                    Fixture.folder("Clients", role: "custom")]
        func kinds(_ chips: [DocumentChip]) -> [String] { chips.map(\.text) }
        XCTAssertEqual(kinds(DocumentChip.of(account: "Work", accountIndex: 1, folder: "INBOX", rail: rail,
                                             needsYou: true, replied: false)), ["Work", "Needs you"])
        XCTAssertEqual(DocumentChip.of(account: "Work", accountIndex: 1, folder: "INBOX", rail: rail, needsYou: false,
                                       replied: false).first?.kind, .account(1))
        XCTAssertEqual(kinds(DocumentChip.of(account: "Ada", accountIndex: 0, folder: "Clients", rail: rail,
                                             needsYou: false, replied: true)), ["Ada", "Clients", "Replied"])
        XCTAssertEqual(kinds(DocumentChip.of(account: "Ada", accountIndex: 0, folder: "Archive", rail: rail,
                                             needsYou: false, replied: false)), ["Ada", "In Archive"])
        XCTAssertEqual(kinds(DocumentChip.of(account: "Ada", accountIndex: 0, folder: "Unknown/Box", rail: [],
                                             needsYou: false, replied: false)), ["Ada", "Unknown/Box"],
                       "a folder the rail doesn't know is the person's")
        XCTAssertEqual(DocumentChip.role(of: "inbox", rail: []), "inbox")
        XCTAssertEqual(DocumentChip.role(of: "Archive", rail: rail), "archive")
    }

    func testTheReplyBoxStandsWhereTheMockupHasIt() {
        func offers(_ folder: String) -> Bool {
            DocumentPlace.of(app: nil, copy: MailCopy(Fixture.row(1, folder: folder)), needsYou: false,
                             replied: false).offersReply
        }
        XCTAssertTrue(offers("INBOX"))
        XCTAssertTrue(offers("Clients"))
        XCTAssertEqual(DocumentPlace.of(app: nil, copy: MailCopy(Fixture.row(1)), needsYou: false, replied: false).me,
                       "ada", "with no window, the address's own name")
    }

    func testTheReplyBoxStandsNowhereMailIsPutAway() async {
        let app = MailApp()
        app.mailbox.wire = Fixture.server().wire
        await app.mailbox.refresh()
        func place(_ folder: String) -> DocumentPlace {
            DocumentPlace.of(app: app, copy: MailCopy(Fixture.row(1, folder: folder)), needsYou: false, replied: false)
        }
        let folders = ["INBOX", "Sent", "Receipts", "Archive", "Trash", "Junk", "Drafts"]
        XCTAssertEqual(folders.map { place($0).offersReply }, [true, true, true, false, false, false, false])
        XCTAssertEqual(place("Receipts").filed, "Receipts")
        XCTAssertEqual(place("INBOX").me, "Ada", "the account's alias")
        XCTAssertEqual(place("Trash").chips.map(\.text), ["Ada", "In Trash"])
    }

    // MARK: The message's words

    func testToWhom() {
        let mine = SendGuard.mineSet(["ada@example.test"])
        XCTAssertEqual(MessageWords.toWhom("ada@example.test", mine: mine), "to you")
        XCTAssertEqual(MessageWords.toWhom("", mine: mine), "to you")
        XCTAssertEqual(MessageWords.toWhom("Ada <ada@example.test>, bo@example.test", mine: mine), "to you and 1 more")
        XCTAssertEqual(MessageWords.toWhom("Team <team@example.test>", mine: mine), "to Team")
        XCTAssertEqual(MessageWords.toWhom("\"Bo Brown\" <bo@example.test>, cy@example.test", mine: mine),
                       "to Bo Brown and 1 more")
        XCTAssertEqual(MessageWords.toWhom("list@example.test", mine: mine), "to list@example.test")
    }

    /// A draft is yours to send: its head names whom it goes to, one of your own accounts among them, never "you".
    func testADraftsHeadNamesWhomItGoesTo() {
        let mine = SendGuard.mineSet(["ada@example.test", "cy@example.test"])
        XCTAssertEqual(MessageWords.toWhom("cy@example.test", mine: mine), "to you", "mail that came to Cy")
        XCTAssertEqual(MessageWords.toWhom("cy@example.test", mine: mine, draft: true), "to cy@example.test")
        XCTAssertEqual(MessageWords.toWhom("Cy <cy@example.test>, bo@example.test", mine: mine, draft: true),
                       "to Cy and 1 more")
        XCTAssertEqual(MessageWords.toWhom("", mine: mine, draft: true), "", "a draft that names no one yet")
    }

    // MARK: The reply box opened

    /// Opened under a long message, the reply box scrolls into sight whole, Send and all, rather than opening below
    /// the fold with only its first line showing.
    func testAnOpenedReplyComesIntoSightWhole() async throws {
        let host = NSHostingView(rootView: ScrollView {
            VStack(spacing: 0) {
                Color.clear.frame(height: 1500)
                Color.clear.frame(height: 300).background { BringsIntoView() }
                Color.clear.frame(height: 400)
            }
            .frame(width: 400)
        }
        .frame(width: 400, height: 500))
        host.frame = CGRect(x: 0, y: 0, width: 400, height: 500)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        defer { window.contentView = nil }
        var probe: BringsIntoView.Probe?
        for _ in 0..<100 where probe == nil {
            host.layoutSubtreeIfNeeded()
            probe = Self.find(BringsIntoView.Probe.self, in: host)
            try await Task.sleep(for: .milliseconds(10))
        }
        let box = try XCTUnwrap(probe, "the box's probe")
        let scroll = try XCTUnwrap(box.enclosingScrollView, "the reader's document scrolls")
        func inSight() -> Bool {
            scroll.contentView.bounds.insetBy(dx: -1, dy: -1).contains(box.convert(box.bounds, to: scroll.contentView))
        }
        for _ in 0..<100 where !inSight() {
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertTrue(inSight(), "the box, 1500 points down, is in sight whole")
    }

    /// The box's rewrite row keeps its mark to one line however narrow the box, its rewrites wrapping under it as the
    /// mockup's row wraps: never "REWRIT" over "E".
    func testTheRewriteRowKeepsItsMarkToOneLine() async throws {
        MeadowVoiceOverTests.assistive(true)
        defer { MeadowVoiceOverTests.assistive(false) }
        let fake = FakeHost()
        defer { withExtendedLifetime(fake) {} }
        let composer = Composer(ComposeSeed(), host: fake)
        defer { composer.close(force: true) }
        func mark(width: CGFloat) async throws -> (mark: CGSize, row: CGFloat) {
            let row = ReplyRewrites(composer: composer).frame(width: width).meadowLight(.day)
            let host = NSHostingView(rootView: row)
            let size = CGSize(width: width, height: host.fittingSize.height)
            host.frame = CGRect(origin: .zero, size: size)
            let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.contentView = host
            defer {
                window.contentView = nil
                window.close()
            }
            window.layoutIfNeeded()
            try await Task.sleep(for: .milliseconds(300))
            let read = MeadowVoiceOverTests.read(host)
            let found = try XCTUnwrap(read.first { $0.label == "Rewrite" }, MeadowVoiceOverTests.said(read))
            return (found.size, size.height)
        }
        let wide = try await mark(width: 760)
        let narrow = try await mark(width: 300)
        XCTAssertEqual(narrow.mark.height, wide.mark.height, accuracy: 0.5, "the mark on one line")
        XCTAssertEqual(narrow.mark.width, wide.mark.width, accuracy: 0.5, "and as wide as it needs")
        XCTAssertGreaterThan(narrow.row, wide.row + 10, "the rewrites wrap under it")
    }

    /// A box taller than what shows brings its foot into sight, where Send is.
    func testATallBoxBringsItsFootIntoSight() {
        let box = NSRect(x: 0, y: 0, width: 400, height: 600)
        XCTAssertEqual(BringsIntoView.Probe.sight(of: box, showing: 800, flipped: true), box)
        XCTAssertEqual(BringsIntoView.Probe.sight(of: box, showing: nil, flipped: true), box)
        XCTAssertEqual(BringsIntoView.Probe.sight(of: box, showing: 400, flipped: true),
                       NSRect(x: 0, y: 200, width: 400, height: 400))
        XCTAssertEqual(BringsIntoView.Probe.sight(of: box, showing: 400, flipped: false),
                       NSRect(x: 0, y: 0, width: 400, height: 400))
    }

    private static func find<V: NSView>(_ kind: V.Type, in view: NSView) -> V? {
        if let found = view as? V { return found }
        for child in view.subviews {
            if let found = find(kind, in: child) { return found }
        }
        return nil
    }

    func testWhereAMailIsFrom() {
        XCTAssertEqual(MessageWords.from("ren@Kumo.example.test", facts: API.SenderFacts(count: 41, firstAgo: "8mo",
                                                                                          replied: 12)),
                       .known(domain: "kumo.example.test", relation: "41 messages, the first 8mo ago"))
        XCTAssertEqual(MessageWords.from("ren@kumo.example.test", facts: API.SenderFacts(count: 3, replied: 0)),
                       .known(domain: "kumo.example.test", relation: "3 messages"))
        XCTAssertEqual(MessageWords.from("new@clinic.example.test", facts: API.SenderFacts(count: 1, replied: 0)),
                       .external(domain: "clinic.example.test"), "the first from them")
        XCTAssertEqual(MessageWords.from("new@clinic.example.test", facts: nil),
                       .external(domain: "clinic.example.test"))
        XCTAssertEqual(MessageWords.from(nil, facts: nil), .unknown)
        XCTAssertEqual(MessageWords.from("no-at-sign", facts: nil), .unknown)
        XCTAssertEqual(MessageWords.sandbox(blocked: 0), "Sandboxed")
        XCTAssertEqual(MessageWords.sandbox(blocked: 1), "Sandboxed · 1 tracker blocked")
        XCTAssertEqual(MessageWords.sandbox(blocked: 4), "Sandboxed · 4 trackers blocked")
    }

    func testAFilesBadge() {
        XCTAssertEqual(FileKind.label(filename: "contract-v3.pdf", contentType: "application/pdf"), "PDF")
        XCTAssertEqual(FileKind.label(filename: "plan.docx", contentType: ""), "DOCX")
        XCTAssertEqual(FileKind.label(filename: "../../evil/photo.JPEG", contentType: "image/jpeg"), "JPEG")
        XCTAssertEqual(FileKind.label(filename: "archive.backup", contentType: "application/octet-stream"), "FILE",
                       "too long an ending to say")
        XCTAssertEqual(FileKind.label(filename: "scan", contentType: "image/png"), "IMG")
        XCTAssertEqual(FileKind.label(filename: ".profile", contentType: "text/plain"), "TXT")
        XCTAssertEqual(FileKind.label(filename: "notes.t-x", contentType: "application/pdf"), "PDF")
    }

    func testTheSummarysFootNamesTheSendersFirstName() {
        XCTAssertEqual(SummaryCard.foot("Ren Takeda"), "Not Ren’s words — the message is the source.")
        XCTAssertEqual(SummaryCard.foot("ren@example.test"), "Not the sender’s words — the message is the source.")
        XCTAssertEqual(SummaryCard.foot(nil), "Not the sender’s words — the message is the source.")
        XCTAssertEqual(SummaryCard.foot("  "), "Not the sender’s words — the message is the source.")
    }

    func testTheInvitesLeafAndWhoIsComing() {
        let english = Locale(identifier: "en_US")
        XCTAssertEqual(InviteCard.calendarDay("2026-11-06T09:30:00+09:00", locale: english)?.month, "NOV")
        XCTAssertEqual(InviteCard.calendarDay("2026-11-06T09:30:00+09:00", locale: english)?.day, "6")
        XCTAssertEqual(InviteCard.calendarDay("2026-01-31T00:00:00", locale: english)?.month, "JAN")
        XCTAssertNil(InviteCard.calendarDay("", locale: english))
        XCTAssertNil(InviteCard.calendarDay("2026-13-01T00:00:00", locale: english))
        let organizer = API.Party(email: "sato@example.test", name: "Dr. Emi Sato")
        XCTAssertEqual(InviteCard.people(organizer: organizer, attendees: []), "From Dr. Emi Sato")
        XCTAssertEqual(InviteCard.people(organizer: API.Party(), attendees: []), nil)
    }

    func testTheAsidesFacts() {
        var receipt = Fixture.row(5, subject: "Your order is on its way")
        receipt.snippet = "Order #4521 · Total $48.00 · arriving Thursday"
        XCTAssertNil(AsideWords.receipt(receipt, lane: nil), "only a receipt has a paper trail")
        receipt.bucket = "receipts/transactional"
        XCTAssertEqual(AsideWords.receipt(receipt, lane: nil), Ledger.money("$", 48))
        XCTAssertNil(AsideWords.receipt(Fixture.row(6), lane: .papertrail), "no amount, no card")
        XCTAssertNil(AsideWords.relation(nil))
        XCTAssertNil(AsideWords.relation(API.SenderFacts(count: 0, replied: 0)))
        XCTAssertEqual(AsideWords.relation(API.SenderFacts(count: 1, replied: 0)),
                       "This is the first message from them.")
        XCTAssertEqual(AsideWords.relation(API.SenderFacts(count: 41, firstAgo: "8mo", replied: 12)),
                       "41 messages, the first 8mo ago. You replied 12 times.")
        XCTAssertEqual(AsideWords.relation(API.SenderFacts(count: 2, replied: 1)), "2 messages. You replied once.")
    }

    func testUpNextAndTheEmptyPane() {
        XCTAssertEqual(UpNextPlace(position: 3, total: 13).nextWords, "5 of 13")
        XCTAssertFalse(UpNextPlace(position: 3, total: 13).isLast)
        XCTAssertTrue(UpNextPlace(position: 12, total: 13).isLast)
        XCTAssertEqual(UpNextPlace.end(title: "Inbox", tab: .needs), "End of Inbox · Needs you")
        XCTAssertEqual(UpNextPlace.end(title: "Archive", tab: nil), "End of Archive")
        XCTAssertEqual(PaneEmptyWords.of(needsDone: true, hasMail: false).title, "Nothing needs you.")
        XCTAssertTrue(PaneEmptyWords.of(needsDone: true, hasMail: false).everything)
        XCTAssertEqual(PaneEmptyWords.of(needsDone: false, hasMail: true).line, "Or press J to start at the top.")
        XCTAssertEqual(PaneEmptyWords.of(needsDone: false, hasMail: false).title, "Nothing here.")
    }

    /// Up next says what the AI made of the next message only while AI is on, as the reader does; turned off, it says
    /// the message's own first words.
    func testUpNextShowsNoSummaryWhileAIIsTurnedOff() {
        let summed = Fixture.row(4, summary: "Asks to move Thursday’s review.")
        XCTAssertEqual(UpNextPlace.gist(of: summed, aiTurnedOff: false), "Asks to move Thursday’s review.")
        XCTAssertEqual(UpNextPlace.gist(of: summed, aiTurnedOff: true), "A line of fictional mail, number 4.")
        XCTAssertEqual(UpNextPlace.gist(of: Fixture.row(5), aiTurnedOff: false), "A line of fictional mail, number 5.")
    }

    /// Your own mail, as the reader shows it in Sent (b25, b27): the reply box, About and the trust line name the
    /// people it went to, not you; mail to you names who wrote it, as before.
    func testYourOwnMailIsNamedByWhomItWentTo() {
        let mine = SendGuard.mineSet(["ada@example.test"])
        let sent = opened(from: "ada@example.test", to: "Ada <ada@example.test>, Lin Mori <lin@guild.example.test>")
        let lin = MessageWords.OtherSide(name: "Lin Mori", address: "lin@guild.example.test", wentTo: true)
        XCTAssertTrue(MessageWords.isYours(sent, mine: mine))
        XCTAssertEqual(MessageWords.otherSide(sent, mine: mine), lin)
        XCTAssertEqual(ReplyBoxWords(answering: sent, mine: mine, following: true).placeholder, "Follow up with Lin…")
        XCTAssertEqual(MessageContent.from(sent, mine: mine), .external(domain: "guild.example.test"))
        var known = sent
        known.sender = API.SenderFacts(count: 41, firstAgo: "8mo", replied: 12)
        XCTAssertEqual(MessageContent.from(known, mine: mine), .external(domain: "guild.example.test"),
                       "what the server knows of you says nothing of Lin")
        let about = AsideWords.about(known, mine: mine)
        XCTAssertEqual(about?.name, "Lin Mori")
        XCTAssertEqual(about?.address, "lin@guild.example.test")
        XCTAssertNil(about?.relation, "no facts of yours on Lin's card")

        var draft = opened(from: "", to: "cy@example.test")
        draft.isDraft = true
        XCTAssertEqual(MessageWords.otherSide(draft, mine: mine),
                       MessageWords.OtherSide(name: "cy@example.test", address: "cy@example.test", wentTo: true))

        var toSelf = opened(from: "ada@example.test", to: "ada+notes@example.test")
        toSelf.sender = API.SenderFacts(count: 3, replied: 0)
        XCTAssertFalse(MessageWords.otherSide(toSelf, mine: mine).wentTo)
        XCTAssertNil(AsideWords.about(toSelf, mine: mine), "no card about yourself")

        var theirs = opened()
        theirs.sender = API.SenderFacts(count: 1, replied: 0)
        XCTAssertEqual(MessageWords.otherSide(theirs, mine: mine),
                       MessageWords.OtherSide(name: "Bo", address: "bo@example.test", wentTo: false))
        XCTAssertEqual(ReplyBoxWords(answering: theirs, mine: mine, following: false).placeholder, "Reply to Bo…")
        XCTAssertEqual(AsideWords.about(theirs, mine: mine)?.relation, "This is the first message from them.")
        XCTAssertEqual(MessageContent.from(theirs, mine: mine), .external(domain: "example.test"))
    }

    /// A conversation's box answers the member the server names; where that is yours, the latest message as it
    /// opened names whom it went to.
    func testAConversationOfYoursIsNamedByWhomItWentTo() {
        let mine = SendGuard.mineSet(["ada@example.test"])
        let member = { (from: String, key: Int) in
            API.ThreadMessage(answered: false, flagged: false, folder: "Sent", fromAddr: from, fromName: "Ada Park",
                              hasAttach: false, isJunk: false, key: key, seen: true, sentTo: [], sentToMore: 0,
                              snippet: "", subject: "Lunch",
                              uid: key, when: "")
        }
        let latest = opened(from: "ada@example.test", to: "Lin Mori <lin@example.test>", key: 9)
        XCTAssertEqual(ReplyBoxWords(answering: member("ada@example.test", 9), opened: latest, mine: mine,
                                     following: true).placeholder, "Follow up with Lin…")
        XCTAssertEqual(ReplyBoxWords(answering: member("ada@example.test", 9), opened: nil, mine: mine,
                                     following: true).placeholder, "Follow up with Ada…", "until it has opened")
        XCTAssertEqual(ReplyBoxWords(answering: member("bo@example.test", 8), opened: latest, mine: mine,
                                     following: false).name, "Ada Park", "another member is named as it is")
    }

    func testTheReplyBoxsWords() {
        let ren = ReplyBoxWords(name: "Ren Takeda", address: "ren@example.test", following: false)
        XCTAssertEqual(ren.placeholder, "Reply to Ren…")
        XCTAssertEqual(ReplyBoxWords(name: "Ren Takeda", address: nil, following: true).placeholder,
                       "Follow up with Ren…")
        XCTAssertEqual(ReplyBoxWords(name: "ren@example.test", address: "ren@example.test", following: false)
            .placeholder, "Reply to ren@example.test…")
        XCTAssertEqual(ReplyBoxWords.drafted(messages: 3),
                       "Drafted by MailMind from the 3-message thread. "
                           + "Edit freely — nothing is sent until you press Send.")
        XCTAssertEqual(ReplyBoxWords.drafted(messages: 1),
                       "Drafted by MailMind from the message. Edit freely — nothing is sent until you press Send.")
    }

    // MARK: The reply in the box

    private func composing() throws -> (Composing, FakeRoutes) {
        let composing = Composing()
        let routes = FakeRoutes()
        let opened = API.MessageOpened(
            account: Fixture.ada, attachments: [], blockedCssUrls: 0, blockedImages: 0, cc: "", date: "Mon",
            folder: "INBOX", fromAddr: "bo@example.test", fromName: "Bo Brown", hasHtml: false, key: 7,
            messageId: "<m7@example.test>", ok: true, replyTo: "", subject: "Lunch", text: "Thursday?",
            to: Fixture.ada, uid: 7)
        let body = String(decoding: try JSONEncoder().encode(opened), as: UTF8.self)
        routes.answer(API.Get.MessageBody.path, body)
        composing.use(routes, digest: Fixture.digest())
        return (composing, routes)
    }

    func testAReplyOpensInTheBoxOnlyForTheMessageTheReaderOffersItFor() async throws {
        let (composing, _) = try composing()
        var windows: [UUID] = []
        composing.openWindow = { windows.append($0) }
        let copy = MailCopy(Fixture.row(7))

        let inWindow = try await replied(composing, copy)
        XCTAssertEqual(windows, [inWindow.id], "no box offered: a window")
        XCTAssertNil(composing.inline)
        inWindow.close(force: true)

        composing.inlineSlot = copy
        let boxed = try await replied(composing, copy)
        XCTAssertEqual(windows.count, 1, "in the box, with no window")
        XCTAssertTrue(composing.inline?.composer === boxed)
        XCTAssertNotNil(composing.composers[boxed.id], "one of the composers, as a window's is")
        let again = await composing.reply(copy)
        XCTAssertTrue(again === boxed, "the reply already in the box")

        let all = try await replied(composing, copy, all: true)
        XCTAssertEqual(windows.last, all.id, "Reply All takes a window")
        XCTAssertTrue(composing.inline?.composer === boxed)
        all.close(force: true)

        composing.popOut()
        XCTAssertNil(composing.inline)
        XCTAssertEqual(windows.last, boxed.id, "popped out into a window, the same composer")
        XCTAssertFalse(boxed.closed)
        boxed.close(force: true)
    }

    func testMovingToAnotherMessageClosesTheReplyLeftInTheBox() async throws {
        let (composing, _) = try composing()
        composing.openWindow = { _ in XCTFail("no window") }
        let copy = MailCopy(Fixture.row(7))
        composing.inlineSlot = copy
        let boxed = try await replied(composing, copy)
        composing.inlineSlot = MailCopy(Fixture.row(8))
        XCTAssertTrue(boxed.closed)
        XCTAssertNil(composing.inline)
        XCTAssertNil(composing.composers[boxed.id])

        composing.inlineSlot = copy
        let second = try await replied(composing, copy)
        await second.discard()
        XCTAssertNil(composing.inline, "discarded, the box closes")
    }

    /// Esc in the box, as the page's composer takes it: the reply closes, what was written in it saved as its draft to
    /// take up again; with no reply in the box, the key is someone else's. The text's own Esc asks the box first.
    func testEscClosesTheReplyInTheBoxKeepingWhatWasWritten() async throws {
        let (composing, routes) = try composing()
        routes.answer(API.Post.Draft.path,
                      #"{"ok": true, "draft_id": 41, "version": 1, "message_id": "<d@example.test>", "pushed": true}"#)
        composing.openWindow = { _ in XCTFail("no window") }
        XCTAssertFalse(composing.escapeInBox(), "no reply in the box: Esc goes on")
        let copy = MailCopy(Fixture.row(7))
        composing.inlineSlot = copy
        let boxed = try await replied(composing, copy)
        boxed.setBody("Thursday works.", selection: NSRange(location: 15, length: 0))

        XCTAssertTrue(composing.escapeInBox())
        XCTAssertTrue(boxed.closed)
        XCTAssertNil(composing.inline)
        await eventually("what was written saved as its draft") {
            routes.calls(to: API.Post.Draft.path).contains { $0.body["text"]?.string?.hasPrefix("Thursday works.") == true }
        }
        XCTAssertFalse(composing.escapeInBox(), "closed: the next Esc goes on")

        let text = BodyTextView(frame: .zero)
        var asked = 0
        text.escape = {
            asked += 1
            return true
        }
        text.cancelOperation(nil)
        XCTAssertEqual(asked, 1, "the text's Esc asks the box")
    }

    /// Esc a moment after the autosave kept the words: nothing is left to save, and the close still says where they
    /// went, in the page's words.
    func testEscAfterTheAutosaveSaysWhereTheWordsWent() async throws {
        let (composing, routes) = try composing()
        routes.answer(API.Post.Draft.path,
                      #"{"ok": true, "draft_id": 41, "version": 1, "message_id": "<d@example.test>", "pushed": true}"#)
        composing.openWindow = { _ in XCTFail("no window") }
        let copy = MailCopy(Fixture.row(7))
        composing.inlineSlot = copy
        let boxed = try await replied(composing, copy)
        boxed.setBody("Thursday works.", selection: NSRange(location: 15, length: 0))
        _ = await boxed.save(silent: true, takeTyped: false, autosave: true).value
        XCTAssertFalse(boxed.dirty, "the autosave kept it")

        XCTAssertTrue(composing.escapeInBox())
        await eventually("where the words went said") { composing.notice?.text == "Saved to Drafts" }
        XCTAssertEqual(routes.calls(to: API.Post.Draft.path).count, 1, "nothing new to save")
    }

    /// r on the message whose reply was closed with Esc opens the box with the words saved, the same draft, rather than
    /// an empty box; its saves go on to that draft. A quick reply starts afresh.
    func testRAfterEscOpensTheBoxWithTheWordsSaved() async throws {
        let (composing, routes) = try composing()
        routes.answer(API.Post.Draft.path,
                      #"{"ok": true, "draft_id": 41, "version": 1, "message_id": "<d@example.test>", "pushed": true}"#)
        composing.openWindow = { _ in XCTFail("no window") }
        let copy = MailCopy(Fixture.row(7))
        composing.inlineSlot = copy
        let boxed = try await replied(composing, copy)
        boxed.setBody("Thursday works.", selection: NSRange(location: 15, length: 0))
        XCTAssertTrue(composing.escapeInBox())
        await eventually("closed, saved") { composing.notice?.text == "Saved to Drafts" }
        let reads = routes.calls(to: API.Get.MessageBody.path).count

        let again = try await replied(composing, copy)
        XCTAssertFalse(again === boxed, "a composer of its own")
        XCTAssertTrue(composing.inline?.composer === again, "in the box")
        XCTAssertTrue(again.body.hasPrefix("Thursday works."), "the words as saved")
        XCTAssertEqual(again.draftId, 41, "the same draft")
        XCTAssertEqual(again.to.map(\.value), ["bo@example.test"])
        XCTAssertEqual(routes.calls(to: API.Get.MessageBody.path).count, reads, "nothing asked of the original")

        composing.say("between")
        again.close()
        await eventually("closed untouched, it still says where its words are") {
            composing.notice?.text == "Saved to Drafts"
        }
        let third = try await replied(composing, copy)
        XCTAssertTrue(third.body.hasPrefix("Thursday works."), "and opens with them again")
        third.setBody("Thursday works. Noon?", selection: NSRange(location: 21, length: 0))
        _ = await third.save(silent: true).value
        XCTAssertEqual(routes.calls(to: API.Post.Draft.path).last?.body["draft_id"]?.number, 41, "saved to that draft")
        third.close()
        XCTAssertNil(composing.inline)

        await composing.quickReply(copy, QuickReply.all[0])
        let quick = try XCTUnwrap(composing.inline?.composer)
        XCTAssertTrue(quick.body.hasPrefix("Yes, that works for me."), "a quick reply starts afresh")
        XCTAssertNil(quick.draftId)
        quick.close(force: true)
    }

    /// A reply closed in the box with nothing written in it saves nothing and says nothing; r opens it empty again.
    func testAnUntouchedReplyClosedInTheBoxOpensEmptyAgain() async throws {
        let (composing, routes) = try composing()
        composing.openWindow = { _ in XCTFail("no window") }
        let copy = MailCopy(Fixture.row(7))
        composing.inlineSlot = copy
        _ = try await replied(composing, copy)
        XCTAssertTrue(composing.escapeInBox())
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertNil(composing.notice, "nothing to say")
        XCTAssertTrue(routes.calls(to: API.Post.Draft.path).isEmpty, "nothing saved")
        let again = try await replied(composing, copy)
        XCTAssertNil(again.draftId)
        again.close(force: true)
    }

    func testTheSendGuardsFixesInTheBoxAreDoneWhereTheyCanBe() async throws {
        let (composing, _) = try composing()
        var windows: [UUID] = []
        composing.openWindow = { windows.append($0) }
        let copy = MailCopy(Fixture.row(7))
        composing.inlineSlot = copy

        let boxed = try await replied(composing, copy)
        let attach = GuardFix(kind: "attach", label: "Attach a file")
        composing.fixInBox(attach, field: nil)
        XCTAssertTrue(composing.inline?.composer === boxed, "the box holds files: its own panel opens")
        XCTAssertTrue(boxed.takeFilesRequest(), "the panel asked for")
        XCTAssertFalse(boxed.takeFilesRequest(), "and taken once")
        composing.fixInBox(attach, field: nil)
        XCTAssertTrue(boxed.takeFilesRequest(), "so asking again opens it again")
        composing.fixInBox(GuardFix(kind: "time", label: "Pick a new time"), field: nil)
        XCTAssertTrue(boxed.laterOpen, "Send later is in the box")
        XCTAssertTrue(windows.isEmpty, "nothing popped out for what the box holds")

        composing.fixInBox(GuardFix(kind: "subject", label: "Add a subject"), field: nil)
        XCTAssertNil(composing.inline, "no subject in the box: the reply goes to its window")
        XCTAssertEqual(windows, [boxed.id])
        XCTAssertFalse(boxed.closed, "the same reply, as it stood")
        XCTAssertEqual(boxed.focusRequest, .subject, "where the window puts the keys")
        boxed.close(force: true)

        let second = try await replied(composing, copy)
        second.commitChip(.cc, "cy@")
        composing.fixInBox(GuardFix(kind: "bad", label: "Edit it"), field: .cc)
        XCTAssertEqual(windows.last, second.id, "no recipients to edit in the box")
        XCTAssertEqual(second.typed[.cc], "cy@", "back in its field to fix")
        XCTAssertTrue(second.showCc)
        XCTAssertEqual(second.focusRequest, .field(.cc))
        second.close(force: true)

        let third = try await replied(composing, copy)
        composing.fixInBox(GuardFix(kind: "to", label: "Add one"), field: nil)
        XCTAssertEqual(windows.last, third.id)
        XCTAssertEqual(third.focusRequest, .field(.to))
        third.close(force: true)

        let fourth = try await replied(composing, copy)
        composing.fixInBox(GuardFix(kind: "review", label: "Review recipients"), field: nil)
        XCTAssertEqual(windows.last, fourth.id)
        XCTAssertTrue(fourth.showCc)
        XCTAssertEqual(fourth.focusRequest, .field(.cc))
        fourth.close(force: true)
        XCTAssertEqual(windows.count, 4)
    }

    func testABoxDrawnAgainKeepsItsSlotAndOneGoneGivesItUp() async throws {
        let (composing, _) = try composing()
        let copy = MailCopy(Fixture.row(7))
        composing.offerInline(copy)
        composing.withdrawInline(copy)
        composing.offerInline(copy)
        try await Task.sleep(for: .milliseconds(400))
        XCTAssertEqual(composing.inlineSlot, copy, "offered again at once: kept")
        composing.withdrawInline(copy)
        try await Task.sleep(for: .milliseconds(400))
        XCTAssertNil(composing.inlineSlot, "gone for good")
    }
}

/// A web view's first load, finished.
/// What the frame's script said, as the frame reads it.
@MainActor
private final class FrameHeard: NSObject, WKScriptMessageHandler {
    private(set) var said: [FrameMessage] = []

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.world.name == FrameScript.world.name, let read = FrameMessage(message.body) else { return }
        if case .hover = read { said.append(read) }
    }
}

private final class FrameLoaded: NSObject, WKNavigationDelegate {
    private var done = false
    private var waiting: CheckedContinuation<Void, Never>?

    func wait() async {
        if done { return }
        await withCheckedContinuation { waiting = $0 }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        done = true
        waiting?.resume()
        waiting = nil
    }
}

/// A reply `composing` opened for `copy`, which must open.
@MainActor
func replied(_ composing: Composing, _ copy: MailCopy, all: Bool = false) async throws -> Composer {
    let composer = await composing.reply(copy, all: all)
    return try XCTUnwrap(composer, "the reply opened")
}
