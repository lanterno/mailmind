import Foundation
import XCTest

private typealias F = StatusFixtures

/// The account form checks what the page checks, sends what the page sends, and never shows a typed password.
final class AccountDraftTests: XCTestCase {
    /// A fictional password: it guards nothing.
    private let typed = "fixture-app-password-1234"

    func testANewAccountNeedsAnAddressAHostForIMAPAndAPassword() {
        var draft = AccountDraft()
        XCTAssertEqual(draft.problem, "A valid email is required.")
        draft.email = "ada@example.test"
        XCTAssertEqual(draft.problem, "Set a password (or 1Password ref / env var).")
        draft.kind = .imap
        XCTAssertEqual(draft.problem, "A host is required for a generic IMAP account.")
        draft.host = "imap.example.test"
        draft.password = typed
        XCTAssertNil(draft.problem)
        draft.kind = .proton
        draft.password = ""
        XCTAssertNil(draft.problem, "a Proton account logs in with the Bridge's password")
    }

    func testItSendsWhatThePageSends() throws {
        var draft = AccountDraft()
        draft.email = "  ada@example.test "
        draft.alias = " Ada "
        draft.password = " \(typed) "
        let gmail = draft.body
        XCTAssertEqual(gmail.email, "ada@example.test")
        XCTAssertEqual(gmail.alias, "Ada")
        XCTAssertEqual(gmail.type, .string("gmail"))
        XCTAssertNil(gmail.host, "a preset's server is the server's to fill in")
        XCTAssertEqual(gmail.credential, .object(["style": .string("keychain"), "value": .string(typed)]))

        draft.kind = .imap
        draft.host = "imap.example.test"
        draft.port = "143"
        draft.security = .starttls
        let imap = draft.body
        XCTAssertEqual(imap.host, .string("imap.example.test"))
        XCTAssertEqual(imap.port, .number(143))
        XCTAssertEqual(imap.starttls, true)

        draft.source = .op
        draft.opRef = "op://Vault/Item/password"
        XCTAssertEqual(draft.body.credential, .object(["style": .string("op"), "ref": .string("op://Vault/Item/password")]))
        draft.source = .env
        draft.envVar = "ADA_PASSWORD"
        XCTAssertEqual(draft.body.credential, .object(["style": .string("env"), "var": .string("ADA_PASSWORD")]))
        draft.kind = .proton
        XCTAssertEqual(draft.body.credential, .object(["style": .string("proton_bridge")]))
    }

    func testAnEditKeepsTheAddressAndThePasswordItHasUnlessOneIsTyped() {
        var draft = AccountDraft(editing: F.account("ada@example.test", alias: "Ada", signature: "Ada"))
        draft.email = "eve@example.test"
        XCTAssertEqual(draft.address, "ada@example.test", "identity is fixed")
        XCTAssertNil(draft.body.credential)
        XCTAssertNil(draft.problem)
        XCTAssertEqual(draft.test, API.TryBody(email: "ada@example.test"), "a test without a password tests what is saved")
        draft.password = typed
        XCTAssertEqual(draft.test?.draft?.email, "ada@example.test")
        XCTAssertNil(AccountDraft().test, "a new account with no password has nothing to test")
    }

    func testAnEditStartsFromHowThePasswordIsKept() {
        let op = AccountDraft(editing: F.account("ada@example.test", style: "op", ref: "op://Vault/Ada/password"))
        XCTAssertEqual(op.source, .op)
        XCTAssertEqual(op.opRef, "op://Vault/Ada/password")
        let env = AccountDraft(editing: F.account("ada@example.test", style: "env", variable: "ADA_PASSWORD"))
        XCTAssertEqual(env.source, .env)
        XCTAssertEqual(env.envVar, "ADA_PASSWORD")
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

    func testATestsAnswerInThePagesWords() {
        XCTAssertEqual(Tested.of(.loggedIn(API.LoggedIn(connect: true, detail: "", email: "ada@example.test", ms: 12, ok: true))),
                       .connected)
        XCTAssertEqual(Tested.of(.couldntLogIn(API.CouldntLogIn(connect: false, email: nil, error: "LOGIN refused", ok: true))),
                       .failed("LOGIN refused"))
        XCTAssertEqual(Tested.of(URLError(.timedOut)), .failed("test failed"))
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
        XCTAssertEqual(SecretStanding.apiKey(set: true, ai: on, sources: .init()).summary, "Kept in your login Keychain")
        XCTAssertNil(SecretStanding.apiKey(set: true, ai: on, sources: .init()).note)
        XCTAssertEqual(SecretStanding.apiKey(set: true, ai: on, sources: .init(apiKeyVariable: true)).note,
                       "ANTHROPIC_API_KEY is set too, and the key kept here wins over it.")
        XCTAssertEqual(SecretStanding.apiKey(set: true, ai: off, sources: .init()).summary,
                       "A key is kept, but it can’t be read")
        let environment = SecretStanding.apiKey(set: false, ai: fromVariable, sources: .init(apiKeyVariable: true))
        XCTAssertEqual(environment.summary, "From the environment variable ANTHROPIC_API_KEY")
        XCTAssertEqual(environment.note, "A key kept here would win over it.")
        XCTAssertFalse(environment.kept)
        XCTAssertEqual(SecretStanding.apiKey(set: false, ai: off, sources: .init()).summary, "No key: AI is off")
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
    private func opened() async throws -> (Core, AppStatus) {
        let core = try await Core.open(CoreHome(folder: folder.path(percentEncoded: false),
                                                variables: ["MAILMIND_DB_KEY": Self.key, "MAILMIND_NOSYNC": "1"],
                                                standIn: keychain.path(percentEncoded: false)))
        let status = AppStatus(newMail: NewMailAlerts(notifier: SystemNotifier.Silent(), isActive: { true },
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
        XCTAssertFalse(before.set)
        let kept = try await SecretPost.post(core, API.Post.AiKey.self, API.KeyBody(key: Self.apiKey))
        XCTAssertTrue(kept.set)
        let after = try await core.get(API.Get.AiKey.self)
        XCTAssertTrue(after.set)
        let stood = try String(contentsOf: keychain, encoding: .utf8)
        XCTAssertTrue(stood.contains(Self.apiKey), "the stand-in, not the login Keychain, keeps it")
        for target in ["/api/ai/key", "/api/digest", "/api/accounts"] {
            let answer = await core.send("GET", target, Data())
            XCTAssertFalse(String(decoding: answer.body, as: UTF8.self).contains(Self.apiKey), target)
        }
        let cleared = try await core.post(API.Post.AiKeyClear.self, API.NoBody())
        XCTAssertFalse(cleared.set)
        XCTAssertFalse(try String(contentsOf: keychain, encoding: .utf8).contains(Self.apiKey))
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
        draft.kind = .imap
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

        // Nothing listens on the discard port: the test says it couldn't log in, in the server's words.
        let answer = try await core.post(API.Post.AccountsTest.self, API.TryBody(email: "ada@example.test"))
        let tested = Tested.of(answer)
        guard case .failed(let why) = tested else { return XCTFail("a closed port logged in") }
        XCTAssertFalse(why.contains(Self.password))

        _ = try await core.post(API.Post.AccountsDelete.self, API.NamesAnAccount(email: "ada@example.test"))
        await status.readAccounts()
        XCTAssertTrue(status.firstRun)
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
}
