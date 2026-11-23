#if os(macOS)
import AppKit
import SwiftUI

/// Add an account or edit one, as a sheet drawn as Meadow's dialog (the page's `ConnectionForm`): its address first,
/// then its type and an Other IMAP account's incoming and outgoing servers, its password, and its alias and signature
/// after. A password typed here goes straight to the route and the field is emptied once it is saved. As a new address
/// rests, the server is asked which preset it takes, and its settings go in the form, or are offered where they fit.
/// From the first run, the login is tested before the account is saved, and the sheet stays open with what to do when
/// the test fails. A Proton account logs in with the Bridge's password, which only a saved account is tested with: the
/// first run adds it, then tests it, and keeps the sheet open with what to do until Done when the test fails. An edit
/// opened from the account's Sync card to fix its login is tested as it is saved too (`AccountSave`, #51).
struct AccountForm: View {
    let status: AppStatus
    @State var draft: AccountDraft
    /// Test the login before saving a new account, and save it only once the test says it works (the first run).
    var testsFirst = false
    /// What the last test said, under the form; a test of the sheet may open it on one.
    @State var tested: Tested? = nil
    /// A fix of the account's server kept, whose test then failed (#51): what was kept stays, so Cancel is Done, and
    /// Save keeps another try. A test of the sheet may open it so.
    @State var kept = false
    /// Done: the address saved, added or edited, or nil when the form was cancelled.
    let done: (String?) -> Void

    @State private var problem = ""
    @State private var saving = false
    @State private var importingHodHod = false
    /// The account added from the first run whose test then failed: the sheet stays open on it until Done.
    @State private var added: String?
    /// The field the sheet was opened to fix (#51), focused as it opens and again when a fix didn't work.
    @FocusState private var focused: AccountFormField?
    @Environment(\.meadow) private var meadow

    /// How long the address rests before the server is asked which preset it takes.
    static let askAfter: Duration = .milliseconds(400)

