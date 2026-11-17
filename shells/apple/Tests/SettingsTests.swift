import AppKit
import Foundation
import SwiftUI
import XCTest

private typealias F = StatusFixtures

/// The account form checks what it must before it sends, sends what the page sends, takes the server's presets, and
/// never shows a typed password.
final class AccountDraftTests: XCTestCase {
    /// A fictional password: it guards nothing.
    private let typed = "fixture-app-password-1234"

    /// iCloud's preset, as the server says it for an address at its own domain.
    private let icloud = API.PresetFound(
        domain: "icloud.com", link: API.Link(url: "https://account.apple.com", words: "Make an app-specific password"),
        name: "iCloud", note: "iCloud Mail takes an app-specific password.", ok: true, preset: .icloud,
        settings: API.PresetSettings(host: "imap.mail.me.com", port: 993, smtpHost: "smtp.mail.me.com", smtpPort: 587,
                                     smtpStarttls: true, starttls: false, trash: "Deleted Messages", type: "imap"),
        supported: true, sure: true)

    /// Gmail's, for an address whose domain Google's servers take.
    private let workspace = API.PresetFound(
        domain: "acme.example.test", link: API.Link(url: "https://myaccount.google.com/apppasswords", words: "Make an App Password"),
        name: "Gmail", note: nil, ok: true, preset: .gmail, settings: API.PresetSettings(type: "gmail"),
        supported: true, sure: false)

    /// Microsoft's, which MailMind can't log in to yet.
    private let microsoft = API.PresetFound(
        domain: "outlook.com", name: "Microsoft", note: "Microsoft's sign-in isn't supported yet.", ok: true,
        preset: .microsoft, supported: false, sure: true)

    private let unknown = API.PresetFound(domain: "example.test", ok: true, supported: true, sure: false)

    func testANewAccountHasNoTypeUntilTheServerSaysAndThenNeedsAServerAndAPassword() {
        var draft = AccountDraft()
        XCTAssertNil(draft.kind, "no type picked for a person")
        XCTAssertEqual(draft.problem, "Type the account’s email address.")
        draft.email = "ada@example.test"
        draft.take(unknown)
        XCTAssertEqual(draft.kind, .imap, "an address no preset knows is Other IMAP")
        XCTAssertEqual(AccountDraft.Kind.imap.label, "Other IMAP")
        XCTAssertEqual(draft.problem, "Type the incoming server’s name.")
        draft.host = "imap.example.test"
        XCTAssertEqual(draft.problem, "Type the account’s password.")
        draft.password = typed
        XCTAssertNil(draft.problem)
        draft.pick(.proton)
        draft.password = ""
        XCTAssertNil(draft.problem, "a Proton account logs in with the Bridge's password")
    }

    func testItSendsTheServersAsTypedAndThePasswordOnlyToTheKeychain() throws {
        var draft = AccountDraft()
        draft.email = "  ada@example.test "
        draft.alias = " Ada "
        draft.password = " \(typed) "
        draft.pick(.gmail)
        let gmail = draft.body
        XCTAssertEqual(gmail.email, "ada@example.test")
        XCTAssertEqual(gmail.alias, "Ada")
        XCTAssertEqual(gmail.type, .string("gmail"))
        XCTAssertNil(gmail.host, "a type's server is the server's to fill in")
        XCTAssertNil(gmail.smtpHost)
        XCTAssertEqual(gmail.credential, .object(["style": .string("keychain"), "value": .string(typed)]))

        draft.pick(.imap)
        draft.host = "imap.example.test"
        draft.port = "143"
        draft.security = .starttls
        let blank = draft.body
        XCTAssertEqual(blank.host, .string("imap.example.test"))
        XCTAssertEqual(blank.port, .number(143))
        XCTAssertEqual(blank.starttls, true)
        XCTAssertNil(blank.smtpHost, "an outgoing server left blank is the server's to fill in")
        XCTAssertEqual(blank.smtpPort, .number(587))
        XCTAssertEqual(blank.smtpStarttls, true)

        draft.smtpHost = " smtp.example.test "
        draft.smtpPort = "465"
        draft.smtpSecurity = .tls
        let typedOut = draft.body
        XCTAssertEqual(typedOut.smtpHost, .string("smtp.example.test"))
        XCTAssertEqual(typedOut.smtpPort, .number(465))
        XCTAssertEqual(typedOut.smtpStarttls, false)
        let test = try XCTUnwrap(draft.test?.draft)
        XCTAssertEqual(test.smtpHost, .string("smtp.example.test"), "a test logs in where the account will send from")
        XCTAssertEqual(test.smtpPort, .number(465))
        XCTAssertEqual(test.smtpStarttls, false)

        draft.pick(.proton)
        XCTAssertEqual(draft.body.credential, .object(["style": .string("proton_bridge")]))
        XCTAssertNil(draft.body.host)
    }

