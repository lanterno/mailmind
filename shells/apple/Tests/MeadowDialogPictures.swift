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

    /// `firstRun` is a fresh home, AI mode off; `aiMode` keeps it on, as it stays for whoever kept a key.
    private func status(failing: Bool = false, firstRun: Bool = false, aiMode: Bool = false) -> AppStatus {
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        if firstRun {
            status.hold(digest: aiMode ? F.digest(accounts: [], aliases: [:])
                                       : F.digest(accounts: [], aliases: [:], engine: "none", keyFrom: "",
                                                  off: "turned-off"),
                        preferences: F.preferences(aiEnabled: aiMode), now: now)
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

    /// The AI pane (#49): on with Claude, OpenAI with its models listed, a model on this Mac with model servers found
    /// (one of their models chosen), with none found and while looking, a compatible service with no URL, and turned
    /// off, where spending and writing ahead aren't shown.
    func testTheAIPaneOnAndOff() throws {
        func ai(_ preferences: API.Preferences, _ digest: API.Digest) -> AppStatus {
            let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                          badge: { _ in }, announce: { _ in }))
            status.hold(digest: digest, preferences: preferences, now: now)
            return status
        }
        let size = CGSize(width: 620, height: 1100)
        let claude = ai(F.preferences(), F.digest(spent: 1_420_000))
        try draw("settings-ai-claude", size) { _ in OnPaper { AIPane(status: claude, kept: [.anthropic]) } }
        let openAI = ai(F.preferences(provider: "openai", model: "gpt-fictional-mini"),
                        F.digest(provider: "openai", model: "gpt-fictional-mini"))
        let listed = AIChoices.Listed(provider: .openai, list: API.ModelList(
            default: "gpt-fictional", models: ["gpt-fictional", "gpt-fictional-mini", "gpt-fictional-nano"], ok: true))
        try draw("settings-ai-openai", size) { _ in
            OnPaper { AIPane(status: openAI, kept: [.openai], listed: listed) }
        }
        let servers = [
            API.LocalServer(models: ["llama-fictional:8b", "qwen-fictional:14b"], name: "Ollama",
                            url: "http://127.0.0.1:11434/v1"),
            API.LocalServer(models: ["mistral-fictional-7b"], name: "LM Studio", url: "http://127.0.0.1:1234/v1"),
        ]
        let found = ai(F.preferences(provider: "local", model: "qwen-fictional:14b"),
                       F.digest(provider: "local", model: "qwen-fictional:14b"))
        try draw("settings-ai-local-found", size) { _ in
            OnPaper { AIPane(status: found, kept: [], look: .found(servers)) }
        }
        let wanting = ai(F.preferences(provider: "local"), F.digest(engine: "none", provider: "local", off: "no-model"))
        try draw("settings-ai-local-none", size) { _ in
            OnPaper { AIPane(status: wanting, kept: [], look: .found([])) }
        }
        try draw("settings-ai-local-looking", size) { _ in
            OnPaper { AIPane(status: wanting, kept: [], look: .looking) }
        }
        let compatible = ai(F.preferences(provider: "compatible"),
                            F.digest(engine: "none", provider: "compatible", off: "no-url"))
        try draw("settings-ai-compatible", size) { _ in OnPaper { AIPane(status: compatible, kept: []) } }
        let off = ai(F.preferences(aiEnabled: false), F.digest(engine: "none", off: "turned-off"))
        try draw("settings-ai-off", size) { _ in OnPaper { AIPane(status: off, kept: [.anthropic]) } }
    }

    func testTheAccountFormAsADialog() throws {
        var draft = AccountDraft()
        draft.email = "ada@example.test"
        draft.take(API.PresetFound(domain: "example.test", ok: true, supported: true, sure: false))
        draft.host = "imap.example.test"
        try draw("settings-account-form", CGSize(width: 560, height: 760)) { _ in
            AccountForm(status: status(), draft: draft, done: { _ in })
        }
        try draw("settings-account-form-new", CGSize(width: 560, height: 560)) { _ in
            AccountForm(status: status(), draft: AccountDraft(), done: { _ in })
        }
    }

    /// A Workspace address, which Gmail's settings fit, and the steps to its App Password.
    func testAGmailAccountsFormWithTheStepsToAnAppPassword() throws {
        var draft = AccountDraft()
        draft.email = "ada@acme.example.test"
        draft.take(API.PresetFound(domain: "acme.example.test", name: "Gmail", ok: true, preset: .gmail,
                                   settings: API.PresetSettings(type: "gmail"), supported: true, sure: false))
        try draw("settings-account-form-offer", CGSize(width: 560, height: 760)) { _ in
            AccountForm(status: status(), draft: draft, done: { _ in })
        }
        draft.useFound()
        try draw("settings-account-form-gmail", CGSize(width: 560, height: 820)) { _ in
            AccountForm(status: status(), draft: draft, done: { _ in })
        }
    }

    /// An iCloud address, its servers filled in and its app-specific password's page beside the password; and a
    /// Microsoft one, told it can't be added yet.
    func testAProvidersFormAndOneMailMindCantLogInToYet() throws {
        var icloud = AccountDraft()
        icloud.email = "ada@icloud.com"
        icloud.take(API.PresetFound(
            domain: "icloud.com", link: API.Link(url: "https://account.apple.com", words: "Make an app-specific password"),
            name: "iCloud", note: "iCloud Mail takes an app-specific password, not your Apple Account password: make one at account.apple.com, under Sign-In and Security, then paste it here.",
            ok: true, preset: .icloud,
            settings: API.PresetSettings(host: "imap.mail.me.com", port: 993, smtpHost: "smtp.mail.me.com", smtpPort: 587,
                                         smtpStarttls: true, starttls: false, trash: "Deleted Messages", type: "imap"),
            supported: true, sure: true))
        try draw("settings-account-form-icloud", CGSize(width: 560, height: 860)) { _ in
            AccountForm(status: status(), draft: icloud, done: { _ in })
        }
        var outlook = AccountDraft()
        outlook.email = "ada@outlook.com"
        outlook.take(API.PresetFound(domain: "outlook.com", name: "Microsoft",
                                     note: "Outlook, Hotmail, Live and Microsoft 365 accounts need Microsoft's own sign-in, which MailMind doesn't support yet.",
                                     ok: true, preset: .microsoft, supported: false, sure: true))
        try draw("settings-account-form-microsoft", CGSize(width: 560, height: 540)) { _ in
            AccountForm(status: status(), draft: outlook, done: { _ in })
        }
    }

    func testTheFirstRunInTheBriefsManner() throws {
        try draw("first-run", CGSize(width: 1000, height: 760)) { _ in
            Welcome(status: status(firstRun: true), kept: [], lights: false)
        }
        try draw("first-run-ai-mode", CGSize(width: 1000, height: 900)) { _ in
            Welcome(status: status(firstRun: true, aiMode: true), kept: [.anthropic], lights: false)
        }
    }

    /// Help as the mockup's design notes, to lay beside reference/1440-*-notes.png.
    func testHelpAsTheMockupsNotes() throws {
        try draw("help", HelpWindow.size) { _ in HelpWindow(status: status(), lights: false) }
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
            // On the dialog's paper, as the window's scrim shows it.
            ContactCard(mailbox: mailbox, address: card.addr, card: card, close: {})
                .meadowPanel(radius: 18, raised: true)
        }
    }
}

/// A pane on the Settings dialog's paper, as the dialog lays it.
private struct OnPaper<Content: View>: View {
    @ViewBuilder let content: () -> Content

    var body: some View {
        content().background { ComposePaper() }
    }
}