    var body: some View {
        VStack(spacing: 0) {
            Text(draft.editing == nil ? "Add an account" : "Edit \(draft.alias.isEmpty ? draft.address : draft.alias)")
                .font(.meadow(.display, 18, .bold))
                .foregroundStyle(meadow.ink)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(EdgeInsets(top: 18, leading: 18, bottom: 12, trailing: 18))
                .accessibilityAddTraits(.isHeader)
            FootInSight(change: Foot(tested: tested, problem: problem)) {
                VStack(spacing: 0) {
                    row("Email") {
                        VStack(alignment: .leading, spacing: 4) {
                            // Each field says its row's name to VoiceOver: a plain field's own title is never shown,
                            // and VoiceOver read only its prompt, or nothing once it held text.
                            TextField("Email", text: $draft.email, prompt: Text(verbatim: "you@example.com"))
                                .textContentType(.emailAddress)
                                .meadowComposeField()
                                .accessibilityLabel("Email")
                                .disabled(draft.editing != nil || draft.type == .hodhod)
                            if draft.editing != nil {
                                SettingHelp(String(localized:
                                    "Identity is fixed — remove and re-add to change the address."))
                            }
                            presetWords
                            if let note = draft.note { NoteWords(text: note) }
                            if let taken = draft.alreadySetUp(among: known) { NoteWords(text: taken) }
                            if draft.editing == nil, draft.type != .hodhod {
                                Button("Import HodHod profile…", action: chooseHodHod)
                                    .meadowButton(small: true)
                                    .disabled(importingHodHod)
                            }
                        }
                    }
                    if draft.kind != nil, !draft.unsupported, draft.type != .hodhod {
                        row("Type") {
                            MeadowSegmentedChoice(label: String(localized: "Type"),
                                                  selection: Binding(get: { draft.type }, set: { draft.pick($0) }),
                                                  options: AccountDraft.Kind.allCases.filter { $0 != .hodhod }.map { ($0, $0.label) },
                                                  fills: false)
                        }
                        if draft.type == .imap { servers }
                    }
                    if !draft.unsupported {
                        row(draft.type == .hodhod ? "Profile" : "Password") {
                            VStack(alignment: .leading, spacing: 8) {
                                if draft.type == .hodhod {
                                    Button("Import HodHod profile…", action: chooseHodHod)
                                        .meadowButton(small: true)
                                        .disabled(importingHodHod)
                                    SettingHelp(String(localized: "Cloud AI pauses while a HodHod account is connected."))
                                } else if draft.type == .proton {
                                    SettingHelp(String(localized:
                                        "Proton accounts log in through Proton Bridge, with the Bridge’s password."))
                                    BridgePasswordView(status: status)
                                    if let link = draft.link { LinkButton(link: link) }
                                    if added == nil, let later = draft.testsOnceAdded { SettingHelp(later) }
                                } else {
                                    credential
                                }
                            }
                        }
                    }
                    row("Alias") {
                        TextField("Alias", text: $draft.alias,
                                  prompt: Text("Optional: the name shown in the sidebar and on rows"))
                            .meadowComposeField()
                            .accessibilityLabel("Alias")
                    }
                    row("Signature") {
                        TextField("Signature", text: $draft.signature, prompt: Text(signaturePrompt), axis: .vertical)
                            .lineLimit(3...6)
                            .padding(.vertical, 7)
                            .meadowComposeField()
                            .accessibilityLabel("Signature")
                    }
                }
            }

            if let tested {
                TestedWords(tested: draft.inSheet(tested))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 18)
                    .padding(.bottom, 8)
            }
            if !problem.isEmpty {
                HStack(spacing: 8) {
                    MeadowIconView(.alert, size: 15)
                    Text(problem)
                }
                .font(.meadow(.ui, 13, .semibold))
                .foregroundStyle(meadow.rust)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(meadow.rustWash, in: RoundedRectangle(cornerRadius: 10))
                .padding(.horizontal, 16)
                .padding(.bottom, 8)
                .accessibilityAddTraits(.isStaticText)
            }
            HStack(spacing: 8) {
                Button("Test Connection") { Task { await test() } }
                    .meadowButton(small: true)
                    .disabled(tested == .running || draft.unsupported || (added == nil && draft.testsOnceAdded != nil))
                Spacer()
                if let added {
                    Button("Done") { finish(added, adding: true) }
                        .meadowButton(.primary)
                        .keyboardShortcut(.defaultAction)
                } else {
                    Button(kept ? "Done" : "Cancel") {
                        draft.forgetPassword()
                        if kept { finish(draft.address, adding: false) } else { done(nil) }
                    }
                    .meadowButton()
                    .keyboardShortcut(.cancelAction)
                    Button(saving ? (tested == .running ? "Testing…" : "Saving…")
                                  : (draft.editing == nil ? "Add Account" : "Save")) {
                        Task { await save() }
                    }
                    .meadowButton(.primary)
                    .keyboardShortcut(.defaultAction)
                    .disabled(saving || importingHodHod || draft.unsupported)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .overlay(alignment: .top) { ComposeHairline() }
        }
        .frame(width: 560)
        .frame(minHeight: 540)
        .defaultFocus($focused, AccountFormField.fixing(draft.fixing))
        .onAppear { focusTheFix() }
        .onChange(of: draft.presetQuery) { draft.found = nil }
        .task(id: draft.presetQuery) { await askPreset() }
        .background { ComposePaper() }
        .presentationBackground { ComposePaper() }
    }

    /// What stands under the form and takes room from it: a test's words, and a problem.
    private struct Foot: Equatable {
        let tested: Tested?
        let problem: String
    }

    /// A field's row (`.fld`): its name in a column of its own, what it holds beside it, a hairline above.
    private func row(_ label: LocalizedStringKey, @ViewBuilder _ content: () -> some View) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(label)
                .font(.meadow(.ui, 13, .bold))
                .foregroundStyle(meadow.ink3)
                .frame(width: 88, height: 32, alignment: .leading)
            content().frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 8)
        .overlay(alignment: .top) { ComposeHairline() }
    }

    /// An Other IMAP account's servers: where its mail is read, and where it is sent from.
    @ViewBuilder private var servers: some View {
        row("Incoming") {
            server(host: $draft.host, port: $draft.port, security: $draft.security, hostName: "Incoming server",
                   portName: "Incoming port", prompt: "imap.yourhost.com", field: .server)
        }
        row("Outgoing") {
            VStack(alignment: .leading, spacing: 4) {
                server(host: $draft.smtpHost, port: $draft.smtpPort, security: $draft.smtpSecurity,
                       hostName: "Outgoing server", portName: "Outgoing port",
                       prompt: draft.host.isEmpty ? "smtp.yourhost.com" : draft.host, field: .outgoingServer)
                SettingHelp(String(localized: "Where your mail is sent from. Left blank, it is the incoming server."))
            }
        }
    }

    private func server(host: Binding<String>, port: Binding<String>, security: Binding<AccountDraft.Security>,
                        hostName: LocalizedStringKey, portName: LocalizedStringKey, prompt: String,
                        field: AccountFormField) -> some View
    {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                TextField(hostName, text: host, prompt: Text(verbatim: prompt))
                    .meadowComposeField()
                    .focused($focused, equals: field)
                    .accessibilityLabel(hostName)
                TextField(portName, text: port).meadowComposeField().frame(width: 72)
                    .accessibilityLabel(portName)
            }
            MeadowSegmentedChoice(label: String(localized: "Security"), selection: security,
                                  options: AccountDraft.Security.allCases.map { ($0, $0.label) }, fills: false)
        }
    }

    private var signaturePrompt: String {
        status.signatureDefault.isEmpty
            ? String(localized: "Optional: added to new mail and above quoted replies")
            : String(localized: "Optional: added to new mail and above quoted replies — blank uses the global default")
    }

    /// The password: typed and kept in the Keychain; or, for an account that reads it from 1Password or the
    /// environment, where that is, unchanged here.
    @ViewBuilder private var credential: some View {
        if let elsewhere = draft.keptElsewhere {
            HStack(spacing: 6) {
                MeadowIconView(.key, size: 14).foregroundStyle(meadow.ink3)
                Text(elsewhere).lineLimit(1).truncationMode(.middle)
            }
            .font(.meadow(.ui, 13))
            .foregroundStyle(meadow.ink2)
            .padding(.top, 7)
            SettingHelp(String(localized: """
                MailMind reads it from there. To keep its password in your Keychain instead, remove the account and \
                add it again.
                """))
        } else {
            SecureField("Password", text: $draft.password, prompt: Text(draft.passwordPrompt))
                .meadowComposeField()
                .focused($focused, equals: .password)
                .accessibilityLabel("Password")
            SettingHelp(String(localized: "Kept in your login Keychain, never in a file."))
            if draft.type == .gmail {
                AppPasswordSteps()
            } else if let link = draft.link {
                LinkButton(link: link)
            }
        }
    }

    /// What the form says of the preset the server found: its settings offered, with a button to take them, or said
    /// to fit.
    @ViewBuilder private var presetWords: some View {
        switch draft.presetWords {
        case .offer(let words, let button)?:
            HStack(spacing: 8) {
                Text(words)
                    .font(.meadow(.ui, 13))
                    .foregroundStyle(meadow.ink2)
                    .fixedSize(horizontal: false, vertical: true)
                Button(button) { draft.useFound() }
                    .meadowButton(small: true)
            }
        case .fits(let words)?:
            SettingHelp(words)
        case nil:
            EmptyView()
        }
    }

    private func chooseHodHod() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.prompt = String(localized: "Import")
        let open = { (answer: NSApplication.ModalResponse) in
            guard answer == .OK, let url = panel.url else { return }
            importingHodHod = true
            problem = ""
            Task {
                defer { importingHodHod = false }
                do {
                    let profile = try EncryptedMailboxProfile.readFile(url)
                    let address = try await EncryptedMailboxAPI(profile: profile).account()
                    try draft.importHodHod(profile, address: address)
                    tested = nil
                } catch {
                    problem = String(localized: "Couldn’t import the HodHod profile or verify its mailbox.")
                }
            }
        }
        if let window = NSApp.keyWindow {
            panel.beginSheetModal(for: window, completionHandler: open)
        } else {
            open(panel.runModal())
        }
    }

    /// Ask the server which preset the address takes once it has rested. A new address has forgotten what the last one
    /// was told (`onChange`).
    private func askPreset() async {
        guard let email = draft.presetQuery, let core = status.core else { return }
        do {
            try await Task.sleep(for: Self.askAfter)
            let found = try await core.get(API.Get.AccountsPreset.self, ["email": email])
            if !Task.isCancelled { draft.take(found) }
        } catch {
            // Cancelled as the address changed, or no answer: nothing is offered.
        }
    }

    private func test() async {
        guard let core = status.core else { return }
        guard let body = added.map({ API.TryBody(email: $0) }) ?? draft.test else {
            tested = .failed(String(localized: "Type the password, then test."))
            return
        }
        tested = .running
        do {
            tested = .of(try await SecretPost.post(core, API.Post.AccountsTest.self, body))
        } catch {
            tested = .of(error)
        }
    }

    /// The addresses MailMind has accounts for.
    private var known: [String] { status.accounts.compactMap(\.email) }

    private func save() async {
        problem = ""
        if let said = draft.alreadySetUp(among: known) ?? draft.problem {
            problem = said
            focusTheFix()
            return
        }
        guard let core = status.core else { return }
        saving = true
        let adding = draft.editing == nil
        let address = draft.address
        let outcome = await AccountSave(routes: .over(core, status: status))
            .run(draft, firstRun: testsFirst) { tested = $0 }
        saving = false
        // The password typed is let go once it is sent to be kept, or once it didn't log in to fix an account: the
        // field asks again. From the first run, one that didn't log in stays typed, to fix.
        if !(adding && outcome == .notKept) { draft.forgetPassword() }
        switch outcome {
        case .kept:
            finish(address, adding: adding)
        case .notKept:
            focusTheFix()
        case .keptFailing:
            if adding { added = address } else { kept = true }
            // Only the outgoing server turned it down: the password reads the mail, and isn't asked for again.
            if tested?.sendingOnly != true { focusTheFix() }
        case .refused(let said):
            problem = said
            focusTheFix()
        }
    }

    /// Put the focus in the field the sheet was opened to fix, once the sheet can take it.
    private func focusTheFix() {
        guard let field = AccountFormField.fixing(draft.fixing) else { return }
        DispatchQueue.main.async { focused = field }
    }

    /// The sheet is done with `address`, saved. A new connection has no mail yet: it is pulled now, not at the next
    /// sync.
    private func finish(_ address: String, adding: Bool) {
        done(address)
        if adding { Task { await status.syncNow() } }
    }
}