    /// An Other IMAP account whose file holds no outgoing server: an edit shows no host (the incoming one is its
    /// prompt) and sends none of it unless a person changes it, so it goes on following the incoming host.
    func testAnEditLeavesAnOutgoingServerNeverSavedFollowingTheIncomingOne() {
        var row = F.account("dee@example.test")
        row.smtpHost = row.host
        row.smtpPort = 587
        row.smtpStarttls = true
        row.defaultsApplied.smtpHost = true
        row.defaultsApplied.smtpPort = true
        row.defaultsApplied.smtpStarttls = true
        var draft = AccountDraft(editing: row)
        XCTAssertEqual([draft.smtpHost, draft.smtpPort], ["", "587"])
        XCTAssertEqual(draft.smtpSecurity, .starttls)
        draft.alias = "Dee"
        let left = draft.body
        XCTAssertEqual(left.host, .string("imap.example.test"))
        XCTAssertNil(left.smtpHost, "the incoming host isn't written in as the outgoing one")
        XCTAssertNil(left.smtpPort, "a port the file doesn't hold stays the type's")
        XCTAssertNil(left.smtpStarttls)

        draft.smtpPort = "465"
        draft.smtpSecurity = .tls
        draft.smtpHost = "smtp.example.test"
        let changed = draft.body
        XCTAssertEqual(changed.smtpHost, .string("smtp.example.test"), "what a person changes is sent")
        XCTAssertEqual(changed.smtpPort, .number(465))
        XCTAssertEqual(changed.smtpStarttls, false)

        var gmail = row
        gmail.type = "gmail"
        var becomes = AccountDraft(editing: gmail)
        becomes.pick(.imap)
        XCTAssertEqual(becomes.body.smtpPort, .number(587), "one becoming Other IMAP sends what the form shows")
        XCTAssertEqual(becomes.body.smtpStarttls, true)
    }

    func testAnEditKeepsTheAddressAndThePasswordItHasUnlessOneIsTyped() {
        var draft = AccountDraft(editing: F.account("ada@example.test", alias: "Ada", signature: "Ada"))
        XCTAssertEqual(draft.kind, .imap)
        XCTAssertEqual(draft.smtpHost, "smtp.example.test")
        XCTAssertEqual(draft.smtpPort, "465")
        XCTAssertEqual(draft.smtpSecurity, .tls)
        draft.email = "eve@example.test"
        XCTAssertEqual(draft.address, "ada@example.test", "identity is fixed")
        XCTAssertNil(draft.body.credential)
        XCTAssertNil(draft.body.trash, "an edit leaves the Trash the server found")
        XCTAssertNil(draft.problem)
        XCTAssertEqual(draft.test, API.TryBody(email: "ada@example.test"), "a test without a password tests what is saved")
        draft.password = typed
        XCTAssertEqual(draft.test?.draft?.email, "ada@example.test")
        XCTAssertNil(AccountDraft().test, "a new account with no password has nothing to test")
    }

    func testAProtonAccountIsTestedOnlyOnceItIsSavedWithTheBridgesPassword() {
        var draft = AccountDraft()
        draft.email = "ada@example.test"
        draft.pick(.proton)
        XCTAssertNil(draft.test, "a new Proton account has nothing the server may test yet")
        XCTAssertEqual(draft.testsOnceAdded, "Add the account, then test it.")
        draft.pick(.imap)
        XCTAssertNil(draft.testsOnceAdded)

        var saved = AccountDraft(editing: F.account("ada@example.test", type: "proton", style: "proton_bridge"))
        XCTAssertEqual(saved.test, API.TryBody(email: "ada@example.test"), "a saved one is tested as it is saved")
        saved.password = typed
        XCTAssertEqual(saved.test, API.TryBody(email: "ada@example.test"), "nothing typed stands for the Bridge's password")
        XCTAssertNil(saved.testsOnceAdded)
    }

    func testAnAccountThatReadsItsPasswordFrom1PasswordOrTheEnvironmentShowsItAndKeepsIt() {
        var op = AccountDraft(editing: F.account("ada@example.test", style: "op", ref: "op://Vault/Ada/password"))
        XCTAssertEqual(op.keptElsewhere, "From 1Password: op://Vault/Ada/password")
        XCTAssertFalse(op.typesPassword)
        op.password = typed
        XCTAssertNil(op.credential, "nothing typed replaces where it is read from")
        XCTAssertEqual(op.test, API.TryBody(email: "ada@example.test"))
        let env = AccountDraft(editing: F.account("ada@example.test", style: "env", variable: "ADA_PASSWORD"))
        XCTAssertEqual(env.keptElsewhere, "From the environment variable ADA_PASSWORD")
        XCTAssertNil(env.body.credential)
        let kept = AccountDraft(editing: F.account("ada@example.test"))
        XCTAssertNil(kept.keptElsewhere)
        XCTAssertTrue(kept.typesPassword)
        XCTAssertTrue(AccountDraft().typesPassword, "a new account's password goes to the Keychain")
        XCTAssertEqual(AccountDraft(editing: F.account("ada@example.test", type: "proton", style: "proton_bridge")).kind, .proton)
    }

    func testATypedPasswordIsNeverDescribed() {
        var draft = AccountDraft()
        draft.email = "ada@example.test"
        draft.password = typed
        var dumped = ""
        dump(draft, to: &dumped)
        for shown in [draft.description, draft.debugDescription, String(describing: draft), String(reflecting: draft),
                      "\(draft)", dumped] {
            XCTAssertFalse(shown.contains(typed), shown)
            XCTAssertFalse(shown.contains("1234"), shown)
        }
        XCTAssertTrue(draft.description.contains("password: typed"))
        draft.forgetPassword()
        XCTAssertEqual(draft.password, "")
    }

    func testATestsFailureSaysWhatToDoNextWithItsPageAndItsClassInSmallType() {
        XCTAssertEqual(Tested.of(.loggedIn(API.LoggedIn(connect: true, detail: "", email: "ada@example.test", ms: 12, ok: true))),
                       .connected)
        let link = API.Link(url: "https://account.apple.com", words: "Make an app-specific password")
        let refused = API.CouldntLogIn(connect: false, email: "ada@icloud.com", error: "AUTHENTICATIONFAILED", link: link,
                                       ok: true, says: "iCloud didn't accept that password.")
        XCTAssertEqual(Tested.of(.couldntLogIn(refused)),
                       .failed("iCloud didn't accept that password.", link: link, detail: "AUTHENTICATIONFAILED"))
        let same = API.CouldntLogIn(connect: false, email: nil, error: "Gmail needs an App Password here.", ok: true,
                                    says: "Gmail needs an App Password here.")
        XCTAssertEqual(Tested.of(.couldntLogIn(same)), .failed("Gmail needs an App Password here."),
                       "a class that is the words already isn't said twice")
        XCTAssertEqual(Tested.of(URLError(.timedOut)), .failed("The test didn’t finish. Try again."))
    }

