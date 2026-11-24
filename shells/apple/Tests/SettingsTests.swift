import AppKit
import Foundation
import PDFKit
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

    /// An edit that changes only the signature sends no credential, whatever kind the account's is, so the server keeps
    /// it as it is: a Proton account's own Keychain item stays (the edit once sent the Bridge's in its place, and the
    /// server deleted the item). A Proton account with a password of its own says where it is kept, as its card does.
    func testAnEditOfOnlyTheSignatureKeepsEveryKindOfCredential() {
        let rows = [
            F.account("ada@example.test"),
            F.account("ada@example.test", type: "gmail"),
            F.account("ada@example.test", style: "op", ref: "op://Vault/Ada/password"),
            F.account("ada@example.test", style: "env", variable: "ADA_PASSWORD"),
            F.account("ada@example.test", type: "proton", style: "proton_bridge"),
            F.account("ada@example.test", type: "proton"),
            F.account("ada@example.test", type: "proton", style: "op", ref: "op://Vault/Bea/password"),
            F.account("ada@example.test", type: "proton", style: "env", variable: "BEA_PASSWORD"),
        ]
        for row in rows {
            let said = "\(row.type), \(row.credential.style ?? "none")"
            var draft = AccountDraft(editing: row)
            draft.signature = "Ada, by the sea"
            XCTAssertNil(draft.body.credential, said + ": the credential is kept as it is")
            XCTAssertEqual(draft.body.signature, "Ada, by the sea", said)
            XCTAssertNil(draft.problem, said)
            XCTAssertEqual(draft.tries(firstRun: false), .never, said + ": a plain edit tries no login")
        }

        let own = AccountDraft(editing: F.account("bea@example.test", type: "proton"))
        XCTAssertEqual(own.protonOwn?.style, "keychain", "its own Keychain item, as the card says")
        XCTAssertNil(AccountDraft(editing: F.account("bea@example.test", type: "proton", style: "proton_bridge")).protonOwn,
                     "the Bridge's password shows as the Bridge's")
        XCTAssertNil(AccountDraft(editing: F.account("bea@example.test")).protonOwn)

        var becomes = AccountDraft(editing: F.account("bea@example.test"))
        becomes.pick(.proton)
        XCTAssertEqual(becomes.body.credential, .object(["style": .string("proton_bridge")]),
                       "an account made Proton by the edit logs in with the Bridge's")
        XCTAssertNil(becomes.protonOwn)
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

    func testATestThatRunsSaysOnlyThatItIsTesting() {
        // A new account's password is in the Keychain, read with no prompt: there's no Touch ID to approve.
        XCTAssertEqual(TestedWords.testing, "Testing…")
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

    /// A test run runs only as it was started, so it has no switch: it says how it runs in the switch's place.
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
        XCTAssertEqual(Tested.kind(of: answer), "NETWORK", "the kind a sync that couldn't connect has")
        XCTAssertFalse(why.contains(Self.password))

        let removed = try await core.post(API.Post.AccountsDelete.self,
                                          API.RemoveBody(deleteMail: true, email: "ada@example.test"))
        XCTAssertEqual(removed.mailDeleted, 0, "its mail on this Mac went too, of which there was none")
        await status.readAccounts()
        XCTAssertTrue(status.firstRun)
        await close(core, status)
    }

    /// A signature edited in Settings signs a new message and a reply written right after Save, not only once the next
    /// sync reads who you are again: Save hands writing what was kept.
    @MainActor
    func testASignatureSavedSignsANewMessageAndAReplyRightAway() async throws {
        let (core, status) = try await opened()
        var draft = AccountDraft()
        draft.email = "ada@example.test"
        draft.signature = "Ada, at the example"
        draft.pick(.imap)
        draft.host = "127.0.0.1"
        draft.port = "9"
        draft.password = Self.password
        _ = try await SecretPost.post(core, API.Post.AccountsAdd.self, draft.body)
        await status.readAccounts()
        let composing = Composing()
        composing.use(core, digest: try await core.get(API.Get.Digest.self))
        composing.openWindow = { _ in }
        XCTAssertEqual(composing.signature("ada@example.test"), "Ada, at the example")

        var edit = AccountDraft(editing: try XCTUnwrap(status.accounts.first))
        edit.signature = "Ada Lovelace, engines"
        let saved = await AccountSave(routes: .over(core, status: status, composing: composing))
            .run(edit, firstRun: false) { _ in }
        XCTAssertEqual(saved, .kept)

        let new = try XCTUnwrap(composing.open(ComposeSeed()))
        XCTAssertEqual(new.body, "\n\n-- \nAda Lovelace, engines", "a new message signs with what was just kept")
        // A reply as `Composing.reply` opens one, once it has read the message it answers.
        let original = API.MessageOpened(account: "ada@example.test", attachments: [], blockedCssUrls: 0,
                                         blockedImages: 0, cc: "", date: "Mon", folder: "INBOX",
                                         fromAddr: "bo@example.test", fromName: "Bo Brown", hasHtml: false, key: 7,
                                         ok: true, replyTo: "", subject: "Plans", text: "Thursday?", threadId: nil,
                                         to: "ada@example.test", uid: 7)
        let reply = try XCTUnwrap(composing.open(Seeds.reply(original, all: false, mine: composing.mine)))
        XCTAssertEqual(reply.body, "\n\n-- \nAda Lovelace, engines", "and so does a reply")
        new.close(force: true)
        reply.close(force: true)
        await close(core, status)
    }

    /// A password typed to fix an account's login is tried as typed and never kept when it doesn't log in, over the
    /// one the account keeps; a server's fix, whose password the sheet never sees, is kept, then tried as kept (#51).
    @MainActor
    func testAFixThatDoesntLogInKeepsNoPasswordAndAServersFixIsKeptThenTried() async throws {
        let (core, status) = try await opened()
        var draft = AccountDraft()
        draft.email = "ada@example.test"
        draft.pick(.imap)
        draft.host = "127.0.0.1"
        draft.port = "9"
        draft.password = Self.password
        _ = try await SecretPost.post(core, API.Post.AccountsAdd.self, draft.body)
        await status.readAccounts()
        let row = try XCTUnwrap(status.accounts.first)
        let save = AccountSave(routes: .over(core, status: status))

        // Nothing listens on the discard port: the password typed doesn't log in.
        let typed = "fixture-new-password-9012"
        var fix = AccountDraft(editing: row, fixing: .password)
        fix.password = typed
        var shown: [Tested] = []
        let tried = await save.run(fix, firstRun: false) { shown.append($0) }
        XCTAssertEqual(tried, .notKept)
        guard case .failed(_, _, let detail)? = shown.last else { return XCTFail("a closed port logged in: \(shown)") }
        XCTAssertEqual(detail, "connection refused", "the draft as typed was tried")
        let stood = try String(contentsOf: keychain, encoding: .utf8)
        XCTAssertTrue(stood.contains(Self.password), "the password kept is still kept")
        XCTAssertFalse(stood.contains(typed), "one that didn't log in is never kept: Cancel leaves the account as it was")
        XCTAssertFalse(status.notice?.text.hasPrefix("Updated") == true, "nothing is said to be updated")

        // A server's fix is kept, then tried as kept; one that still doesn't log in stays kept, and isn't said to be.
        var server = AccountDraft(editing: row, fixing: .server)
        server.port = "7"
        let kept = await save.run(server, firstRun: false) { _ in }
        XCTAssertEqual(kept, .keptFailing)
        XCTAssertEqual(status.accounts.first?.port, 7, "kept, and read again")
        XCTAssertFalse(status.notice?.text.hasPrefix("Updated") == true, "said only once it logs in")
        XCTAssertTrue(try String(contentsOf: keychain, encoding: .utf8).contains(Self.password))
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
/// backups keep their copies: an upgrade's until newer ones replace them, Start Fresh's until it is deleted.
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
        XCTAssertEqual(RemoveAccount.backups, """
            Copies of its mail in MailMind’s backups folder stay there: newer backups replace them in time, and a copy \
            Start Fresh kept stays until you delete it.
            """, "a copy Start Fresh kept is never replaced, so the words don't say every copy is")
    }
}