/// What the account sheet's Save does, step by step, over routes a test stands in for (#51). Where a login can be
/// tried as typed (a new account from the first run; a password typed to fix an account's login), it is tried before
/// anything is kept, and one that doesn't log in keeps nothing: Cancel still leaves the account as it was. A test of
/// what is typed is never held back for the refusal the account's sync met, which names the login that was kept.
/// Where it can't be (a Proton account from the first run, whose Bridge password only a saved account is tried with;
/// a server's fix, whose kept password the sheet never sees), the account is kept, then tried as kept. A fix is said
/// to be kept only once it logs in, and its account then syncs again, which clears the trouble it fixed.
@MainActor
struct AccountSave {
    let routes: AccountRoutes

    enum Outcome: Equatable {
        /// Kept, and logging in where it was tried: the sheet closes.
        case kept
        /// The login typed didn't work, and nothing was kept.
        case notKept
        /// Kept, but the login as kept didn't work.
        case keptFailing
        /// The server wouldn't keep it: its words, or ours.
        case refused(String)
    }

    /// Save `draft`, from the first run when `firstRun`, showing each test as it runs and as it ends.
    func run(_ draft: AccountDraft, firstRun: Bool, show: (Tested) -> Void) async -> Outcome {
        let adding = draft.editing == nil
        let address = draft.address
        let tries = draft.tries(firstRun: firstRun)
        let fixing = !adding && draft.fixing != nil
        // A fixed password that reads the mail but that the outgoing server turns down has fixed what the sync was
        // refused: it is kept and the account syncs, while the sheet stays on the outgoing server's words.
        var readsOnly = false
        if tries == .typed, let trial = draft.test {
            show(.running)
            let tested = await routes.test(trial)
            show(tested)
            if tested != .connected {
                guard fixing, tested.sendingOnly else { return .notKept }
                readsOnly = true
            }
        }
        do {
            try await routes.keep(draft.body, adding)
        } catch {
            let refused = (error as? Problem).flatMap { $0.isRefusal ? $0 : nil }
            return .refused(refused.map { $0.detail ?? String(localized: "Could not save.") }
                ?? String(localized: "Save failed."))
        }
        let said = adding ? String(localized: "Added \(StatusWords.short(address))")
                          : String(localized: "Updated \(StatusWords.short(address))")
        if !(fixing && tries == .kept) { routes.say(said) }
        await routes.kept(address, adding)
        if readsOnly {
            await routes.retry(address)
            return .keptFailing
        }
        if tries == .kept {
            show(.running)
            let tested = await routes.test(API.TryBody(email: address))
            show(tested)
            guard tested == .connected else { return .keptFailing }
            if fixing { routes.say(said) }
        }
        if fixing { await routes.retry(address) }
        return .kept
    }
}