    func testOnlyANewAddressWithAnAtInItIsAskedWhichPresetItTakes() {
        var draft = AccountDraft()
        XCTAssertNil(draft.presetQuery)
        draft.email = "ada"
        XCTAssertNil(draft.presetQuery)
        draft.email = "  ada@acme.example.test "
        XCTAssertEqual(draft.presetQuery, "ada@acme.example.test")
        XCTAssertNil(AccountDraft(editing: F.account("ada@example.test")).presetQuery, "a saved account's type is its own")
    }

    func testAProvidersOwnAddressTakesItsServersItsTrashAndItsPasswordsPage() {
        var draft = AccountDraft()
        draft.email = "ada@icloud.com"
        draft.take(icloud)
        XCTAssertEqual(draft.kind, .imap)
        XCTAssertEqual([draft.host, draft.port, draft.smtpHost, draft.smtpPort], ["imap.mail.me.com", "993", "smtp.mail.me.com", "587"])
        XCTAssertEqual(draft.security, .tls)
        XCTAssertEqual(draft.smtpSecurity, .starttls)
        XCTAssertEqual(draft.note, "iCloud Mail takes an app-specific password.")
        XCTAssertEqual(draft.link?.url, "https://account.apple.com", "its page is where the password is typed")
        XCTAssertEqual(draft.presetWords, .fits("iCloud address: iCloud’s settings are filled in."))
        draft.password = typed
        XCTAssertEqual(draft.body.trash, "Deleted Messages")

        draft.take(unknown)                                  // another address: what the preset filled in goes
        XCTAssertEqual(draft.kind, .imap)
        XCTAssertEqual([draft.host, draft.smtpHost], ["", ""])
        XCTAssertNil(draft.body.trash)
        XCTAssertNil(draft.link)

        draft.host = "imap.example.test"                     // what was typed stays
        draft.take(unknown)
        XCTAssertEqual(draft.host, "imap.example.test")
    }

    func testAProvidersSettingsAreOfferedWhereItsServersTakeTheDomainsMail() {
        var draft = AccountDraft()
        draft.email = "ada@acme.example.test"
        draft.take(workspace)
        XCTAssertEqual(draft.kind, .imap, "offered, not taken")
        XCTAssertEqual(draft.presetWords, .offer("Gmail takes acme.example.test’s mail: use Gmail’s settings?", button: "Use Gmail"))
        draft.useFound()
        XCTAssertEqual(draft.kind, .gmail)
        XCTAssertEqual(draft.presetWords, .fits("Gmail takes acme.example.test’s mail: Gmail’s settings fit it."))
        draft.pick(.imap)
        XCTAssertEqual(draft.presetWords, .offer("Gmail takes acme.example.test’s mail: use Gmail’s settings?", button: "Use Gmail"))

        draft.take(unknown)
        XCTAssertNil(draft.presetWords)
        draft.found = nil
        XCTAssertNil(draft.presetWords)
    }

    func testAMicrosoftAddressIsToldItCantBeAddedYetAndNothingIsAsked() {
        var draft = AccountDraft()
        draft.email = "ada@outlook.com"
        draft.take(microsoft)
        XCTAssertTrue(draft.unsupported)
        XCTAssertEqual(draft.note, "Microsoft's sign-in isn't supported yet.")
        XCTAssertEqual(draft.problem, "Microsoft's sign-in isn't supported yet.")
        XCTAssertNil(draft.presetWords)
        XCTAssertFalse(AccountDraft(editing: F.account("ada@example.test")).unsupported)
    }

    func testGmailsPasswordFieldShowsAnAppPasswordsShapeAndTheStepsToOne() {
        var draft = AccountDraft()
        draft.pick(.gmail)
        XCTAssertEqual(draft.passwordPrompt, "abcd efgh ijkl mnop")
        draft.pick(.imap)
        XCTAssertEqual(draft.passwordPrompt, "paste the password or app password")
        XCTAssertEqual(AccountDraft(editing: F.account("ada@example.test")).passwordPrompt, "unchanged — leave blank to keep")
        XCTAssertEqual(AccountDraft.appPasswords.absoluteString, "https://myaccount.google.com/apppasswords")
        XCTAssertEqual(AccountDraft.appPasswordSteps.count, 3)
    }
}

/// The read-only switch: what it says while it and the run disagree.
final class ReadOnlyRunTests: XCTestCase {
    func testTheSwitchSaysItTakesEffectWhenMailMindOpensAgain() {
        XCTAssertNil(ReadOnlyRun.pending(chosen: true, running: nil), "nothing until the core has said how it runs")
        XCTAssertNil(ReadOnlyRun.pending(chosen: true, running: "read-only"))
        XCTAssertNil(ReadOnlyRun.pending(chosen: false, running: "full"))
        XCTAssertEqual(ReadOnlyRun.pending(chosen: true, running: "full"), "Read only starts the next time MailMind opens.")
        XCTAssertEqual(ReadOnlyRun.pending(chosen: false, running: "read-only"),
                       "MailMind changes mail on your servers again the next time it opens.")
    }