/// An account whose login failed is fixed from its Sync card (#51): the trouble's button opens the Edit sheet on the
/// field that fixes it, worded for the account, and the card says each thing once.
final class AccountFixTests: XCTestCase {
    /// iCloud's preset, as the server says it for an address at its own domain.
    private let icloud = API.PresetFound(
        domain: "icloud.com", link: API.Link(url: "https://account.apple.com", words: "Make an app-specific password"),
        name: "iCloud", note: nil, ok: true, preset: .icloud,
        settings: API.PresetSettings(host: "imap.mail.me.com", type: "imap"), supported: true, sure: true)

    func testAPasswordTurnedDownIsFixedWithAnAppPasswordOrThePasswordAndAServerUnreachedWithTheServer() {
        func fix(_ kind: String, _ type: String, error: String? = nil, credential: String = "keychain",
                 appPasswords: Bool = false) -> AccountFix?
        {
            AccountFix.of(kind: kind, type: type, error: error, credential: credential, appPasswords: appPasswords)
        }
        XCTAssertEqual(fix("AUTH", "gmail", error: "LoginNeedsAppPassword"), .appPassword)
        XCTAssertEqual(fix("AUTH", "gmail", error: "LoginRefused"), .appPassword, "a held login may be a wrong one")
        XCTAssertNil(fix("AUTH", "gmail", error: "LoginNeedsWebSignIn"), "signed in on the web, not in the sheet")
        XCTAssertNil(fix("AUTH", "gmail", error: "LoginImapOff"), "IMAP is turned on in Gmail, not in the sheet")
        XCTAssertEqual(fix("AUTH", "imap"), .password)
        XCTAssertEqual(fix("AUTH", "imap", appPasswords: true), .appPassword)
        XCTAssertNil(fix("AUTH", "imap", credential: "op"), "1Password's item is changed in 1Password")
        XCTAssertNil(fix("AUTH", "gmail", credential: "env"), "a variable is changed where it is set")
        XCTAssertNil(fix("AUTH", "proton", credential: "proton_bridge"), "Bridge's password has a card of its own")
        XCTAssertEqual(fix("NETWORK", "imap"), .server)
        XCTAssertNil(fix("NETWORK", "gmail"), "Gmail's servers aren't typed")
        for kind in ["TIMEOUT", "UIDVALIDITY", "TLS_PIN", "IMAP", "UNKNOWN"] {
            XCTAssertNil(fix(kind, "imap"), "\(kind): nothing in the sheet fixes it")
        }

        XCTAssertEqual(AccountFix.appPassword.button, "Enter App Password…")
        XCTAssertEqual(AccountFix.password.button, "Change Password…")
        XCTAssertEqual(AccountFix.server.button, "Edit Server…")
    }

    func testNoPasswordKeptIsFixedAsATurnedDownOneIsAndSaysTheKeychainHoldsNone() {
        func fix(_ type: String, credential: String = "keychain", appPasswords: Bool = false) -> AccountFix? {
            AccountFix.of(kind: "NO_PASSWORD", type: type, error: "NoCredential", credential: credential,
                          appPasswords: appPasswords)
        }
        XCTAssertEqual(fix("imap"), .password)
        XCTAssertEqual(fix("imap", appPasswords: true), .appPassword)
        XCTAssertEqual(fix("gmail"), .appPassword)
        XCTAssertNil(fix("imap", credential: "env"), "a variable is set where it is set")
        XCTAssertNil(fix("gmail", credential: "op"), "1Password's item is kept in 1Password")
        XCTAssertNil(fix("proton", credential: "proton_bridge"), "Bridge's password has a card of its own")
        XCTAssertTrue(AccountDetail.wantsPassword("NO_PASSWORD") && AccountDetail.wantsPassword("AUTH"))
        XCTAssertFalse(AccountDetail.wantsPassword("NETWORK") || AccountDetail.wantsPassword(nil))

        XCTAssertEqual(StatusWords.syncProblem("NO_PASSWORD", type: "imap", error: "NoCredential", fix: .password), """
            MailMind has no password for this account: this Mac’s Keychain doesn’t hold one. Choose Change Password \
            and type it again.
            """)
        XCTAssertTrue(StatusWords.syncProblem("NO_PASSWORD", type: "imap", fix: .appPassword)
            .contains("choose Enter App Password"))
        XCTAssertTrue(StatusWords.syncProblem("NO_PASSWORD", type: "gmail", fix: .appPassword)
            .contains("Make a Gmail App Password, then choose Enter App Password"))
        let proton = StatusWords.syncProblem("NO_PASSWORD", type: "proton")
        XCTAssertTrue(proton.hasPrefix("MailMind has no Proton Bridge password"), proton)
        XCTAssertTrue(proton.contains("under Proton Bridge password"), proton)
        let elsewhere = StatusWords.syncProblem("NO_PASSWORD", type: "imap")
        XCTAssertTrue(elsewhere.contains("nothing is kept where it reads this account’s password"), elsewhere)
        for type in ["gmail", "imap", "proton"] {
            for fix in [nil, AccountFix.appPassword, .password] {
                let said = StatusWords.syncProblem("NO_PASSWORD", type: type, error: "NoCredential", fix: fix)
                XCTAssertFalse(said.contains("turned down") || said.contains("didn’t accept"),
                               "no server refused anything: \(said)")
            }
        }
        XCTAssertEqual(StatusWords.syncHelp("NO_PASSWORD", type: "proton"), StatusWords.protonBridge)
        XCTAssertEqual(StatusWords.syncHelp("NO_PASSWORD", type: "gmail"), StatusWords.appPasswords)
        XCTAssertEqual(StatusWords.syncHelp("NO_PASSWORD", type: "imap", provider: icloud.link), icloud.link)
        XCTAssertNil(StatusWords.syncHelp("NO_PASSWORD", type: "imap"))
    }

    func testAProvidersAppPasswordPageIsOfferedOnlyWhereTheAccountReadsItsServer() {
        XCTAssertEqual(AccountFix.providerPage(icloud, host: "imap.mail.me.com"), icloud.link)
        XCTAssertEqual(AccountFix.providerPage(icloud, host: "IMAP.mail.me.com"), icloud.link, "a name in any case")
        XCTAssertNil(AccountFix.providerPage(icloud, host: "imap.example.test"),
                     "a custom domain's mail may be any provider's")
        XCTAssertNil(AccountFix.providerPage(nil, host: "imap.mail.me.com"))
        let gmail = API.PresetFound(
            domain: "example.test", link: StatusWords.appPasswords, name: "Gmail", ok: true, preset: .gmail,
            settings: API.PresetSettings(host: "imap.gmail.com", type: "gmail"), supported: true, sure: false)
        XCTAssertNil(AccountFix.providerPage(gmail, host: "imap.gmail.com"), "an IMAP account isn't Gmail's preset")
    }