/// The routes and the status the account sheet's Save goes through: the core's, or a test's in their place.
@MainActor
struct AccountRoutes {
    /// Try a login: a draft as typed, or an account as kept.
    var test: (API.TryBody) async -> Tested
    /// Keep the account: add it (true), or keep its edit.
    var keep: (API.AccountBody, Bool) async throws -> Void
    /// Once an account is kept (added when true): Settings and the window read the accounts again.
    var kept: (String, Bool) async -> Void
    /// Say a few words in the status line.
    var say: (String) -> Void
    /// Sync the account again.
    var retry: (String) async -> Void

    /// The core's routes, a secret sent through `SecretPost`, the app's status, and its writing.
    static func over(_ core: Core, status: AppStatus, composing: Composing = .shared) -> AccountRoutes {
        AccountRoutes(
            test: { body in
                do {
                    return .of(try await SecretPost.post(core, API.Post.AccountsTest.self, body))
                } catch {
                    return .of(error)
                }
            },
            keep: { body, adding in
                if adding {
                    _ = try await SecretPost.post(core, API.Post.AccountsAdd.self, body)
                } else {
                    _ = try await SecretPost.post(core, API.Post.AccountsEdit.self, body)
                }
            },
            kept: { _, adding in
                await status.readAccounts()
                await status.readDigest()
                // Who you are as writing reads it (the signature, the name, the accounts that send) at once, so a new
                // message or a reply written right after Save signs with what was just kept, not with the last sync's.
                await composing.readDigest()
                // The window's rail and list read again now, not at the end of the new account's first sync: the rail
                // shows it at once, and its Inbox says it is being read for the first time. Not waited for: its
                // folders are read live from its server, and the sheet closes (or tests a Proton account) meanwhile.
                if adding { MailApp.shared.accountsChanged() }
            },
            say: { status.say($0) },
            retry: { await status.retry($0) })
    }
}

