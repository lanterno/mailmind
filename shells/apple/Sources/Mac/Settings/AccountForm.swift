#if os(macOS)
import SwiftUI

/// Add an account or edit one, as a sheet (the page's `ConnectionForm`): its alias and signature, its address and type,
/// an IMAP server's host and port, and where its password comes from. A password typed here goes straight to the route
/// and the field is emptied, saved or not.
struct AccountForm: View {
    let status: AppStatus
    @State var draft: AccountDraft
    /// Done: the address added, or nil when an edit was saved or the form cancelled.
    let done: (String?) -> Void

    @State private var problem = ""
    @State private var saving = false
    @State private var tested: Tested?

    var body: some View {
        VStack(spacing: 0) {
            Form {
                Section {
                    TextField("Alias", text: $draft.alias, prompt: Text("Optional: the name shown in the sidebar and on rows"))
                    TextField("Signature", text: $draft.signature, prompt: Text(signaturePrompt), axis: .vertical)
                        .lineLimit(3...6)
                }
                Section {
                    TextField("Email", text: $draft.email, prompt: Text(verbatim: "you@example.com"))
                        .textContentType(.emailAddress)
                        .disabled(draft.editing != nil)
                    if draft.editing != nil {
                        SettingHelp(String(localized: "Identity is fixed — remove and re-add to change the address."))
                    }
                    Picker("Type", selection: $draft.kind) {
                        ForEach(AccountDraft.Kind.allCases) { Text($0.label).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    if draft.kind == .imap {
                        TextField("Host", text: $draft.host, prompt: Text(verbatim: "mail.yourhost.com"))
                        TextField("Port", text: $draft.port)
                        Picker("Security", selection: $draft.security) {
                            ForEach(AccountDraft.Security.allCases) { Text($0.label).tag($0) }
                        }
                        .pickerStyle(.segmented)
                    }
                }
                Section("Password") {
                    if draft.kind == .proton {
                        SettingHelp(String(localized: "Proton accounts log in through Proton Bridge, with the Bridge’s password."))
                        BridgePasswordView(status: status)
                    } else {
                        credential
                    }
                }
            }
            .formStyle(.grouped)

            if !problem.isEmpty {
                Text(problem)
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 20)
                    .accessibilityAddTraits(.isStaticText)
            }
            HStack {
                Button("Test Connection") { Task { await test() } }
                    .disabled(tested == .running)
                if let tested { TestedWords(tested: tested) }
                Spacer()
                Button("Cancel", role: .cancel) {
                    draft.forgetPassword()
                    done(nil)
                }
                .keyboardShortcut(.cancelAction)
                Button(saving ? "Saving…" : (draft.editing == nil ? "Add Account" : "Save")) { Task { await save() } }
                    .keyboardShortcut(.defaultAction)
                    .disabled(saving)
            }
            .padding(20)
        }
        .frame(width: 520)
        .frame(minHeight: 520)
    }

    private var signaturePrompt: String {
        status.signatureDefault.isEmpty
            ? String(localized: "Optional: added to new mail and above quoted replies")
            : String(localized: "Optional: added to new mail and above quoted replies — blank uses the global default")
    }

    @ViewBuilder private var credential: some View {
        Picker("Credential", selection: $draft.source) {
            ForEach(AccountDraft.Source.allCases) { Text($0.label).tag($0) }
        }
        .pickerStyle(.segmented)
        switch draft.source {
        case .keychain:
            SecureField("Password", text: $draft.password,
                        prompt: Text(draft.editing == nil ? "paste the password / app password"
                                                          : "unchanged — leave blank to keep"))
            SettingHelp(String(localized: "Kept in your login Keychain, never in a file."))
            if draft.kind == .gmail {
                SettingHelp(String(localized: "Gmail needs an App Password (2-Step Verification → App passwords), not your login password."))
            }
        case .op:
            TextField("1Password reference", text: $draft.opRef, prompt: Text(verbatim: "op://Vault/Item/Field"))
            SettingHelp(String(localized: "Read from the 1Password CLI at runtime."))
        case .env:
            TextField("Environment variable", text: $draft.envVar, prompt: Text(verbatim: "ENV_VAR_NAME"))
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