    func testEachTroubleTheSheetFixesNamesItsButtonAndNoOtherSaysChooseEdit() {
        XCTAssertEqual(StatusWords.syncProblem("AUTH", type: "gmail", error: "LoginNeedsAppPassword", fix: .appPassword),
                       """
                       Gmail needs an App Password, not your Google password: make one, then choose Enter App \
                       Password and paste it.
                       """)
        XCTAssertTrue(StatusWords.syncProblem("AUTH", type: "gmail", error: "LoginRefused", fix: .appPassword)
            .contains("choose Enter App Password"))
        XCTAssertTrue(StatusWords.syncProblem("AUTH", type: "imap", fix: .password).contains("choose Change Password"))
        XCTAssertTrue(StatusWords.syncProblem("AUTH", type: "imap", fix: .appPassword)
            .contains("choose Enter App Password"))
        XCTAssertTrue(StatusWords.syncProblem("NETWORK", type: "imap", fix: .server).contains("under Edit Server"))
        // A password read from 1Password or a variable is changed there, and the card says so.
        let elsewhere = StatusWords.syncProblem("AUTH", type: "imap")
        XCTAssertTrue(elsewhere.contains("where MailMind reads this account’s password"), elsewhere)
        for kind in ["AUTH", "NETWORK", "TIMEOUT", "UIDVALIDITY", "TLS_PIN", "IMAP", "UNKNOWN"] {
            for type in ["gmail", "imap", "proton"] {
                for fix in [nil, AccountFix.appPassword, .password, .server] {
                    let said = StatusWords.syncProblem(kind, type: type, error: "LoginRefused", fix: fix)
                    XCTAssertFalse(said.contains("choose Edit and"), "\(kind) \(type): \(said)")
                }
            }
        }
        // An Other IMAP account's provider's page goes with a password it turned down, and nothing else.
        XCTAssertEqual(StatusWords.syncHelp("AUTH", type: "imap", provider: icloud.link), icloud.link)
        XCTAssertNil(StatusWords.syncHelp("NETWORK", type: "imap", provider: icloud.link))
        XCTAssertEqual(StatusWords.syncHelp("AUTH", type: "gmail", error: "LoginRefused", provider: icloud.link),
                       StatusWords.appPasswords, "Gmail's page is Google's")
    }

    func testTheSheetOpenedForAPasswordAsksForANewOneAndFocusesItsField() {
        let gmail = StatusFixtures.account("cy@example.test", type: "gmail")
        let fixing = AccountDraft(editing: gmail, fixing: .appPassword)
        XCTAssertTrue(fixing.asksPassword)
        XCTAssertEqual(fixing.passwordPrompt, "abcd efgh ijkl mnop", "an App Password's shape, not 'unchanged'")
        XCTAssertEqual(fixing.problem, "Paste the new app password.", "the kept one is what failed: Save asks again")
        XCTAssertEqual(AccountFormField.fixing(fixing.fixing), .password)

        let plain = AccountDraft(editing: gmail)
        XCTAssertFalse(plain.asksPassword)
        XCTAssertEqual(plain.passwordPrompt, "unchanged — leave blank to keep")
        XCTAssertNil(plain.problem, "a plain edit keeps the kept password")
        XCTAssertNil(AccountFormField.fixing(plain.fixing))

        let imap = StatusFixtures.account("ada@example.test")
        var typing = AccountDraft(editing: imap, fixing: .password)
        XCTAssertEqual(typing.passwordPrompt, "type the password")
        XCTAssertEqual(typing.problem, "Type the account’s password.")
        typing.password = "fixture-password-5678"
        XCTAssertNil(typing.problem)
        var dumped = ""
        dump(typing, to: &dumped)
        XCTAssertTrue(dumped.contains("fixing"), dumped)
        XCTAssertFalse(dumped.contains("5678"), "a typed password is never described")
        XCTAssertEqual(AccountDraft(editing: imap, fixing: .appPassword).passwordPrompt, "paste the new app password")

        let server = AccountDraft(editing: imap, fixing: .server)
        XCTAssertFalse(server.asksPassword)
        XCTAssertNil(server.problem, "a server's fix keeps the kept password")
        XCTAssertEqual(AccountFormField.fixing(server.fixing), .server)

        XCTAssertNil(AccountDraft(fixing: .password).fixing, "a new account has nothing to fix")
    }

    func testATestsTroubleIsTheKindASyncWouldSayFromTheClassTheCoreGivesIt() {
        func failed(_ error: String, says: String = "It didn't work.") -> API.AccountsTestAnswer {
            .couldntLogIn(API.CouldntLogIn(connect: false, email: nil, error: error, ok: true, says: says))
        }
        XCTAssertEqual(Tested.kind(of: failed("AUTHENTICATIONFAILED")), "AUTH")
        XCTAssertEqual(Tested.kind(of: failed("no password")), "NO_PASSWORD", "none kept, as the sync found")
        XCTAssertEqual(Tested.kind(of: failed("connection refused")), "NETWORK")
        XCTAssertEqual(Tested.kind(of: failed("host not found")), "NETWORK")
        XCTAssertEqual(Tested.kind(of: failed("timeout")), "TIMEOUT")
        XCTAssertEqual(Tested.kind(of: failed("certificate changed")), "TLS_PIN")
        let gmail = "Gmail needs an App Password here."
        XCTAssertEqual(Tested.kind(of: failed(gmail, says: gmail)), "AUTH", "Google's own words are its refusal")
        XCTAssertNil(Tested.kind(of: failed("smtp refused")), "no sync has an outgoing server's trouble")
        XCTAssertNil(Tested.kind(of: .loggedIn(API.LoggedIn(connect: true, detail: "", email: "ada@example.test", ms: 3, ok: true))))
    }

    /// Under a test's sentence, its trouble in plain words, as the Sync card says it: never the server's code or an
    /// error's name. Each class either server's test gives has words; the outgoing server's say it is that one.
    func testATestsTroubleIsSaidInTheSyncCardsPlainWordsNeverItsCode() throws {
        XCTAssertEqual(Tested.plainWords(of: "AUTHENTICATIONFAILED"), "The server turned down the password.")
        XCTAssertTrue(StatusWords.syncProblem("AUTH").hasPrefix("The server turned down the password"),
                      "the Sync card's words for a password turned down")
        let logins = ["AUTHENTICATIONFAILED", "no password", "connection refused", "host not found", "timeout",
                      "certificate changed", "SSLCertVerificationError", "SSLError", "abort", "ConnectionResetError"]
        for detail in logins + ["port must be 1–65535", "SMTP port must be 1–65535", "SMTP NoSmtpServer"] {
            let words = try XCTUnwrap(Tested.plainWords(of: detail), detail)
            for code in ["AUTHENTICATIONFAILED", "SSL", "abort", "timeout", "refused", "–65535", "Error", "Smtp"] {
                XCTAssertFalse(words.contains(code), "\(detail): \(words)")
            }
        }
        for detail in logins.map({ "SMTP " + $0 }) {
            let words = try XCTUnwrap(Tested.plainWords(of: detail), detail)
            XCTAssertTrue(words.contains("outgoing server"), "\(detail): \(words)")
            XCTAssertFalse(words.contains("SMTP"), "\(detail): \(words)")
        }
        // Another error's name, which no person needs, says nothing; nor does no class, as Gmail's sentences have.
        XCTAssertNil(Tested.plainWords(of: "ValueError"))
        XCTAssertNil(Tested.plainWords(of: nil))
    }

