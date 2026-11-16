#if os(macOS)
import SwiftUI

/// Add an account or edit one, as a sheet drawn as Meadow's dialog (the page's `ConnectionForm`): its address first,
/// then its type and an Other IMAP account's incoming and outgoing servers, its password, and its alias and signature
/// after. A password typed here goes straight to the route and the field is emptied once it is saved. As a new address
/// rests, the server is asked which preset it takes, and its settings go in the form, or are offered where they fit.
/// From the first run, the login is tested before the account is saved, and the sheet stays open with what to do when
/// the test fails. A Proton account logs in with the Bridge's password, which only a saved account is tested with: the
/// first run adds it, then tests it, and keeps the sheet open with what to do until Done when the test fails.
struct AccountForm: View {
    let status: AppStatus
    @State var draft: AccountDraft
    /// Test the login before saving a new account, and save it only once the test says it works (the first run).
    var testsFirst = false
    /// Done: the address saved, added or edited, or nil when the form was cancelled.
    let done: (String?) -> Void

    @State private var problem = ""
    @State private var saving = false
    @State private var tested: Tested?
    /// The account added from the first run whose test then failed: the sheet stays open on it until Done.
    @State private var added: String?
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
            ScrollView {
                VStack(spacing: 0) {
                    row("Email") {
                        VStack(alignment: .leading, spacing: 4) {
                            TextField("Email", text: $draft.email, prompt: Text(verbatim: "you@example.com"))
                                .textContentType(.emailAddress)
                                .meadowComposeField()
                                .disabled(draft.editing != nil)
                            if draft.editing != nil {
                                SettingHelp(String(localized:
                                    "Identity is fixed — remove and re-add to change the address."))
                            }
                            presetWords
                            if let note = draft.note { NoteWords(text: note) }
                        }
                    }
                    if draft.kind != nil, !draft.unsupported {
                        row("Type") {
                            MeadowSegmentedChoice(label: String(localized: "Type"),
                                                  selection: Binding(get: { draft.type }, set: { draft.pick($0) }),
                                                  options: AccountDraft.Kind.allCases.map { ($0, $0.label) },
                                                  fills: false)
                        }
                        if draft.type == .imap { servers }
                    }
                    if !draft.unsupported {
                        row("Password") {
                            VStack(alignment: .leading, spacing: 8) {
                                if draft.type == .proton {
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
                    }
                    row("Signature") {
                        TextField("Signature", text: $draft.signature, prompt: Text(signaturePrompt), axis: .vertical)
                            .lineLimit(3...6)
                            .padding(.vertical, 7)
                            .meadowComposeField()
                    }
                }
            }
            .scrollContentBackground(.hidden)

            if let tested {
                TestedWords(tested: tested)
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
                    Button("Cancel") {
                        draft.forgetPassword()
                        done(nil)
                    }
                    .meadowButton()
                    .keyboardShortcut(.cancelAction)
                    Button(saving ? (testsFirst ? "Testing…" : "Saving…") : (draft.editing == nil ? "Add Account" : "Save")) {
                        Task { await save() }
                    }
                    .meadowButton(.primary)
                    .keyboardShortcut(.defaultAction)
                    .disabled(saving || draft.unsupported)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .overlay(alignment: .top) { ComposeHairline() }
        }
        .frame(width: 560)
        .frame(minHeight: 540)
        .onChange(of: draft.presetQuery) { draft.found = nil }
        .task(id: draft.presetQuery) { await askPreset() }
        .background { ComposePaper() }
        .presentationBackground { ComposePaper() }
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
                   portName: "Incoming port", prompt: "imap.yourhost.com")
        }
        row("Outgoing") {
            VStack(alignment: .leading, spacing: 4) {
                server(host: $draft.smtpHost, port: $draft.smtpPort, security: $draft.smtpSecurity,
                       hostName: "Outgoing server", portName: "Outgoing port",
                       prompt: draft.host.isEmpty ? "smtp.yourhost.com" : draft.host)
                SettingHelp(String(localized: "Where your mail is sent from. Left blank, it is the incoming server."))
            }
        }
    }

    private func server(host: Binding<String>, port: Binding<String>, security: Binding<AccountDraft.Security>,
                        hostName: LocalizedStringKey, portName: LocalizedStringKey, prompt: String) -> some View
    {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                TextField(hostName, text: host, prompt: Text(verbatim: prompt)).meadowComposeField()
                TextField(portName, text: port).meadowComposeField().frame(width: 72)
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

    private func save() async {
        problem = ""
        if let said = draft.problem {
            problem = said
            return
        }
        guard let core = status.core else { return }
        saving = true
        let adding = draft.editing == nil
        let address = draft.address
        let body = draft.body
        // Tested first: a login that doesn't work keeps the sheet open, with the password still typed, to fix. A Proton
        // account logs in with the Bridge's password, which only a saved account may be tested with: it is tested once
        // added, below.
        if adding, testsFirst, draft.type != .proton, let trial = draft.test {
            tested = .running
            do {
                tested = .of(try await SecretPost.post(core, API.Post.AccountsTest.self, trial))
            } catch {
                tested = .of(error)
            }
            guard tested == .connected else {
                saving = false
                return
            }
        }
        draft.forgetPassword()
        do {
            if adding {
                _ = try await SecretPost.post(core, API.Post.AccountsAdd.self, body)
            } else {
                _ = try await SecretPost.post(core, API.Post.AccountsEdit.self, body)
            }
        } catch {
            let refused = (error as? Problem).flatMap { $0.isRefusal ? $0 : nil }
            problem = refused.map { $0.detail ?? String(localized: "Could not save.") } ?? String(localized: "Save failed.")
            saving = false
            return
        }
        status.say(adding ? String(localized: "Added \(StatusWords.short(address))")
                          : String(localized: "Updated \(StatusWords.short(address))"))
        await status.readAccounts()
        await status.readDigest()
        // A Proton account from the first run, tested now it is saved: one whose Bridge doesn't let it in keeps the
        // sheet open with what to do, and a Test to try again, until Done.
        if adding, testsFirst, draft.type == .proton {
            tested = .running
            do {
                tested = .of(try await SecretPost.post(core, API.Post.AccountsTest.self, API.TryBody(email: address)))
            } catch {
                tested = .of(error)
            }
            guard tested == .connected else {
                added = address
                saving = false
                return
            }
        }
        finish(address, adding: adding)
    }

    /// The sheet is done with `address`, saved. A new connection has no mail yet: it is pulled now, not at the next
    /// sync.
    private func finish(_ address: String, adding: Bool) {
        done(address)
        if adding { Task { await status.syncNow() } }
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
