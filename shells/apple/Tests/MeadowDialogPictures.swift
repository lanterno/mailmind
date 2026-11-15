import AppKit
import SwiftUI
import XCTest

/// The compose window drawn in each light on fictional answers: each must draw, and with `TEST_RUNNER_MEADOW_PICTURES=<folder>` given to `xcodebuild test` each is written there, at
/// twice the size, to be laid beside the mockup's pictures. They are drawn by an offscreen window, so the AppKit
/// views inside them (the message's text, a field, a switch) are drawn too.
@MainActor
final class MeadowDialogPictures: XCTestCase {
    private static var folder: URL? {
        ProcessInfo.processInfo.environment["MEADOW_PICTURES"].map { URL(fileURLWithPath: $0) }
    }

    private var open: [Composer] = []
    /// A composer holds its host unowned: the test's live as long as the test.
    private var hosts: [FakeHost] = []

    override func tearDown() async throws {
        for composer in open { composer.close(force: true) }
        open = []
        hosts = []
    }

    /// Draw `view` at `size` in each light, in a window of that light's appearance.
    private func draw<V: View>(_ name: String, _ size: CGSize, _ view: (MeadowLight) -> V) throws {
        for light in MeadowLight.allCases {
            let host = NSHostingView(rootView: view(light)
                .frame(width: size.width, height: size.height)
                .meadowLight(light))
            host.frame = CGRect(origin: .zero, size: size)
            let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.appearance = NSAppearance(named: light.isDark ? .darkAqua : .aqua)
            window.contentView = host
            host.layoutSubtreeIfNeeded()
            RunLoop.main.run(until: Date().addingTimeInterval(0.4))
            host.layoutSubtreeIfNeeded()
            let png = try XCTUnwrap(Self.drawn(host), name)
            XCTAssertGreaterThan(png.count, 4000, "\(name) in \(light) drew something")
            if let folder = Self.folder {
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                try png.write(to: folder.appendingPathComponent("\(name)-\(light.rawValue).png"))
            }
            window.contentView = nil
            window.close()
        }
    }

    /// The view's layers drawn at twice its size, so what AppKit draws in layers of its own is drawn too.
    private static func drawn(_ host: NSView) -> Data? {
        let scale: CGFloat = 2
        guard let layer = host.layer,
              let context = CGContext(data: nil, width: Int(host.bounds.width * scale),
                                      height: Int(host.bounds.height * scale), bitsPerComponent: 8, bytesPerRow: 0,
                                      space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        context.scaleBy(x: scale, y: scale)
        // A layer draws top down; a bitmap's rows go bottom up.
        context.translateBy(x: 0, y: host.bounds.height)
        context.scaleBy(x: 1, y: -1)
        layer.render(in: context)
        return context.makeImage().flatMap {
            NSBitmapImageRep(cgImage: $0).representation(using: .png, properties: [:])
        }
    }

    // MARK: The compose window

    private func host() -> FakeHost {
        let routes = FakeRoutes()
        routes.answer("/api/contacts/known", #"""
            {"ok": true, "thin": false, "bad": [],
             "items": {"bo@example.test": {"known": true, "self": false},
                       "dee@example.test": {"known": false, "self": false}}}
            """#)
        let host = FakeHost(routes: routes)
        host.senders = ["hana@example.test", "home@example.test"]
        hosts.append(host)
        return host
    }

    private static let aliases: [String: String?] = ["hana@example.test": "Work", "home@example.test": "Personal"]

    func testANewMessageAsTheMockupDrawsIt() throws {
        let host = host()
        var seed = ComposeSeed()
        seed.from = "hana@example.test"
        try draw("compose-new", CGSize(width: 760, height: 537)) { _ in
            let composer = Composer(seed, host: host)
            open.append(composer)
            return ComposerForm(composer: composer, host: host, aliases: Self.aliases,
                                accounts: ["hana@example.test", "home@example.test"], lights: false) { _ in }
        }
    }

    func testAReplyWithChipsAFileAndTheQuote() async throws {
        let host = host()
        var seed = ComposeSeed()
        seed.mode = .replyall
        seed.from = "home@example.test"
        seed.to = ["Bo Brown <bo@example.test>"]
        seed.cc = ["dee@example.test", "not an address"]
        seed.subject = "Re: The guild dinner"
        seed.origKey = 7
        seed.quote = Quote(html: nil, text: "On Mon, Bo wrote:\n> Shall we book for Thursday?", from: "bo@example.test",
                           date: "Mon")
        let composer = Composer(seed, host: host)
        open.append(composer)
        composer.setBody("Thursday works for me — I’ll bring the menu.\n\nAda",
                         selection: NSRange(location: 0, length: 0))
        composer.add([.data(name: "menu.pdf", type: "application/pdf", Data(repeating: 7, count: 120_000))])
        try await Task.sleep(for: .milliseconds(400))   // the file is read, and the lookup marks Dee as new
        try draw("compose-reply", CGSize(width: 760, height: 600)) { _ in
            ComposerForm(composer: composer, host: host, aliases: Self.aliases,
                                accounts: ["hana@example.test", "home@example.test"], lights: false) { _ in }
        }
    }

    func testSendsChecksHoldingASend() async throws {
        let host = host()
        var seed = ComposeSeed()
        seed.from = "hana@example.test"
        seed.to = ["bo@example.test"]
        try draw("compose-guard", CGSize(width: 760, height: 600)) { _ in
            let composer = Composer(seed, host: host)
            open.append(composer)
            composer.setBody("Here’s the plan, attached.", selection: NSRange(location: 26, length: 0))
            composer.send()
            return ComposerForm(composer: composer, host: host, aliases: Self.aliases,
                                accounts: ["hana@example.test"], lights: false) { _ in }
        }
    }

    func testTheComposersPopovers() throws {
        let host = host()
        host.snippets = [API.Snippet(text: "Best, Hana", trigger: "sig")]
        let composer = Composer(ComposeSeed(), host: host)
        open.append(composer)
        try draw("compose-later", CGSize(width: 320, height: 250)) { _ in LaterChoices(composer: composer) }
        try draw("compose-draft-options", CGSize(width: 340, height: 250)) { _ in
            DraftOptions(tone: .warm, chooseTone: { _ in }, go: { _ in })
        }
        try draw("compose-snippets", CGSize(width: 360, height: 340)) { _ in
            SnippetsPanel(composer: composer, host: host) {}
        }
    }
}