    func testATestThatFailsAsTheSyncDidSaysOnlyThatAndOneThatDiffersSaysWhatIsNew() {
        let page = StatusWords.appPasswords
        let wrong = Tested.failed("Gmail needs an App Password here.", link: page, detail: "AUTH-ish")
        XCTAssertEqual(TestedOnCard.of(wrong, kind: "AUTH", standing: "AUTH", type: "gmail", page: page), .same,
                       "nothing new: not the sentence, the page or the class again")
        XCTAssertEqual(CardTestedWords.same, "Still failing the same way.")

        // The sync couldn't reach the server and the test was turned down: the test's own words, and its page.
        XCTAssertEqual(TestedOnCard.of(wrong, kind: "AUTH", standing: "NETWORK", type: "gmail", page: nil),
                       .other("Gmail needs an App Password here.", link: page))
        // Turned down both times, but the test's words want a sign-in, where the card says to make a password.
        let signIn = Tested.failed("Sign in to Gmail on the web first.", link: nil, detail: nil)
        XCTAssertEqual(TestedOnCard.of(signIn, kind: "AUTH", standing: "AUTH", type: "gmail", page: page),
                       .other("Sign in to Gmail on the web first.", link: nil))
        // A different trouble whose page the card already shows: its sentence, not the page a second time.
        XCTAssertEqual(TestedOnCard.of(wrong, kind: "TIMEOUT", standing: "AUTH", type: "gmail", page: page),
                       .other("Gmail needs an App Password here.", link: nil))
        // A Proton test points to Bridge's page, which the card's Bridge password stands for.
        let bridge = Tested.failed("Bridge said no.", link: StatusWords.protonBridge, detail: nil)
        XCTAssertEqual(TestedOnCard.of(bridge, kind: "AUTH", standing: "AUTH", type: "proton", page: nil), .same)
        // A card with no trouble shows the test as it is.
        XCTAssertEqual(TestedOnCard.of(wrong, kind: "AUTH", standing: nil, type: "gmail", page: nil),
                       .other("Gmail needs an App Password here.", link: page))
        XCTAssertEqual(TestedOnCard.of(.connected, kind: nil, standing: "AUTH", type: "gmail", page: page), .connected)
        XCTAssertEqual(TestedOnCard.of(.running, kind: nil, standing: "AUTH", type: "gmail", page: page), .running)
    }

    func testSaveTriesALoginAsTypedWhereItCanAndOnceKeptWhereItCant() {
        var adding = AccountDraft()
        adding.email = "ada@example.test"
        adding.pick(.imap)
        XCTAssertEqual(adding.tries(firstRun: false), .never, "an account added from Settings is tested on its card")
        XCTAssertEqual(adding.tries(firstRun: true), .typed)
        adding.pick(.proton)
        XCTAssertEqual(adding.tries(firstRun: true), .kept, "Bridge's password is tried only with a saved account")

        let cy = StatusFixtures.account("cy@example.test", type: "gmail")
        XCTAssertEqual(AccountDraft(editing: cy).tries(firstRun: false), .never, "a plain edit")
        var password = AccountDraft(editing: cy, fixing: .appPassword)
        password.password = "fixture-app-password-0001"
        XCTAssertEqual(password.tries(firstRun: false), .typed, "a password typed to fix the login")
        let ada = StatusFixtures.account("ada@example.test")
        XCTAssertEqual(AccountDraft(editing: ada, fixing: .server).tries(firstRun: false), .kept,
                       "a server's fix: the sheet never sees the password kept")
        let elsewhere = StatusFixtures.account("news@example.test", style: "op", ref: "op://Private/News/password")
        XCTAssertEqual(AccountDraft(editing: elsewhere, fixing: .password).tries(firstRun: false), .kept,
                       "nothing typed to try")
    }

    func testAnEditedAccountsPasswordIsTriedWhereItReadsItsMail() throws {
        // A Gmail account whose file names its servers (as a test's world does) is tried there, not at Google's.
        var gmail = AccountDraft(editing: StatusFixtures.account("cy@example.test", type: "gmail"),
                                 fixing: .appPassword)
        gmail.password = "fixture-app-password-0001"
        let tried = try XCTUnwrap(gmail.test?.draft)
        XCTAssertEqual(tried.host, .string("imap.example.test"))
        XCTAssertEqual(tried.port, .number(993))
        XCTAssertEqual(tried.smtpHost, .string("smtp.example.test"), "and sends where it sends")
        XCTAssertEqual(tried.smtpPort, .number(465))
        XCTAssertNil(gmail.body.host, "the edit keeps the account on the servers it has")
        // An Other IMAP account made a Gmail one is tried at Google's.
        var picked = AccountDraft(editing: StatusFixtures.account("ada@example.test"))
        picked.pick(.gmail)
        picked.password = "fixture-app-password-0001"
        XCTAssertNil(try XCTUnwrap(picked.test?.draft).host)

        // A new Gmail account's servers are its type's.
        var adding = AccountDraft()
        adding.email = "cy@example.test"
        adding.pick(.gmail)
        adding.password = "fixture-app-password-0001"
        XCTAssertNil(try XCTUnwrap(adding.test?.draft).host)
    }

    @MainActor
    func testAPasswordTypedToFixALoginIsTriedFirstAndKeptOnlyOnceItLogsIn() async throws {
        let email = "cy@example.test"
        let cy = StatusFixtures.account(email, type: "gmail")
        var draft = AccountDraft(editing: cy, fixing: .appPassword)
        draft.password = "fixture-app-password-0001"
        let trial = try XCTUnwrap(draft.test)
        XCTAssertNotNil(trial.draft?.credential, "the password typed is tried, not the one kept")
        let turnedDown = Tested.failed("Gmail didn't take it.", link: StatusWords.appPasswords)

        let wrong = SaveRoutes(tests: [turnedDown])
        var shown: [Tested] = []
        let notKept = await AccountSave(routes: wrong.routes).run(draft, firstRun: false) { shown.append($0) }
        XCTAssertEqual(notKept, .notKept)
        XCTAssertEqual(wrong.calls, [.test(trial)], "nothing kept, said or synced: Cancel leaves the account as it was")
        XCTAssertEqual(shown, [.running, turnedDown])

        let right = SaveRoutes(tests: [.connected])
        let kept = await AccountSave(routes: right.routes).run(draft, firstRun: false) { _ in }
        XCTAssertEqual(kept, .kept)
        XCTAssertEqual(right.calls, [.test(trial), .keep(draft.body, adding: false), .say("Updated cy"), .kept(email),
                                     .retry(email)], "kept once it logs in, then synced again, which clears the trouble")
    }

