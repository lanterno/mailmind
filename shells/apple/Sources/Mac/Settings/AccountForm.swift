#if os(macOS)
import SwiftUI

/// Add an account or edit one, as a sheet drawn as Meadow's dialog (the page's `ConnectionForm`): its alias and
/// signature, its address and type, an IMAP server's host and port, and where its password comes from. A password
/// typed here goes straight to the route and the field is emptied, saved or not.
struct AccountForm: View {
    let status: AppStatus
    @State var draft: AccountDraft
    /// Done: the address added, or nil when an edit was saved or the form cancelled.
    let done: (String?) -> Void

    @State private var problem = ""
    @State private var saving = false
    @State private var tested: Tested?
    @Environment(\.meadow) private var meadow

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
                        }
                    }
                    row("Type") {
                        MeadowSegmentedChoice(label: String(localized: "Type"), selection: $draft.kind,
                                              options: AccountDraft.Kind.allCases.map { ($0, $0.label) }, fills: false)
                    }
                    if draft.kind == .imap {
                        row("Host") {
                            TextField("Host", text: $draft.host, prompt: Text(verbatim: "mail.yourhost.com"))
                                .meadowComposeField()
                        }
                        row("Port") {
                            TextField("Port", text: $draft.port).meadowComposeField().frame(width: 88)
                        }
                        row("Security") {
                            MeadowSegmentedChoice(label: String(localized: "Security"), selection: $draft.security,
                                                  options: AccountDraft.Security.allCases.map { ($0, $0.label) },
                                                  fills: false)
                        }
                    }
                    row("Password") {
                        VStack(alignment: .leading, spacing: 8) {
                            if draft.kind == .proton {
                                SettingHelp(String(localized:
                                    "Proton accounts log in through Proton Bridge, with the Bridge’s password."))
                                BridgePasswordView(status: status)
                            } else {
                                credential
                            }
                        }
                    }
                }
            }
            .scrollContentBackground(.hidden)

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
                    .disabled(tested == .running)
                if let tested { TestedWords(tested: tested) }
                Spacer()
                Button("Cancel") {
                    draft.forgetPassword()
                    done(nil)
                }
                .meadowButton()
                .keyboardShortcut(.cancelAction)
                Button(saving ? "Saving…" : (draft.editing == nil ? "Add Account" : "Save")) { Task { await save() } }
                    .meadowButton(.primary)
                    .keyboardShortcut(.defaultAction)
                    .disabled(saving)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .overlay(alignment: .top) { ComposeHairline() }
        }
        .frame(width: 560)
        .frame(minHeight: 540)
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

    private var signaturePrompt: String {
        status.signatureDefault.isEmpty
            ? String(localized: "Optional: added to new mail and above quoted replies")
            : String(localized: "Optional: added to new mail and above quoted replies — blank uses the global default")
    }

    @ViewBuilder private var credential: some View {
        MeadowSegmentedChoice(label: String(localized: "Credential"), selection: $draft.source,
                              options: AccountDraft.Source.allCases.map { ($0, $0.label) }, fills: false)
        switch draft.source {
        case .keychain:
            SecureField("Password", text: $draft.password,
                        prompt: Text(draft.editing == nil ? "paste the password / app password"
                                                          : "unchanged — leave blank to keep"))
                .meadowComposeField()
            SettingHelp(String(localized: "Kept in your login Keychain, never in a file."))
            if draft.kind == .gmail {
                SettingHelp(String(localized: "Gmail needs an App Password (2-Step Verification → App passwords), not your login password."))
            }
        case .op:
            TextField("1Password reference", text: $draft.opRef, prompt: Text(verbatim: "op://Vault/Item/Field"))
                .meadowComposeField()
            SettingHelp(String(localized: "Read from the 1Password CLI at runtime."))
        case .env:
            TextField("Environment variable", text: $draft.envVar, prompt: Text(verbatim: "ENV_VAR_NAME"))
                .meadowComposeField()
            SettingHelp(String(localized: "Read from an environment variable."))
        }
    }

    private func test() async {
        guard let core = status.core else { return }
        guard let body = draft.test else {
            tested = .failed(String(localized: "enter a password to test"))
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
        done(adding ? address : nil)
        // A new connection has no mail yet: pull it now, not at the next sync.
        if adding { Task { await status.syncNow() } }
    }
}
#endif
