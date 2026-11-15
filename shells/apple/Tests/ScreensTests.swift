import AppKit
import SwiftUI
import WebKit
import XCTest

/// The reader's and the composer's views drawn, light and dark, on fictional mail: each must draw without a fault,
/// and with `TEST_RUNNER_SCREENS=1` given to `xcodebuild test` each is written to build/screens/ to be looked at.
@MainActor
final class ScreensTests: XCTestCase {
    private static let folder = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        .appendingPathComponent("../build/screens").standardized
    private static var writing: Bool { ProcessInfo.processInfo.environment["SCREENS"] != nil }
    private static let key = "8f3a1c5e7b9d2f4a6c8e0b1d3f5a7c9e2b4d6f8a0c1e3b5d7f9a2c4e6b8d0f1a"

    private var home: URL!
    private var open: [Composer] = []
    /// A composer holds its host unowned, as the app's lives as long as the app: the test's live as long as the test.
    private var hosts: [FakeHost] = []

    override func setUp() async throws {
        home = FileManager.default.temporaryDirectory.appending(path: "mailmind-screens-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        if Self.writing { try FileManager.default.createDirectory(at: Self.folder, withIntermediateDirectories: true) }
    }

    override func tearDown() async throws {
        for composer in open { composer.close(force: true) }
        open = []
        hosts = []
        try? FileManager.default.removeItem(at: home)
    }

    // MARK: Drawing

    /// A view with AppKit inside it (a text view, a field), drawn by an offscreen window as the screen would show it.
    private func drawn(_ name: String, size: CGSize, dark: Bool, _ view: some View) async throws {
        // The window draws its own background, which a view's drawing leaves out: the view takes it.
        let host = NSHostingView(rootView: view.frame(width: size.width, height: size.height)
            .background(Color(nsColor: .windowBackgroundColor)))
        let window = NSWindow(contentRect: CGRect(origin: .zero, size: size), styleMask: [.titled, .resizable],
                              backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false   // ARC owns it: closing must not release it a second time
        window.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        window.contentView = host
        try await Task.sleep(for: .milliseconds(400))   // SwiftUI's first passes, and a lookup's answer
        host.layoutSubtreeIfNeeded()
        let rep = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
        host.cacheDisplay(in: host.bounds, to: rep)
        let png = try XCTUnwrap(rep.representation(using: .png, properties: [:]))
        XCTAssertGreaterThan(png.count, 1000, "\(name) drew something")
        try keep(png, as: "\(name)-\(dark ? "dark" : "light")")
        window.contentView = nil
        window.close()
    }

    private func keep(_ png: Data, as name: String) throws {
        guard Self.writing else { return }
        try png.write(to: Self.folder.appendingPathComponent("\(name).png"))
    }

    // MARK: The composer

    private func host() -> (FakeHost, FakeRoutes) {
        let routes = FakeRoutes()
        routes.answer("/api/contacts/known", #"""
            {"ok": true, "thin": false, "bad": [],
             "items": {"bo@example.test": {"known": true, "self": false},
                       "dee@example.test": {"known": false, "self": false}}}
            """#)
        let host = FakeHost(routes: routes)
        host.senders = ["ada@example.test", "ada@guild.example.test"]
        host.signatures = ["ada@example.test": "Ada"]
        host.snippets = [API.Snippet(text: "Best, Ada", trigger: "sig")]
        hosts.append(host)
        return (host, routes)
    }

    func testTheComposerReplying() async throws {
        let (host, _) = host()
        var seed = ComposeSeed()
        seed.mode = .replyall
        seed.from = "ada@example.test"
        seed.to = ["Bo Brown <bo@example.test>"]
        seed.cc = ["dee@example.test"]
        seed.subject = "Re: The guild dinner"
        seed.origKey = 7
        seed.quote = Quote(html: nil, text: "On Mon, Bo wrote:\n> Shall we book for Thursday?", from: "bo@example.test",
                           date: "Mon")
        for dark in [false, true] {
            let composer = Composer(seed, host: host)
            open.append(composer)
            composer.setBody("Thursday works for me — I’ll bring the menu.\n\n-- \nAda",
                             selection: NSRange(location: 44, length: 0))
            composer.add([.data(name: "menu.pdf", type: "application/pdf", Data(repeating: 7, count: 120_000))])
            try await drawn("composer-reply", size: CGSize(width: 720, height: 600), dark: dark,
                            ComposerForm(composer: composer, host: host, aliases: ["ada@guild.example.test": "Guild"]) { _ in })
            composer.close(force: true)
        }
    }

    func testTheComposerHeldBySendsChecks() async throws {
        let (host, _) = host()
        var seed = ComposeSeed()
        seed.to = ["bo@example.test"]
        for dark in [false, true] {
            let composer = Composer(seed, host: host)
            open.append(composer)
            composer.commitChip(.to, "dee@example.test")
            composer.setBody("Here’s the plan, attached.", selection: NSRange(location: 26, length: 0))
            try await Task.sleep(for: .milliseconds(300))   // the lookup marks Dee as new
            composer.send()
            XCTAssertEqual(composer.check?.lines.map(\.key), ["subject", "attach", "new"])
            try await drawn("composer-guard", size: CGSize(width: 720, height: 560), dark: dark,
                            ComposerForm(composer: composer, host: host, aliases: [:]) { _ in })
            composer.close(force: true)
        }
    }

    func testSendLaterSnippetsAndDraftOptions() async throws {
        let (host, _) = host()
        let composer = Composer(ComposeSeed(), host: host)
        open.append(composer)
        for dark in [false, true] {
            try await drawn("later", size: CGSize(width: 320, height: 210), dark: dark, LaterChoices(composer: composer))
            try await drawn("draft-options", size: CGSize(width: 320, height: 200), dark: dark,
                            DraftOptions(tone: .warm, chooseTone: { _ in }, go: { _ in }))
            try await drawn("snippets", size: CGSize(width: 340, height: 300), dark: dark,
                            SnippetsPanel(composer: composer, host: host) {})
        }
    }

    // MARK: The reader

    func testTheReader() async throws {
        let core = try await Core.open(CoreHome(folder: home.path(percentEncoded: false), variables: ["MAILMIND_DB_KEY": Self.key],
                                                standIn: home.appending(path: "keychain.json").path(percentEncoded: false)))
        let copy = MailCopy(API.FolderRow(
            account: "ada@example.test", action: "", commitmentWorthy: false, flagged: false, folder: "INBOX",
            fromAddr: "bo@bank.example.test", fromName: "Bo Brown", hasAttach: true, isJunk: false, key: 7, needsYou: false,
            replyWorthy: true, safeToFile: false, seen: true, snippet: "", subject: "The guild dinner", summary: "",
            ts: 1_793_620_800, uid: 7, when: ""))
        let message = API.MessageOpened(
            account: "ada@example.test",
            attachments: [API.AttachmentPart(contentType: "application/pdf", filename: "menu.pdf", isInline: 0, partId: "2",
                                             size: 120_000, travels: true),
                          API.AttachmentPart(contentType: "image/jpeg", filename: "hall.jpg", isInline: 0, partId: "3",
                                             size: 2_400_000, travels: true)],
            blockedCssUrls: 0, blockedImages: 3, cc: "", date: "Mon, 2 Nov 2026 12:00", folder: "INBOX",
            fromAddr: "bo@bank.example.test", fromName: "Bo Brown", hasHtml: true, key: 7, messageId: "<m7@example.test>",
            ok: true, replyTo: "desk@elsewhere.example.test", sender: API.SenderFacts(count: 0, replied: 0),
            subject: "The guild dinner", to: "ada@example.test", uid: 7)
        for dark in [false, true] {
            try await drawn("reader", size: CGSize(width: 760, height: 560), dark: dark,
                            MessageReader(core: core, copy: copy, opened: message, row: nil, prefs: ReadingPrefs(nil),
                                          mine: SendGuard.mineSet(["ada@example.test"])))
        }
        await core.close()
    }

    func testTheReadingSettings() async throws {
        for dark in [false, true] {
            try await drawn("reading-settings", size: CGSize(width: 380, height: 300), dark: dark, ReadingSettings())
        }
    }

    func testTheOutboxTray() async throws {
        let routes = FakeRoutes()
        routes.answer("/api/outbox", #"""
            {"ok": true, "items": [
              {"id": 1, "account": "ada@example.test", "to": ["bo@example.test"], "cc": [], "bcc_count": 0,
               "subject": "The guild dinner", "state": "queued", "attempts": 0, "next_attempt_at": 1900000000,
               "scheduled": true, "retrying": false, "unsure": false, "created_at": "2026-11-02 12:00:00"},
              {"id": 2, "account": "ada@example.test", "to": ["dee@example.test"], "cc": [], "bcc_count": 1,
               "subject": "Minutes", "state": "error", "error": "The server said no", "attempts": 3, "next_attempt_at": 0,
               "scheduled": false, "retrying": false, "unsure": false, "created_at": "2026-11-02 12:00:00"}],
             "drafts": []}
            """#)
        routes.answer("/api/pending", #"{"ok": true, "items": [], "waiting": 0, "stuck": 0}"#)
        let tray = OutboxTray(routes: routes) { _ in }
        await tray.load()
        XCTAssertEqual(tray.items.count, 2, "the outbox read")
        XCTAssertEqual(tray.failed, 1)
        for dark in [false, true] {
            try await drawn("outbox", size: CGSize(width: 420, height: 220), dark: dark, OutboxList(tray: tray, readOnly: false))
        }
    }

    // MARK: The frame

    /// A body as the frame shows it: the core's document with the look and the quote folded, in a web view with the
    /// frame's walls. Drawn by WebKit's own snapshot, as nothing else can draw a web view.
    private func frame(_ name: String, document: String, html: Bool, dark: Bool, colors: API.MailColors = .auto) async throws {
        let look = MailLook.of(html: html, prefs: .init(colors: colors), darkAppearance: dark,
                               dark: ReaderPalette.of(dark: true), light: ReaderPalette.of(dark: false))
        let answer = CoreAnswer(status: 200, contentType: "text/html; charset=utf-8", headers: [], body: Data(document.utf8))
        let dressed = String(decoding: FrameGuard.dressed(answer, look: look, html: html), as: UTF8.self)
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        let view = WKWebView(frame: CGRect(x: 0, y: 0, width: 640, height: 360), configuration: configuration)
        let loaded = Loaded()
        view.navigationDelegate = loaded
        view.loadHTMLString(dressed, baseURL: nil)
        await loaded.wait()
        let shot = try await view.takeSnapshot(configuration: nil)
        let png = try XCTUnwrap(NSBitmapImageRep(data: XCTUnwrap(shot.tiffRepresentation))?
            .representation(using: .png, properties: [:]))
        try keep(png, as: "\(name)-\(dark ? "dark" : "light")")
    }

    func testTheFramesLook() async throws {
        let plain = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><base target=\"_blank\"></head><body><pre>"
            + PlainQuote.escaped("Hi Ada,\n\nThursday at seven, then. I’ll book the long table.\n\nBo\n\nOn Mon, Ada wrote:\n> Shall we book for Thursday?\n> The menu is attached.")
            + "</pre></body></html>"
        let rich = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><base target=\"_blank\"></head><body>"
            + "<div style=\"background:#fff8e7;padding:16px\"><h2 style=\"color:#7a3e00\">The guild dinner</h2>"
            + "<p>Thursday at <b>seven</b>. <a href=\"https://guild.example.test/menu\">See the menu</a>.</p></div></body></html>"
        for dark in [false, true] {
            try await frame("frame-plain", document: plain, html: false, dark: dark)
            try await frame("frame-rich", document: rich, html: true, dark: dark, colors: dark ? .dark : .auto)
        }
    }
}

/// Says when a web view's document has loaded.
@MainActor
private final class Loaded: NSObject, WKNavigationDelegate {
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