    /// Relaunch Now opens MailMind again only as it really quits: a quit called off leaves nothing waiting to open it
    /// when the person next quits.
    @MainActor
    func testItOpensAgainOnlyAsItReallyQuits() async {
        let center = NotificationCenter()
        let quits = { center.post(name: NSApplication.willTerminateNotification, object: nil) }
        let opened = expectation(description: "opened again as it quit")
        ReadOnlyRun.quitThenOpen(center: center, quit: quits, openAgain: { opened.fulfill() })
        await fulfillment(of: [opened], timeout: 1)

        let openedLater = expectation(description: "opened again at a later quit")
        openedLater.isInverted = true
        ReadOnlyRun.quitThenOpen(center: center, quit: {}, openAgain: { openedLater.fulfill() })
        quits()
        await fulfillment(of: [openedLater], timeout: 0.3)
    }

    /// A test run shares the app's defaults, so it has no switch: it says how it runs in the switch's place.
    func testADemoOrTestRunHasNoSwitchAndSaysHowItRuns() {
        XCTAssertFalse(ReadOnlyRun.switchable)
        XCTAssertEqual(ReadOnlyRun.notSwitchable(running: "read-only"), """
            This demo or test run is read only, as it was started. Read only is a switch here when you open MailMind \
            yourself.
            """)
        XCTAssertEqual(ReadOnlyRun.notSwitchable(running: "full"), """
            This demo or test run changes mail on its servers, as it was started. Read only is a switch here when you \
            open MailMind yourself.
            """)
    }
}

/// What Settings says of the Claude API key and the Bridge's password: whether one is kept, and what wins over it.
final class SecretStandingTests: XCTestCase {
    func testOnlyTheVariablesThisAppHandsTheCoreCountAndOnlyWhetherTheyAreSet() {
        let sources = SecretSources(environment: [
            "ANTHROPIC_API_KEY": "", "MAILMIND_PROTON_BRIDGE_PASS": "x", "MAILMIND_PROTON_OP_ITEM": "Bridge",
        ])
        XCTAssertEqual(sources, SecretSources(apiKeyVariable: false, bridgeVariable: true, bridgeOpItem: true),
                       "an empty variable gives nothing")
        XCTAssertEqual(SecretSources(environment: [:]), SecretSources())
    }

    func testTheClaudeKey() {
        let on = F.digest().ai, off = F.digest(engine: "none", keyFrom: "").ai
        let fromVariable = F.digest(keyFrom: "ANTHROPIC_API_KEY").ai
        XCTAssertEqual(SecretStanding.apiKey(.anthropic, kept: true, ai: on, sources: .init()).summary,
                       "Kept in your login Keychain")
        XCTAssertNil(SecretStanding.apiKey(.anthropic, kept: true, ai: on, sources: .init()).note)
        XCTAssertEqual(SecretStanding.apiKey(.anthropic, kept: true, ai: on, sources: .init(apiKeyVariable: true)).note,
                       "ANTHROPIC_API_KEY is set too, and the key kept here wins over it.")
        XCTAssertEqual(SecretStanding.apiKey(.anthropic, kept: true, ai: off, sources: .init()).summary,
                       "A key is kept, but it can’t be read")
        let environment = SecretStanding.apiKey(.anthropic, kept: false, ai: fromVariable,
                                                sources: .init(apiKeyVariable: true))
        XCTAssertEqual(environment.summary, "From the environment variable ANTHROPIC_API_KEY")
        XCTAssertEqual(environment.note, "A key kept here would win over it.")
        XCTAssertFalse(environment.kept)
        XCTAssertEqual(SecretStanding.apiKey(.anthropic, kept: false, ai: off, sources: .init()).summary,
                       "No key: AI is off")
    }

    /// Each provider's key stands on its own: only Claude's is read from ANTHROPIC_API_KEY, and what the digest says
    /// of the provider in force says nothing of another's.
    func testEachProvidersKey() {
        let openAIOff = F.digest(engine: "none", keyFrom: "", provider: "openai").ai
        let claudeOn = F.digest().ai
        let variable = SecretSources(apiKeyVariable: true)
        let none = SecretStanding.apiKey(.openai, kept: false, ai: openAIOff, sources: variable)
        XCTAssertEqual(none.summary, "No key: AI is off")
        XCTAssertEqual(none.note, "Without a key nothing is sent to OpenAI.", "the variable is Claude's alone")
        XCTAssertFalse(none.kept)
        XCTAssertEqual(SecretStanding.apiKey(.openai, kept: true, ai: openAIOff, sources: .init()).summary,
                       "A key is kept, but it can’t be read")
        XCTAssertEqual(SecretStanding.apiKey(.gemini, kept: true, ai: openAIOff, sources: .init()).summary,
                       "Kept in your login Keychain", "OpenAI's trouble isn't Gemini's")
        XCTAssertNil(SecretStanding.apiKey(.gemini, kept: true, ai: claudeOn, sources: variable).note)
        XCTAssertEqual(SecretStanding.apiKey(.gemini, kept: false, ai: claudeOn, sources: .init()).note,
                       "Without a key nothing is sent to Google.")
        let optional = SecretStanding.apiKey(.compatible, kept: false, ai: nil, sources: .init())
        XCTAssertEqual(optional.summary, "No key")
        XCTAssertEqual(optional.note, "The service is asked with none. Keep one if it asks for one.")
        XCTAssertEqual(SecretStanding.apiKey(.compatible, kept: true, ai: nil, sources: .init()).summary,
                       "Kept in your login Keychain")
    }