/// The account form's fields the focus is put in: the one a fix from the Sync card is for (#51).
enum AccountFormField: Hashable {
    case password, server, outgoingServer

    /// The field that fixes `fix`: a password's, or the incoming server's name.
    static func fixing(_ fix: AccountFix?) -> AccountFormField? {
        switch fix {
        case .appPassword?, .password?: .password
        case .server?: .server
        case nil: nil
        }
    }
}

/// A form that scrolls, and scrolls to its foot whenever `change` does: what then shows under it takes room from it
/// where its sheet can't grow past the window it hangs on, and its last row stays whole, never cut in two.
struct FootInSight<Change: Equatable, Content: View>: View {
    let change: Change
    @ViewBuilder let content: () -> Content

    private static var foot: String { "foot" }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(spacing: 0) {
                    content()
                    Color.clear.frame(height: 0).id(Self.foot)
                }
            }
            .scrollContentBackground(.hidden)
            .onChange(of: change) {
                // Once the room it lost is laid out.
                DispatchQueue.main.async { proxy.scrollTo(Self.foot, anchor: .bottom) }
            }
        }
    }
}

/// A provider's words on its account before the password: what kind of password it takes, or that MailMind can't log
/// in to it yet.
struct NoteWords: View {
    let text: String
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            MeadowIconView(.info, size: 14).foregroundStyle(meadow.ink3)
            Text(text)
                .font(.meadow(.ui, 13))
                .foregroundStyle(meadow.ink2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 2)
    }
}

/// A page that helps (where a provider's app password is made, Proton Bridge), opened in the person's browser.
struct LinkButton: View {
    let link: API.Link

    var body: some View {
        Button {
            if let url = URL(string: link.url) { NSWorkspace.shared.open(url) }
        } label: {
            Text(link.words)
            MeadowIconView(.external, size: 14)
        }
        .meadowButton(small: true)
        .help(link.url)
    }
}

/// How to make the App Password Gmail asks for (the page's `AppPasswordSteps`), with Google's page for it a click away
/// in the person's browser.
struct AppPasswordSteps: View {
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            SettingHelp(String(localized: "Gmail takes an App Password here, not your Google password:"))
            ForEach(Array(AccountDraft.appPasswordSteps.enumerated()), id: \.offset) { index, step in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(verbatim: "\(index + 1).").monospacedDigit().foregroundStyle(meadow.ink3)
                    Text(step).foregroundStyle(meadow.ink2).fixedSize(horizontal: false, vertical: true)
                }
                .font(.meadow(.ui, 13))
            }
            Button { NSWorkspace.shared.open(AccountDraft.appPasswords) } label: {
                Text("Open Google’s App Passwords")
                MeadowIconView(.external, size: 14)
            }
            .meadowButton(small: true)
            .help(AccountDraft.appPasswords.absoluteString)
            SettingHelp(String(localized:
                "No App Passwords there? Advanced Protection, or your Workspace admin, has switched them off."))
        }
    }
}
#endif