    @MainActor
    func testAFixedPasswordThatReadsTheMailIsKeptWhenOnlyTheOutgoingServerTurnsItDown() async throws {
        let email = "ada@example.test"
        let ada = StatusFixtures.account(email)
        var draft = AccountDraft(editing: ada, fixing: .password)
        draft.password = "fixture-password-0002"
        let trial = try XCTUnwrap(draft.test)
        let sending = Tested.failed("Reading mail works, but the outgoing server didn't accept the password.",
                                    detail: "SMTP AUTHENTICATIONFAILED")
        XCTAssertTrue(sending.sendingOnly)
        XCTAssertFalse(Tested.failed("No.", detail: "AUTHENTICATIONFAILED").sendingOnly)

        let routes = SaveRoutes(tests: [sending])
        let outcome = await AccountSave(routes: routes.routes).run(draft, firstRun: false) { _ in }
        XCTAssertEqual(outcome, .keptFailing, "the sheet stays on the outgoing server's words")
        XCTAssertEqual(routes.calls, [.test(trial), .keep(draft.body, adding: false), .say("Updated ada"), .kept(email),
                                      .retry(email)], "the login the sync was refused works: kept, and synced again")

        var adding = AccountDraft()
        adding.email = "bo@example.test"
        adding.pick(.imap)
        adding.host = "imap.example.test"
        adding.password = "fixture-password-0003"
        XCTAssertNotNil(adding.test)
        let first = SaveRoutes(tests: [sending])
        let added = await AccountSave(routes: first.routes).run(adding, firstRun: true) { _ in }
        XCTAssertEqual(added, .notKept, "a first account is still kept only once it sends too")
    }

    @MainActor
    func testAServersFixIsKeptThenTriedAndSaidToBeUpdatedOnlyOnceItLogsIn() async {
        let email = "ada@example.test"
        let ada = StatusFixtures.account(email)
        var draft = AccountDraft(editing: ada, fixing: .server)
        draft.host = "imap2.example.test"
        let unreached = Tested.failed("The server didn't answer.", detail: "connection refused")

        let failing = SaveRoutes(tests: [unreached])
        let stillFailing = await AccountSave(routes: failing.routes).run(draft, firstRun: false) { _ in }
        XCTAssertEqual(stillFailing, .keptFailing, "kept: the sheet's Cancel is Done")
        XCTAssertEqual(failing.calls, [.keep(draft.body, adding: false), .kept(email), .test(API.TryBody(email: email))],
                       "not said to be updated while the sheet says it still fails, and not synced")

        let working = SaveRoutes(tests: [.connected])
        let fixed = await AccountSave(routes: working.routes).run(draft, firstRun: false) { _ in }
        XCTAssertEqual(fixed, .kept)
        XCTAssertEqual(working.calls, [.keep(draft.body, adding: false), .kept(email), .test(API.TryBody(email: email)),
                                       .say("Updated ada"), .retry(email)])
    }