    func testTheBridgePasswordTheEnvironmentThen1PasswordThenTheKeychain() {
        XCTAssertEqual(SecretStanding.bridgePassword(set: true, sources: .init()),
                       SecretStanding(summary: "Kept in your login Keychain", note: nil, kept: true))
        XCTAssertEqual(SecretStanding.bridgePassword(set: false, sources: .init()).summary, "Not set")
        let variable = SecretStanding.bridgePassword(set: true, sources: .init(bridgeVariable: true, bridgeOpItem: true))
        XCTAssertEqual(variable.summary, "From the environment variable MAILMIND_PROTON_BRIDGE_PASS")
        XCTAssertEqual(variable.note, "It wins over the Keychain. The Keychain keeps one too.")
        XCTAssertTrue(variable.kept, "what the Keychain keeps can still be taken out")
        XCTAssertEqual(SecretStanding.bridgePassword(set: false, sources: .init(bridgeOpItem: true)).summary,
                       "From 1Password, the item MAILMIND_PROTON_OP_ITEM names")
    }
}

/// Settings over the core in this process, on a temp home of its own with a file in the Keychain's place: what it keeps
/// goes there and never comes back in an answer.
final class SettingsCoreTests: XCTestCase {
    /// Guards only this test's empty database.
    private static let key = "3c5e7a9b1d2f4a6c8e0b2d4f6a8c0e1b3d5f7a9c2e4b6d8f0a1c3e5b7d9f2a4c"
    /// Fictional secrets: they guard nothing.
    private static let apiKey = "sk-ant-fixture-0000-not-a-key"
    private static let password = "fixture-imap-password-5678"
    private var folder: URL!
    private var keychain: URL { folder.appending(path: "keychain.json") }

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appending(path: "mailmind-settings-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: folder)
    }

