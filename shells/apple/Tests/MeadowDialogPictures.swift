import AppKit
import SwiftUI
import XCTest

private typealias F = StatusFixtures

/// The compose window, Settings, the first run and a sender's card drawn in each light on fictional answers: each
/// must draw, and with `TEST_RUNNER_MEADOW_PICTURES=<folder>` given to `xcodebuild test` each is written there, at
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

    // MARK: Settings and the first run

    private let now = F.at("2026-11-14T15:00:00Z")

    private func status(failing: Bool = false, firstRun: Bool = false) -> AppStatus {
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        if firstRun {
            status.hold(digest: F.digest(accounts: [], aliases: [:], engine: "none", keyFrom: ""),
                        preferences: F.preferences(), now: now)
            return status
        }
        let accounts = [
            F.account("ada@example.test", alias: "Work", type: "gmail", signature: "Ada\\nat the example"),
            F.account("home@example.test", alias: "Home", type: "imap", style: "op", ref: "op://Private/Home/password"),
            F.account("bridge@example.test", alias: "Proton", type: "proton", style: "proton_bridge"),
        ]
        let health = [
            F.standing("ada@example.test", ok: "2026-11-14T14:52:00Z", count: 12),
            failing
                ? F.standing("home@example.test", ok: "2026-11-14T09:00:00Z", error: "LOGIN refused", kind: "AUTH",
                             errorAt: "2026-11-14T14:30:00Z", count: 4)
                : F.standing("home@example.test", ok: "2026-11-14T14:50:00Z", count: 5),
            F.standing("bridge@example.test", ok: "2026-11-14T14:40:00Z", count: 2),
        ]
        status.hold(digest: F.digest(accounts: accounts.compactMap(\.email),
                                     aliases: ["ada@example.test": "Work", "home@example.test": "Home",
                                               "bridge@example.test": "Proton"],
                                     last: "2026-11-14T14:52:00Z"),
                    accounts: accounts, health: health, preferences: F.preferences(quiet: true), now: now)
        return status
    }

    func testSettingsInEachPlace() throws {
        let status = status(failing: true)
        for tab in SettingsTab.order {
            status.settingsTab = tab
            try draw("settings-\(tab.rawValue)", CGSize(width: 840, height: 640)) { _ in
                SettingsLayout(status: status, lights: false)
            }
        }
    }

    func testTheAccountFormAsADialog() throws {
        var draft = AccountDraft()
        draft.email = "ada@example.test"
        draft.kind = .imap
        draft.host = "imap.example.test"
        try draw("settings-account-form", CGSize(width: 560, height: 640)) { _ in
            AccountForm(status: status(), draft: draft, done: { _ in })
        }
    }

    func testTheFirstRunInTheBriefsManner() throws {
        try draw("first-run", CGSize(width: 1000, height: 760)) { _ in
            Welcome(status: status(firstRun: true), keySet: false, lights: false)
        }
    }

    // MARK: A sender's card

    func testASendersCard() async throws {
        let mailbox = Mailbox()
        mailbox.wire = Fixture.server().wire
        await mailbox.refresh()
        mailbox.composeTo = { _ in }
        let card = API.ContactCard(
            accounts: [Fixture.ada, Fixture.work], addr: "aoi@example.test", count: 380, firstAgo: "4 years",
            known: true, lastAgo: "2 days", name: "Aoi Nakamura", ok: true, recent: [
                API.RecentMail(account: Fixture.ada, folder: "INBOX", key: 1, seen: false,
                               subject: "Lunch on Saturday?", uid: 11, when: "9:12 am"),
                API.RecentMail(account: Fixture.ada, folder: "INBOX", key: 2, seen: true,
                               subject: "Photos from the hike", uid: 12, when: "Mon"),
                API.RecentMail(account: Fixture.work, folder: "INBOX", key: 3, seen: true,
                               subject: "Re: The guild dinner", uid: 13, when: "Oct 28"),
            ], replied: 120)
        try draw("contact-card", CGSize(width: 420, height: 500)) { _ in
            ContactCard(mailbox: mailbox, address: card.addr, card: card)
        }
    }
}