    @MainActor
    func testAPlainEditTheFirstRunAndARefusalSaveAsTheyDid() async throws {
        let ada = StatusFixtures.account("ada@example.test")
        var plain = AccountDraft(editing: ada)
        plain.alias = "Ada"
        let edit = SaveRoutes(tests: [])
        let edited = await AccountSave(routes: edit.routes).run(plain, firstRun: false) { _ in }
        XCTAssertEqual(edited, .kept)
        XCTAssertEqual(edit.calls, [.keep(plain.body, adding: false), .say("Updated ada"), .kept("ada@example.test")],
                       "no test: its card tests it")

        var first = AccountDraft()
        first.email = "bo@example.test"
        first.pick(.imap)
        first.host = "imap.example.test"
        first.password = "fixture-password-0002"
        let trial = try XCTUnwrap(first.test)
        let refused = SaveRoutes(tests: [.failed("No.")])
        let notAdded = await AccountSave(routes: refused.routes).run(first, firstRun: true) { _ in }
        XCTAssertEqual(notAdded, .notKept)
        XCTAssertEqual(refused.calls, [.test(trial)])

        var proton = AccountDraft()
        proton.email = "pat@example.test"
        proton.pick(.proton)
        let bridge = SaveRoutes(tests: [.failed("Bridge isn't running.")])
        let added = await AccountSave(routes: bridge.routes).run(proton, firstRun: true) { _ in }
        XCTAssertEqual(added, .keptFailing, "added, then tested: the sheet stays open until Done")
        XCTAssertEqual(bridge.calls, [.keep(proton.body, adding: true), .say("Added pat"), .kept("pat@example.test"),
                                      .test(API.TryBody(email: "pat@example.test"))])

        let turnedDown = SaveRoutes(tests: [])
        turnedDown.refusal = Problem(CoreAnswer(status: 400, contentType: "application/problem+json", headers: [],
                                                body: Data(#"{"detail": "That port isn't a number."}"#.utf8)))
        let said = await AccountSave(routes: turnedDown.routes).run(plain, firstRun: false) { _ in }
        XCTAssertEqual(said, .refused("That port isn't a number."))
        XCTAssertEqual(turnedDown.calls, [.keep(plain.body, adding: false)])
    }

    func testTheSheetDoesntSayAgainWhatThePasswordsRowSays() {
        let page = StatusWords.appPasswords
        let cy = StatusFixtures.account("cy@example.test", type: "gmail")
        let gmail = AccountDraft(editing: cy, fixing: .appPassword)
        XCTAssertEqual(gmail.passwordPage, AccountDraft.appPasswords.absoluteString, "under Gmail's steps")
        // A Google password, a wrong App Password, a password Gmail's server couldn't take: the steps say the rest.
        for refusal in [Tested.failed("Gmail needs an App Password here, not your Google password.", link: page),
                        .failed("Gmail didn't accept that App Password.", link: page)] {
            XCTAssertEqual(gmail.inSheet(refusal), .failed(AccountDraft.gmailTurnedDown, link: nil), "\(refusal)")
        }
        XCTAssertEqual(gmail.inSheet(.failed("Gmail didn't accept that password.", link: page,
                                             detail: "AUTHENTICATIONFAILED")),
                       .failed(AccountDraft.gmailTurnedDown, link: nil, detail: nil),
                       "its kind isn't said again under the sentence that says it")
        let signIn = Tested.failed("Google wants you to sign in on the web first.", link: nil)
        XCTAssertEqual(gmail.inSheet(signIn), signIn, "no App Password fixes it: its own words")
        XCTAssertEqual(gmail.inSheet(.connected), .connected)
        XCTAssertEqual(gmail.inSheet(.running), .running)

        // A password read from 1Password shows no steps: the page is the test's to show.
        let kept = StatusFixtures.account("cy@example.test", type: "gmail", style: "op", ref: "op://Private/Cy/password")
        let elsewhere = AccountDraft(editing: kept)
        XCTAssertNil(elsewhere.passwordPage)
        let refusal = Tested.failed("Gmail needs an App Password here.", link: page)
        XCTAssertEqual(elsewhere.inSheet(refusal), refusal)

        // A new iCloud account's row shows Apple's page: a test's sentence, not the page again.
        var icloud = AccountDraft()
        icloud.email = "ada@icloud.com"
        icloud.take(self.icloud)
        let apple = self.icloud.link
        XCTAssertEqual(icloud.passwordPage, apple?.url)
        XCTAssertEqual(icloud.inSheet(.failed("iCloud didn't accept that password.", link: apple,
                                              detail: "AUTHENTICATIONFAILED")),
                       .failed("iCloud didn't accept that password.", link: nil, detail: nil))
        // An edited Other IMAP account's row shows no page: the test's stays.
        let ada = AccountDraft(editing: StatusFixtures.account("ada@example.test"))
        XCTAssertEqual(ada.inSheet(.failed("No.", link: apple)), .failed("No.", link: apple))
    }
}

extension AccountFixTests {
    /// A wrong password at an Other IMAP account is said once: the core's sentence, never "The server turned down the
    /// password" again under it. A kind of trouble the core says only in general, a connection lost, keeps its plain
    /// words in small type.
    func testTheSheetSaysATestsTroubleOnce() {
        let ada = AccountDraft(editing: StatusFixtures.account("ada@example.test"))
        let wrong = Tested.failed("The server didn't accept that address and password. Check them; if your provider "
                                  + "offers app passwords, make one and paste it here.", detail: "AUTHENTICATIONFAILED")
        XCTAssertEqual(ada.inSheet(wrong), .failed("The server didn't accept that address and password. Check them; if "
                                                   + "your provider offers app passwords, make one and paste it here."))
        for said in ["no password", "SMTP AUTHENTICATIONFAILED", "connection refused", "timeout", "host not found",
                     "certificate changed", "SSLCertVerificationError", "SMTP SSLError", "SMTP port must be 1–65535"] {
            XCTAssertTrue(Tested.saidInFull(said), said)
        }
        let lost = Tested.failed("MailMind couldn't log in. Check the address, the password and the server's settings.",
                                 detail: "ConnectionResetError")
        XCTAssertEqual(ada.inSheet(lost), lost, "the general sentence: what kind of trouble it was stays")
        XCTAssertEqual(Tested.plainWords(of: "ConnectionResetError"), "The connection to the server was lost.")
        XCTAssertFalse(Tested.saidInFull(nil))
    }

    /// An address MailMind has an account for already is said so as it is typed, and Save doesn't send it: both
    /// servers would turn it down. Editing an account is no such thing.
    func testAnAddressAlreadySetUpIsSaidSo() {
        var draft = AccountDraft()
        draft.email = " Ada@Example.test "
        XCTAssertEqual(draft.alreadySetUp(among: ["ada@example.test", "bo@example.test"]),
                       "Ada@Example.test is already set up in MailMind.")
        XCTAssertNil(draft.alreadySetUp(among: ["bo@example.test"]))
        draft.email = "ada"
        XCTAssertNil(draft.alreadySetUp(among: ["ada"]), "not an address yet")
        let editing = AccountDraft(editing: StatusFixtures.account("ada@example.test"))
        XCTAssertNil(editing.alreadySetUp(among: ["ada@example.test"]))
    }
}

/// The account sheet's form scrolls to its foot as a test's words come under it, so where the sheet can't grow past
/// its window, its last row (the signature) stays whole, never cut in two.
@MainActor
final class FootInSightTests: XCTestCase {
    @Observable
    final class Said {
        var words = ""
    }

    /// A form taller than its sheet, and the words that come under it.
    struct Sheet: View {
        let said: Said

        var body: some View {
            VStack(spacing: 0) {
                FootInSight(change: said.words) {
                    Color.clear.frame(height: 900)
                }
                if !said.words.isEmpty { Text(said.words).frame(height: 80) }
            }
            .frame(width: 400, height: 300)
        }
    }

    func testTheFormScrollsToItsFootAsWordsComeUnderIt() async throws {
        let said = Said()
        let host = NSHostingView(rootView: Sheet(said: said))
        host.frame = CGRect(x: 0, y: 0, width: 400, height: 300)
        let window = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.contentView = host
        defer {
            window.contentView = nil
            window.close()
        }
        window.layoutIfNeeded()
        let scroll = try XCTUnwrap(Self.find(NSScrollView.self, in: host), "the form scrolls")
        func shown() -> NSRect { scroll.documentVisibleRect }
        func height() -> CGFloat { scroll.documentView?.frame.height ?? 0 }
        XCTAssertEqual(shown().minY, 0, accuracy: 1, "it opens at its head")

        said.words = "The server didn't answer."
        for _ in 0..<100 where abs(shown().maxY - height()) > 1 {
            host.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertEqual(shown().maxY, height(), accuracy: 1, "its foot in sight, whole")
    }

    private static func find<V: NSView>(_ kind: V.Type, in view: NSView) -> V? {
        if let found = view as? V { return found }
        for child in view.subviews {
            if let found = find(kind, in: child) { return found }
        }
        return nil
    }
}

/// The routes Save goes through, each call written down, answered as a test says.
@MainActor
private final class SaveRoutes {
    enum Call: Equatable {
        case test(API.TryBody)
        case keep(API.AccountBody, adding: Bool)
        case kept(String)
        case say(String)
        case retry(String)
    }

    private(set) var calls: [Call] = []
    /// What each test says, in turn; connected once they run out.
    private var tests: [Tested]
    /// What keeping the account throws, if anything.
    var refusal: (any Error)?

    init(tests: [Tested]) {
        self.tests = tests
    }

    var routes: AccountRoutes {
        AccountRoutes(
            test: { [self] body in
                calls.append(.test(body))
                return tests.isEmpty ? .connected : tests.removeFirst()
            },
            keep: { [self] body, adding in
                calls.append(.keep(body, adding: adding))
                if let refusal { throw refusal }
            },
            kept: { [self] address, _ in calls.append(.kept(address)) },
            say: { [self] in calls.append(.say($0)) },
            retry: { [self] in calls.append(.retry($0)) })
    }
}

/// The Sync card and the Edit sheet as drawn (#51): the card's trouble said once with its button, no class in it, and
/// the sheet opened from that button with the focus in its emptied password field.
@MainActor
final class AccountCardTests: XCTestCase {
    private let now = StatusFixtures.at("2026-11-16T12:00:00Z")

    private func status(_ account: API.AccountRow, error: String, kind: String) -> AppStatus {
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        let email = account.email ?? ""
        status.hold(digest: StatusFixtures.digest(accounts: [email]), accounts: [account],
                    health: [StatusFixtures.standing(email, ok: "2026-11-16T09:00:00Z", error: error, kind: kind,
                                                     errorAt: "2026-11-16T11:00:00Z", count: 3)],
                    now: now)
        return status
    }

    func testAGmailLoginTurnedDownSaysItOnceAndOffersEnterAppPassword() throws {
        let cy = StatusFixtures.account("cy@example.test", type: "gmail")
        var asked: [AccountFix] = []
        let shown = Shown(HealthRow(status: status(cy, error: "LoginNeedsAppPassword", kind: "AUTH"),
                                    email: "cy@example.test", fix: { asked.append($0) }))
        defer { shown.close() }
        let said = shown.words
        XCTAssertEqual(said, """
            Couldn’t sync · 60m ago Gmail needs an App Password, not your Google password: make one, then choose \
            Enter App Password and paste it. Make an App Password Enter App Password… Retry
            """, "each thing once, and no class")
        XCTAssertFalse(said.contains("LoginNeedsAppPassword"), "Copy Diagnostics carries the class")
        XCTAssertFalse(said.contains("AUTH"))
        XCTAssertTrue(shown.press("Enter App Password…"))
        XCTAssertEqual(asked, [.appPassword], "it opens the sheet for an App Password")
    }

    func testAnOtherIMAPPasswordTurnedDownOffersChangePasswordUnlessItIsReadElsewhere() throws {
        let ada = StatusFixtures.account("ada@example.test")
        var asked: [AccountFix] = []
        let shown = Shown(HealthRow(status: status(ada, error: "LOGIN refused", kind: "AUTH"),
                                    email: "ada@example.test", fix: { asked.append($0) }))
        defer { shown.close() }
        XCTAssertEqual(shown.count("Change Password…"), 1, shown.words)
        XCTAssertFalse(shown.words.contains("LOGIN refused"), "no class: \(shown.words)")
        XCTAssertTrue(shown.press("Change Password…"))
        XCTAssertEqual(asked, [.password])

        let kept = StatusFixtures.account("news@example.test", style: "op", ref: "op://Private/News/password")
        let elsewhere = Shown(HealthRow(status: status(kept, error: "LOGIN refused", kind: "AUTH"),
                                        email: "news@example.test"))
        defer { elsewhere.close() }
        XCTAssertEqual(elsewhere.count("Password…"), 0, "1Password's item is changed in 1Password: \(elsewhere.words)")
        XCTAssertTrue(elsewhere.words.contains("where MailMind reads this account’s password"), elsewhere.words)
    }

    func testATestThatFailedTheSameWaySaysOnlyThat() {
        let same = Shown(CardTestedWords(shown: .same))
        defer { same.close() }
        XCTAssertEqual(same.words.trimmingCharacters(in: .whitespaces), "Still failing the same way.")
        let other = Shown(CardTestedWords(shown: .other("The server didn't answer.", link: nil)))
        defer { other.close() }
        XCTAssertEqual(other.words.trimmingCharacters(in: .whitespaces), "The server didn't answer.",
                       "its own sentence, and no class")
    }

    func testTheSheetOpenedToFixAPasswordHasTheFocusInItsEmptyPasswordField() async throws {
        for (account, fix) in [(StatusFixtures.account("ada@example.test"), AccountFix.password),
                               (StatusFixtures.account("cy@example.test", type: "gmail"), .appPassword)] {
            let status = status(account, error: "LOGIN refused", kind: "AUTH")
            let shown = Shown(AccountForm(status: status, draft: AccountDraft(editing: account, fixing: fix),
                                          done: { _ in }),
                              size: CGSize(width: 560, height: 640))
            defer { shown.close() }
            await deskEventually { shown.focusedField is NSSecureTextField }
            let field = try XCTUnwrap(shown.focusedField as? NSSecureTextField, "\(account.type): \(String(describing: shown.focusedField))")
            XCTAssertEqual(field.stringValue, "", "empty, for the new one")
        }
    }

    func testTheSheetOpenedToFixAServerHasTheFocusInItsIncomingServer() async throws {
        let ada = StatusFixtures.account("ada@example.test")
        let shown = Shown(AccountForm(status: status(ada, error: "connection refused", kind: "NETWORK"),
                                      draft: AccountDraft(editing: ada, fixing: .server), done: { _ in }),
                          size: CGSize(width: 560, height: 640))
        defer { shown.close() }
        await deskEventually { (shown.focusedField as? NSTextField)?.stringValue == "imap.example.test" }
    }

    func testTheSheetSaysAGmailRefusalOnceUnderItsStepsWithOneLinkToGoogle() {
        let cy = StatusFixtures.account("cy@example.test", type: "gmail")
        // The core's answer to a Google password at Gmail, with Google's page.
        let refusal = Tested.failed("""
            Gmail needs an App Password here, not your Google password. If Google offers none, Advanced Protection or \
            a Workspace admin has switched them off.
            """, link: StatusWords.appPasswords)
        let shown = Shown(AccountForm(status: status(cy, error: "LoginNeedsAppPassword", kind: "AUTH"),
                                      draft: AccountDraft(editing: cy, fixing: .appPassword), tested: refusal,
                                      done: { _ in }))
        defer { shown.close() }
        // What the sheet says under its fields (the renderer draws no scroll view's content): one line, no page.
        let said = shown.words
        XCTAssertTrue(said.contains(AccountDraft.gmailTurnedDown), said)
        XCTAssertEqual(shown.count("not your Google password"), 0, "the steps say it: \(said)")
        XCTAssertEqual(shown.count("Advanced Protection"), 0, said)
        XCTAssertEqual(shown.count("Make an App Password"), 0, "the steps show Google's page: \(said)")

        // And with the steps above it, as the password's row shows them: each thing once.
        let both = Shown(VStack(alignment: .leading) {
            AppPasswordSteps()
            TestedWords(tested: AccountDraft(editing: cy, fixing: .appPassword).inSheet(refusal))
        })
        defer { both.close() }
        XCTAssertEqual(both.count("not your Google password"), 1, both.words)
        XCTAssertEqual(both.count("Advanced Protection"), 1, both.words)
        XCTAssertEqual(both.count("Open Google’s App Passwords") + both.count("Make an App Password"), 1, both.words)
    }

    func testASheetWhoseServersFixWasKeptButStillFailsOffersDoneNotCancel() {
        let ada = StatusFixtures.account("ada@example.test")
        var closed: [String?] = []
        let shown = Shown(AccountForm(status: status(ada, error: "connection refused", kind: "NETWORK"),
                                      draft: AccountDraft(editing: ada, fixing: .server),
                                      tested: .failed("The server didn't answer.", detail: "connection refused"),
                                      kept: true, done: { closed.append($0) }))
        defer { shown.close() }
        XCTAssertEqual(shown.count("Cancel"), 0, "nothing to cancel: the fix is kept: \(shown.words)")
        XCTAssertEqual(shown.count("Done"), 1, shown.words)
        XCTAssertEqual(shown.count("Save"), 1, "another try is kept the same way: \(shown.words)")
        XCTAssertTrue(shown.press("Done"))
        XCTAssertEqual(closed, ["ada@example.test"], "closed on what was kept")
    }

    /// The add-account sheet's fields, as VoiceOver reads them: each by its row's name, where they said only their
    /// prompt, or nothing once they held text. Tab goes from each to the next down the form, and Esc closes the sheet.
    func testTheAccountSheetsFieldsSayTheirNamesTabDownTheFormAndEscCloses() throws {
        MeadowVoiceOverTests.assistive(true)
        defer { MeadowVoiceOverTests.assistive(false) }
        var draft = AccountDraft()
        draft.email = "dee@example.test"
        draft.take(API.PresetFound(domain: "example.test", ok: true, supported: true, sure: false))
        draft.host = "imap.example.test"
        draft.port = "993"
        draft.smtpHost = "smtp.example.test"
        draft.smtpPort = "587"
        draft.alias = "Dee"
        var closed: [String?] = []
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        let shown = Shown(AccountForm(status: status, draft: draft, done: { closed.append($0) }),
                          size: CGSize(width: 560, height: 860))
        defer { shown.close() }
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))

        let named = ["Email", "Incoming server", "Incoming port", "Outgoing server", "Outgoing port", "Password",
                     "Alias", "Signature"]
        let read = MeadowVoiceOverTests.read(shown.host)
        let fields = read.filter { [NSAccessibility.Role.textField.rawValue, NSAccessibility.Role.textArea.rawValue]
            .contains($0.role) }
        XCTAssertEqual(Set(fields.map(\.label)), Set(named), MeadowVoiceOverTests.said(read))
        XCTAssertFalse(fields.contains { $0.label.isEmpty }, MeadowVoiceOverTests.said(read))

        // Tab, from the address, through each field down the form (and whatever else the Mac's keyboard settings
        // let Tab reach), back round to the address.
        func key(_ characters: String, _ code: UInt16) throws -> NSEvent {
            try XCTUnwrap(NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0,
                                           windowNumber: shown.window.windowNumber, context: nil,
                                           characters: characters, charactersIgnoringModifiers: characters,
                                           isARepeat: false, keyCode: code))
        }
        func says(_ view: NSView?) -> String? {
            if view is NSSecureTextField { return "password" }
            if let field = view as? NSTextField { return field.stringValue }
            if let text = view as? NSTextView { return text.string }
            return nil
        }
        func textFields(in view: NSView) -> [NSTextField] {
            ((view as? NSTextField).map { [$0] } ?? []) + view.subviews.flatMap(textFields(in:))
        }
        let email = try XCTUnwrap(textFields(in: shown.host).first { $0.stringValue == draft.email })
        shown.window.makeFirstResponder(email)
        var visited: [String] = []
        for _ in 0..<40 {
            shown.window.sendEvent(try key("\t", 48))
            RunLoop.main.run(until: Date().addingTimeInterval(0.05))
            guard let said = says(shown.focusedField) else { continue }
            if said == draft.email { break }
            if visited.last != said { visited.append(said) }
        }
        XCTAssertEqual(Array(visited.prefix(7)), ["imap.example.test", "993", "smtp.example.test", "587", "password",
                                                  "Dee", ""], "Tab down the form")

        // Esc is Cancel.
        let escape = try key("\u{1b}", 53)
        if !shown.window.performKeyEquivalent(with: escape) { shown.window.sendEvent(escape) }
        RunLoop.main.run(until: Date().addingTimeInterval(0.1))
        XCTAssertEqual(closed, [nil], "Esc closes the sheet, as Cancel")
    }

    /// The account sheet over Settings, its keys sent through `NSApp.sendEvent` as a person's go: AppKit hands them
    /// to the key window, the sheet, so Tab moves down its fields, never through Settings' places behind it, and Esc
    /// is the sheet's Cancel, never Settings' own Esc, which would leave the sheet up.
    func testTabAndEscReachTheAccountSheetOverSettingsAsAPersonsKeysGo() throws {
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
                                                      badge: { _ in }, announce: { _ in }))
        let window = NSWindow(contentRect: NSRect(origin: CGPoint(x: -4000, y: -4000), size: SettingsWindow.size),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        window.contentView = NSHostingView(rootView: SettingsLayout(status: status, lights: false, opening: .adding)
            .meadowLight(.day))
        window.makeKeyAndOrderFront(nil)
        var sheet: NSWindow?
        for _ in 0..<40 where sheet == nil {
            RunLoop.main.run(until: Date().addingTimeInterval(0.05))
            sheet = window.attachedSheet
        }
        let form = try XCTUnwrap(sheet, "Accounts opens on the account form")
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))
        let behind = window.firstResponder
        func key(_ characters: String, _ code: UInt16) throws -> NSEvent {
            try XCTUnwrap(NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0,
                                           windowNumber: form.windowNumber, context: nil, characters: characters,
                                           charactersIgnoringModifiers: characters, isARepeat: false, keyCode: code))
        }
        func editing(_ responder: NSResponder?) -> String {
            // A secure field's editor is AppKit's own, which it doesn't name to Swift.
            if responder?.className == "NSSecureTextView" { return "password" }
            if let text = responder as? NSTextView, text.isFieldEditor { return "a field" }
            return String(describing: responder.map { type(of: $0) })
        }
        XCTAssertEqual(editing(form.firstResponder), "a field", "the address has the keyboard as the sheet opens")

        NSApp.sendEvent(try key("\t", 48))
        RunLoop.main.run(until: Date().addingTimeInterval(0.1))
        XCTAssertEqual(editing(form.firstResponder), "password", "Tab goes down the sheet's fields")
        XCTAssertTrue(window.firstResponder === behind, "and never through Settings' places behind it")

        NSApp.sendEvent(try key("\u{1b}", 53))
        for _ in 0..<20 where window.attachedSheet != nil {
            RunLoop.main.run(until: Date().addingTimeInterval(0.05))
        }
        XCTAssertNil(window.attachedSheet, "Esc is the sheet's Cancel")
        XCTAssertTrue(window.isVisible, "and Settings stays")
    }

    /// A view in a window of its own, off the screen and see-through, as a sheet or a card shows it. A view that is
    /// read is drawn into a PDF too, whose text is what it says and where: SwiftUI builds no accessibility to read
    /// with no assistive app attached.
    @MainActor
    private struct Shown {
        let window: NSWindow
        let host: NSView
        private let drawn: PDFDocument?

        /// `view` laid out in `size`, or, with none, as wide as a Sync card and drawn to be read.
        init(_ view: some View, size: CGSize? = nil) {
            _ = NSApplication.shared
            let lit = AnyView(view.frame(width: size == nil ? 560 : nil).meadowLight(.day))
            var laid = size ?? .zero
            var drawn: PDFDocument?
            if size == nil {
                let data = NSMutableData()
                ImageRenderer(content: lit).render { size, draw in
                    laid = size
                    var box = CGRect(origin: .zero, size: size)
                    guard let consumer = CGDataConsumer(data: data as CFMutableData),
                          let pdf = CGContext(consumer: consumer, mediaBox: &box, nil) else { return }
                    pdf.beginPDFPage(nil)
                    draw(pdf)
                    pdf.endPDFPage()
                    pdf.closePDF()
                }
                drawn = PDFDocument(data: data as Data)
            }
            self.drawn = drawn
            host = NSHostingView(rootView: lit)
            host.frame = CGRect(origin: .zero, size: laid)
            window = NSWindow(contentRect: host.frame, styleMask: [.titled], backing: .buffered, defer: false)
            window.isReleasedWhenClosed = false
            window.contentView = host
            // A press and the focus go only to a window on screen: this one is, but far off it and see-through.
            window.alphaValue = 0
            window.setFrameOrigin(NSPoint(x: -20000, y: -20000))
            window.makeKeyAndOrderFront(nil)
            host.layoutSubtreeIfNeeded()
            RunLoop.main.run(until: Date().addingTimeInterval(0.3))
            host.layoutSubtreeIfNeeded()
        }

        func close() {
            window.orderOut(nil)
            window.contentView = nil
        }

        /// Everything the view says, in order, its lines joined where they wrapped.
        var words: String {
            (drawn?.string ?? "").split(whereSeparator: \.isNewline).joined(separator: " ")
        }

        /// How many times the view says `what`.
        func count(_ what: String) -> Int { words.components(separatedBy: what).count - 1 }

        /// Press where `label` is drawn, as a person clicks the button that says it; false where nothing says it.
        func press(_ label: String) -> Bool {
            guard let drawn, let page = drawn.page(at: 0),
                  let found = drawn.findString(label, withOptions: []).last
            else { return false }
            let at = found.bounds(for: page)
            for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
                guard let event = NSEvent.mouseEvent(with: type, location: NSPoint(x: at.midX, y: at.midY),
                                                     modifierFlags: [],
                                                     timestamp: ProcessInfo.processInfo.systemUptime,
                                                     windowNumber: window.windowNumber, context: nil, eventNumber: 0,
                                                     clickCount: 1, pressure: 1)
                else { return false }
                window.sendEvent(event)
            }
            RunLoop.main.run(until: Date().addingTimeInterval(0.1))
            return true
        }

        /// The field whose editor has the focus.
        var focusedField: NSView? {
            host.layoutSubtreeIfNeeded()
            let responder = window.firstResponder
            if let editor = responder as? NSTextView, editor.isFieldEditor { return editor.delegate as? NSView }
            return responder as? NSView
        }
    }
}