    @MainActor
    private func opened(notifier: any Notifier = SystemNotifier.Silent()) async throws -> (Core, AppStatus) {
        let core = try await Core.open(CoreHome(folder: folder.path(percentEncoded: false),
                                                variables: ["MAILMIND_DB_KEY": Self.key, "MAILMIND_NOSYNC": "1"],
                                                standIn: keychain.path(percentEncoded: false)))
        let status = AppStatus(newMail: NewMailAlerts(notifier: notifier, isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        await status.attach(core)
        return (core, status)
    }

    @MainActor
    private func close(_ core: Core, _ status: AppStatus) async {
        status.detach()
        await core.close()
    }

    @MainActor
    func testAFreshHomeIsTheFirstRun() async throws {
        let (core, status) = try await opened()
        XCTAssertTrue(status.accountsRead)
        XCTAssertTrue(status.firstRun)
        XCTAssertNotNil(status.preferences)
        XCTAssertNotNil(status.digest)
        await close(core, status)
    }

    @MainActor
    func testTheClaudeKeyIsKeptInTheStandInAndNeverAnswered() async throws {
        let (core, status) = try await opened()
        let before = try await core.get(API.Get.AiKey.self)
        XCTAssertEqual(before.kept, [])
        let kept = try await SecretPost.post(core, API.Post.AiKey.self,
                                             API.KeyBody(key: Self.apiKey, provider: .anthropic))
        XCTAssertEqual(kept.kept, [.anthropic])
        let after = try await core.get(API.Get.AiKey.self)
        XCTAssertEqual(after.kept, [.anthropic])
        let stood = try String(contentsOf: keychain, encoding: .utf8)
        XCTAssertTrue(stood.contains(Self.apiKey), "the stand-in, not the login Keychain, keeps it")
        for target in ["/api/ai/key", "/api/digest", "/api/accounts"] {
            let answer = await core.send("GET", target, Data())
            XCTAssertFalse(String(decoding: answer.body, as: UTF8.self).contains(Self.apiKey), target)
        }
        let cleared = try await core.post(API.Post.AiKeyClear.self, API.ProviderBody(provider: .anthropic))
        XCTAssertEqual(cleared.kept, [])
        XCTAssertFalse(try String(contentsOf: keychain, encoding: .utf8).contains(Self.apiKey))
        await close(core, status)
    }

    /// Each provider's key is kept on its own, a model on this Mac's too, and the pane's choices reach the core as it
    /// keeps them: another provider starts from its own model and URL.
    @MainActor
    func testEachProvidersKeyAndTheChoicesTheCoreKeeps() async throws {
        let (core, status) = try await opened()
        let openAI = "sk-openai-fictional-0002"
        let kept = try await SecretPost.post(core, API.Post.AiKey.self, API.KeyBody(key: openAI, provider: .openai))
        XCTAssertEqual(kept.kept, [.openai])
        let both = try await SecretPost.post(core, API.Post.AiKey.self,
                                             API.KeyBody(key: "local-fictional-0003", provider: .local))
        XCTAssertEqual(Set(both.kept), [.openai, .local], "a model server on this Mac may ask for a key")
        await status.setPreferences(.init(aiEnabled: true, aiModel: "gpt-fictional", aiProvider: .openai))?.value
        XCTAssertEqual(status.preferences?.aiProvider, .openai)
        XCTAssertEqual(status.preferences?.aiModel, "gpt-fictional")
        await status.setPreferences(AIChoices.choose(.local))?.value
        XCTAssertEqual(status.preferences?.aiProvider, .local)
        XCTAssertEqual(status.preferences?.aiModel, "", "another provider starts from its own model")
        XCTAssertEqual(status.digest?.ai.off, .noModel, "a model on this Mac has no default")
        XCTAssertEqual(status.digest?.ai.provider, .local)
        await status.setPreferences(.init(aiEnabled: false))?.value
        XCTAssertEqual(status.digest?.ai.off, .turnedOff)
        XCTAssertEqual(status.digest?.ai.engine, "none")
        await close(core, status)
    }

    /// A model on this Mac: the core finds no model server in a test's home (it asks none), even with AI mode off;
    /// a model found is chosen with its server's URL in one change; and a URL off this Mac is refused, in words a
    /// person reads.
    @MainActor
    func testAModelOnThisMac() async throws {
        let (core, status) = try await opened()
        await status.setPreferences(.init(aiEnabled: false, aiProvider: .local))?.value
        let found = try await core.get(API.Get.AiLocal.self)
        XCTAssertEqual(found.servers, [], "nothing found is no servers, not an error")
        let look = await AIChoices.look { try await core.get(API.Get.AiLocal.self) }
        XCTAssertEqual(look, .found([]))
        // A port nothing listens on: the digest says how AI stands without asking, and nothing here asks a model.
        let server = API.LocalServer(models: ["llama-fictional"], name: "LM Studio", url: "http://127.0.0.1:9/v1")
        await status.setPreferences(AIChoices.choose("llama-fictional", on: server))?.value
        XCTAssertEqual(status.preferences?.aiBaseUrl, "http://127.0.0.1:9/v1")
        XCTAssertEqual(status.preferences?.aiModel, "llama-fictional")
        XCTAssertTrue(AIChoices.isChosen("llama-fictional", on: server, preferences: status.preferences))
        XCTAssertEqual(status.digest?.ai.provider, .local)
        XCTAssertEqual(status.digest?.ai.off, .turnedOff, "AI mode is still off")
        do {
            _ = try await core.post(API.Post.Preferences.self, .init(aiBaseUrl: "https://ai.example.test/v1"))
            XCTFail("a model on this Mac is on this Mac")
        } catch let problem as Problem {
            XCTAssertTrue(problem.isRefusal)
            XCTAssertEqual(problem.detail, "A model on this Mac must be at 127.0.0.1, ::1 or localhost.")
        }
        let held = try await core.get(API.Get.Preferences.self).preferences
        XCTAssertEqual(held.aiBaseUrl, "http://127.0.0.1:9/v1", "the refused URL isn't kept")
        await close(core, status)
    }

    /// Asking a provider for its models is refused, in words a person reads, where it can't be asked; nothing here
    /// reaches a real provider.
    @MainActor
    func testTheModelsAreRefusedWhereTheyCantBeAsked() async throws {
        let (core, status) = try await opened()
        for (provider, why) in [(API.AiProvider.compatible, "Give the service’s URL first."),
                                (.gemini, "Keep a key for this provider first.")] {
            do {
                _ = try await core.get(API.Get.AiModels.self, ["provider": provider.rawValue])
                XCTFail("\(provider) can't be asked yet")
            } catch let problem as Problem {
                XCTAssertEqual(problem.detail?.replacingOccurrences(of: "'", with: "’"), why)
            }
        }
        await close(core, status)
    }

    @MainActor
    func testTheBridgePasswordIsKeptAndTakenOut() async throws {
        let (core, status) = try await opened()
        let kept = try await SecretPost.post(core, API.Post.AccountsBridgePassword.self,
                                             API.PasswordBody(password: Self.password))
        XCTAssertTrue(kept.set)
        let read = try await core.get(API.Get.AccountsBridgePassword.self)
        XCTAssertTrue(read.set)
        let cleared = try await core.post(API.Post.AccountsBridgePasswordClear.self, API.NoBody())
        XCTAssertFalse(cleared.set)
        let gone = try await core.get(API.Get.AccountsBridgePassword.self)
        XCTAssertFalse(gone.set)
        await close(core, status)
    }

    @MainActor
    func testAnAccountAddedIsListedWithItsPasswordKeptAndNotShown() async throws {
        let (core, status) = try await opened()
        var draft = AccountDraft()
        draft.email = "ada@example.test"
        draft.alias = "Ada"
        draft.signature = "Ada, at the example"
        draft.pick(.imap)
        draft.host = "127.0.0.1"
        draft.port = "9"
        draft.password = Self.password
        _ = try await SecretPost.post(core, API.Post.AccountsAdd.self, draft.body)
        await status.readAccounts()
        XCTAssertFalse(status.firstRun)
        let row = try XCTUnwrap(status.accounts.first)
        XCTAssertEqual(row.email, "ada@example.test")
        XCTAssertEqual(row.alias, "Ada")
        XCTAssertEqual(row.signature, "Ada, at the example")
        XCTAssertEqual(row.credential.style, "keychain")
        XCTAssertTrue(row.credential.set)
        let listed = await core.send("GET", "/api/accounts", Data())
        XCTAssertFalse(String(decoding: listed.body, as: UTF8.self).contains(Self.password))

        var edit = AccountDraft(editing: row)
        edit.alias = "Ada Lovelace"
        _ = try await SecretPost.post(core, API.Post.AccountsEdit.self, edit.body)
        await status.readAccounts()
        XCTAssertEqual(status.accounts.first?.alias, "Ada Lovelace")
        XCTAssertTrue(status.accounts.first?.credential.set == true, "an edit with no password typed keeps the one kept")

        // Nothing listens on the discard port: the test says it couldn't log in, and what to do next.
        let answer = try await core.post(API.Post.AccountsTest.self, API.TryBody(email: "ada@example.test"))
        let tested = Tested.of(answer)
        guard case .failed(let why, _, let detail) = tested else { return XCTFail("a closed port logged in") }
        XCTAssertEqual(why, "The server didn't answer. Check the server's name and port, and that this Mac is online.")
        XCTAssertEqual(detail, "connection refused", "the class, in small type")
        XCTAssertFalse(why.contains(Self.password))

        let removed = try await core.post(API.Post.AccountsDelete.self,
                                          API.RemoveBody(deleteMail: true, email: "ada@example.test"))
        XCTAssertEqual(removed.mailDeleted, 0, "its mail on this Mac went too, of which there was none")
        await status.readAccounts()
        XCTAssertTrue(status.firstRun)
        await close(core, status)
    }

    @MainActor
    func testAnAddressAtGooglesOwnDomainTakesGmailsPresetAndATestHomeAsksNoNameserver() async throws {
        let (core, status) = try await opened()
        let gmail = try await core.get(API.Get.AccountsPreset.self, ["email": "Ada@GMail.com"])
        XCTAssertEqual([gmail.domain, gmail.preset?.rawValue, gmail.settings?.type], ["gmail.com", "gmail", "gmail"])
        XCTAssertTrue(gmail.sure)
        let acme = try await core.get(API.Get.AccountsPreset.self, ["email": "ada@acme.example.test"])
        XCTAssertEqual(acme, API.PresetFound(domain: "acme.example.test", ok: true, supported: true, sure: false))
        let icloud = try await core.get(API.Get.AccountsPreset.self, ["email": "ada@icloud.com"])
        XCTAssertEqual(icloud.settings?.host, "imap.mail.me.com")
        XCTAssertEqual(icloud.settings?.trash, "Deleted Messages")
        let outlook = try await core.get(API.Get.AccountsPreset.self, ["email": "ada@outlook.com"])
        XCTAssertFalse(outlook.supported)
        XCTAssertNil(outlook.settings)
        await close(core, status)
    }

    @MainActor
    func testAGmailAppPasswordAsGoogleShowsItIsKeptWithoutItsSpaces() async throws {
        let (core, status) = try await opened()
        var draft = AccountDraft()
        draft.email = "dee@example.test"
        draft.pick(.gmail)
        draft.password = "abcd efgh ijkl mnop"
        _ = try await SecretPost.post(core, API.Post.AccountsAdd.self, draft.body)
        let stood = try String(contentsOf: keychain, encoding: .utf8)
        XCTAssertTrue(stood.contains("abcdefghijklmnop"))
        XCTAssertFalse(stood.contains("abcd efgh"))
        let removed = try await core.post(API.Post.AccountsDelete.self, API.RemoveBody(email: "dee@example.test"))
        XCTAssertNil(removed.mailDeleted, "its mail here is let go of only when asked")
        await close(core, status)
    }

    /// General's Backup & Export says the room the mail takes on this Mac: the database's file and its log, as they
    /// are on disk, said as Finder says a size.
    @MainActor
    func testGeneralSaysTheRoomTheMailTakesOnThisMac() async throws {
        let (core, status) = try await opened()
        let plan = try await core.get(API.Get.ExportPlan.self)
        let onDisk = try ["mailmind.db", "mailmind.db-wal"]
            .map { folder.appending(path: $0) }
            .filter { FileManager.default.fileExists(atPath: $0.path(percentEncoded: false)) }
            .map { try $0.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0 }
            .reduce(0, +)
        XCTAssertGreaterThan(plan.databaseBytes, 0)
        XCTAssertEqual(plan.databaseBytes, onDisk)
        XCTAssertEqual(BackupSection.size(2_000_000_000), "2 GB")
        XCTAssertEqual(BackupSection.size(plan.databaseBytes),
                       ByteCountFormatter.string(fromByteCount: Int64(onDisk), countStyle: .file))
        await close(core, status)
    }

    @MainActor
    func testTheCoreSaysWhatAICostLatelyAndAFreshHomeHasSpentNothing() async throws {
        let (core, status) = try await opened()
        let spent = try await core.get(API.Get.AiSpending.self)
        XCTAssertEqual(AIWords.spent("The last 7 days", spent.week), "The last 7 days: nothing.")
        XCTAssertEqual(AIWords.spent("This month", spent.month), "This month: nothing.")
        XCTAssertEqual(status.digest?.ai.backgroundModel, "", "AI off for want of a key, no model is in use")
        await close(core, status)
    }

    @MainActor
    func testDraftingAheadStartsOffAndIsKeptOnceTurnedOn() async throws {
        let (core, status) = try await opened()
        XCTAssertEqual(status.preferences?.draftAhead, false, "off until a person turns it on")
        await status.setPreferences(.init(draftAhead: true))?.value
        XCTAssertEqual(status.preferences?.draftAhead, true)
        let read = try await core.get(API.Get.Preferences.self).preferences
        XCTAssertTrue(read.draftAhead, "the core keeps it")
        XCTAssertFalse(AppStatus.touchesAI(.init(draftAhead: true)), "how AI stands isn't read again for it")
        await close(core, status)
    }

    @MainActor
    func testAPreferenceTheServerRefusesComesBackAsItHoldsItWithItsWords() async throws {
        let (core, status) = try await opened()
        let was = try XCTUnwrap(status.preferences?.undoSendSeconds)
        await status.setPreferences(.init(undoSendSeconds: 9999))?.value
        XCTAssertEqual(status.preferences?.undoSendSeconds, was)
        let said = try XCTUnwrap(status.notice?.text)
        XCTAssertTrue(said.contains("Undo Send"), said)

        await status.setPreferences(.init(quietFrom: "21:15", quietHours: true))?.value
        let read = try await core.get(API.Get.Preferences.self).preferences
        XCTAssertTrue(read.quietHours)
        XCTAssertEqual(read.quietFrom, "21:15")
        await close(core, status)
    }

    /// The first run has no account to hear from as the core attaches, so macOS is asked whether MailMind may notify
    /// as the first one is added, not at the next launch.
    @MainActor
    func testNotificationsAreAskedForAsTheFirstAccountIsAdded() async throws {
        let asks = Asks()
        let (core, status) = try await opened(notifier: asks)
        XCTAssertTrue(status.firstRun)
        XCTAssertEqual(asks.count, 0, "no account to hear from yet")
        var draft = AccountDraft()
        draft.email = "ada@example.test"
        draft.pick(.imap)
        draft.host = "127.0.0.1"
        draft.port = "9"
        draft.password = Self.password
        _ = try await SecretPost.post(core, API.Post.AccountsAdd.self, draft.body)
        await status.readAccounts()
        await deskEventually { asks.count == 1 }
        await status.readAccounts()
        try await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(asks.count, 1, "asked once")
        await close(core, status)
    }

    /// Counts how often macOS would be asked.
    private final class Asks: Notifier, @unchecked Sendable {
        private let lock = NSLock()
        private var made = 0
        var count: Int { lock.withLock { made } }
        func allowed() async -> Bool? { nil }
        func ask() async -> Bool {
            lock.withLock { made += 1 }
            return true
        }
        func post(id: String, title: String, body: String) async {}
    }
}

/// The first run's account sheet hangs on the window, not on the welcome: saving the first account ends the first run
/// and takes the welcome away, and a Proton account's sheet stays open on its Bridge test until Done.
@MainActor
final class FirstAccountSheetTests: XCTestCase {
    func testTheSheetStaysOpenAsTheFirstAccountEndsTheFirstRun() async throws {
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        status.hold()
        XCTAssertTrue(status.firstRun)
        let host = NSHostingView(rootView: Color.clear.frame(width: 1000, height: 700)
            .modifier(StatusHooks(core: nil, status: status))
            .meadowLight(.day))
        host.frame = CGRect(x: 0, y: 0, width: 1000, height: 700)
        let window = NSWindow(contentRect: host.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        // A sheet attaches only to a window on screen: this one is, but far off it and see-through.
        window.alphaValue = 0
        window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
        window.orderFront(nil)
        defer {
            status.addingFirstAccount = false
            window.orderOut(nil)
            window.contentView = nil
        }
        status.addingFirstAccount = true
        await deskEventually { host.layoutSubtreeIfNeeded(); return window.attachedSheet != nil }
        XCTAssertNotNil(window.attachedSheet, "the welcome's Connect opens the account sheet")

        // The account is saved: the first run ends and the welcome goes, and the sheet stays.
        status.hold(accounts: [StatusFixtures.account("ada@example.test")])
        XCTAssertFalse(status.firstRun)
        for _ in 0..<20 {
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertNotNil(window.attachedSheet, "the sheet outlives the welcome")
        XCTAssertTrue(status.addingFirstAccount)

        status.addingFirstAccount = false
        await deskEventually { host.layoutSubtreeIfNeeded(); return window.attachedSheet == nil }
    }
}

/// Removing an account with its mail on this Mac warns of the sends and drafts only this Mac holds, and says MailMind's
/// backups keep their copies until they are replaced.
@MainActor
final class RemoveAccountTests: XCTestCase {
    /// The outbox tray as the core lists it: Ada's sends in each state, and drafts waiting for their server.
    private let tray = StatusFixtures.decode(API.Outbox.self, """
        {"ok": true, "items": [
          \(RemoveAccountTests.send(1, "queued")), \(RemoveAccountTests.send(2, "sending")),
          \(RemoveAccountTests.send(3, "error")), \(RemoveAccountTests.send(4, "sent")),
          \(RemoveAccountTests.send(5, "cancelled")), \(RemoveAccountTests.send(6, "queued", account: "bo@example.test"))
         ],
         "drafts": [
          {"id": 7, "account": "ADA@example.test", "subject": "Plans", "saved_at": 1, "attempts": 0, "discarding": false, "kept": true},
          {"id": 8, "account": "ada@example.test", "subject": "Old", "saved_at": 1, "attempts": 0, "discarding": true, "kept": false},
          {"id": 9, "account": "bo@example.test", "subject": "Bo's", "saved_at": 1, "attempts": 0, "discarding": false, "kept": false}
         ]}
        """)

    private static func send(_ id: Int, _ state: String, account: String = "ada@example.test") -> String {
        """
        {"id": \(id), "account": "\(account)", "to": ["bo@example.test"], "cc": [], "bcc_count": 0, "subject": "Dinner",
         "state": "\(state)", "attempts": 0, "next_attempt_at": 0, "scheduled": false, "retrying": false,
         "unsure": false, "created_at": "2026-11-16 12:00:00"}
        """
    }

    func testWhatOnlyThisMacHoldsIsCountedForTheAccountAlone() {
        XCTAssertEqual(Unsent.of(tray, account: "ada@example.test"), Unsent(sends: 3, drafts: 1),
                       "waiting, going and failed sends, and a draft not on the server; not one leaving it")
        XCTAssertEqual(Unsent.of(tray, account: "bo@example.test"), Unsent(sends: 1, drafts: 1))
        XCTAssertEqual(Unsent.of(tray, account: "eve@example.test"), Unsent())
    }

    func testTheWarningSaysWhatGoesAndThatItIsOnlyHere() {
        XCTAssertNil(Unsent().words, "nothing waits, so nothing to warn of")
        XCTAssertEqual(Unsent(sends: 1).words, "This also deletes 1 message that hasn’t been sent. It’s only on this Mac.")
        XCTAssertEqual(Unsent(drafts: 2).words,
                       "This also deletes 2 drafts that aren’t on the server yet. They’re only on this Mac.")
        XCTAssertEqual(Unsent(sends: 3, drafts: 1).words, """
            This also deletes 3 messages that haven’t been sent and 1 draft that isn’t on the server yet. They’re only \
            on this Mac.
            """)
        XCTAssertEqual(RemoveAccount.backups, "Copies of its mail in MailMind’s backups stay until they’re replaced.")
    }
}
