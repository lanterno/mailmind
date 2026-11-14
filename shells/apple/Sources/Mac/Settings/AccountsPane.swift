#if os(macOS)
import SwiftUI

/// Settings' Accounts: the email connections, each as the page's `ConnectionRow` shows it, beside the one chosen. Add
/// one with +, take one out with −, as Mail's Accounts pane does.
struct AccountsPane: View {
    let status: AppStatus
    @State private var chosen: String?
    @State private var form: Editing?
    @State private var removing: API.AccountRow?
    @State private var stats: [String: API.AccountStats] = [:]

    init(status: AppStatus, chosen: String? = nil) {
        self.status = status
        _chosen = State(initialValue: chosen)
    }

    /// Which form is open: a new account, or the one an address names.
    enum Editing: Identifiable, Hashable {
        case adding
        case editing(String)
        var id: Self { self }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(spacing: 0) {
                List(selection: $chosen) {
                    ForEach(status.accounts, id: \.email) { row in
                        AccountListRow(status: status, row: row)
                            .tag(row.email)
                    }
                }
                .listStyle(.bordered(alternatesRowBackgrounds: false))
                .overlay {
                    if status.accountsRead, status.accounts.isEmpty {
                        Text("No email connections yet.")
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                            .padding()
                    }
                }
                HStack(spacing: 0) {
                    Button { form = .adding } label: { Image(systemName: "plus").frame(width: 24, height: 20) }
                        .help("Add a connection")
                        .accessibilityLabel("Add a connection")
                    Divider().frame(height: 16)
                    Button { removing = status.accounts.first { $0.email == chosen } } label: {
                        Image(systemName: "minus").frame(width: 24, height: 20)
                    }
                    .disabled(chosen == nil)
                    .help("Remove the connection chosen")
                    .accessibilityLabel("Remove the connection chosen")
                    Spacer()
                    Text(status.accounts.count == 1 ? "1 connection" : "\(status.accounts.count) connections")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.trailing, 6)
                }
                .buttonStyle(.borderless)
                .padding(.vertical, 2)
                .background(.background.secondary)
                .overlay(alignment: .top) { Divider() }
            }
            .frame(width: 220)
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.separator))

            Group {
                if let row = status.accounts.first(where: { $0.email == chosen }) {
                    AccountDetail(status: status, row: row, live: stats[row.email ?? ""],
                                  edit: { form = .editing(row.email ?? "") }, remove: { removing = row })
                } else {
                    ContentUnavailableView {
                        Label("No account chosen", systemImage: "at")
                    } description: {
                        Text("Choose an account to see how it stands, or add one with +.")
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .frame(width: 720, height: 460)
        .onAppear {
            if chosen == nil { chosen = status.accounts.first?.email }
        }
        .task {
            await status.readAccounts()
            if chosen == nil { chosen = status.accounts.first?.email }
            // The page counts each Inbox on the server as Settings opens: an IMAP scan, so only here.
            if let read = try? await status.core?.get(API.Get.Stats.self) {
                stats = Dictionary(read.accounts.map { ($0.account, $0) }, uniquingKeysWith: { first, _ in first })
            }
        }
        .sheet(item: $form) { editing in
            AccountForm(status: status, draft: AccountDraft(editing: editing.row(in: status.accounts))) { added in
                form = nil
                if let added { chosen = added }
            }
        }
        .confirmationDialog(removing.map { "Remove \($0.alias.flatMap { $0.isEmpty ? nil : $0 } ?? $0.email ?? "")?" } ?? "",
                            isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
                            presenting: removing) { row in
            Button("Remove", role: .destructive) {
                Task { await remove(row) }
            }
        } message: { row in
            Text("MailMind stops reading \(row.email ?? "") and lets go of its password. Nothing changes on its server.")
        }
    }

    private func remove(_ row: API.AccountRow) async {
        guard let core = status.core, let email = row.email else { return }
        do {
            _ = try await core.post(API.Post.AccountsDelete.self, API.NamesAnAccount(email: email))
            status.say(String(localized: "Removed \(StatusWords.short(email))"))
            if chosen == email { chosen = nil }
        } catch let problem as Problem where problem.status == 404 {
            status.say(problem.detail ?? String(localized: "Couldn’t remove"))
        } catch {
            status.say(String(localized: "Remove failed"))
        }
        await status.readAccounts()
        await status.readDigest()
        if chosen == nil { chosen = status.accounts.first?.email }
    }
}

extension AccountsPane.Editing {
    func row(in accounts: [API.AccountRow]) -> API.AccountRow? {
        guard case .editing(let email) = self else { return nil }
        return accounts.first { $0.email == email }
    }
}

/// An account in the list: its initial in its colour, with a dot for how it stands, its name and address.
struct AccountListRow: View {
    let status: AppStatus
    let row: API.AccountRow

    var body: some View {
        let email = row.email ?? ""
        HStack(spacing: 8) {
            AccountBadge(email: email, label: label, standing: StatusWords.standing(of: email, digest: status.digest,
                                                                                     health: status.health))
            VStack(alignment: .leading, spacing: 1) {
                Text(label).lineLimit(1)
                Text(email).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .opacity(row.disabled ? 0.5 : 1)
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }

    private var label: String {
        if let alias = row.alias, !alias.isEmpty { return alias }
        return StatusWords.short(row.email ?? "")
    }
}

/// An account's initial on a circle of its own hue, as the page's chip draws it, with how it stands as a dot.
struct AccountBadge: View {
    let email: String
    let label: String
    let standing: StatusWords.Standing

    var body: some View {
        Text(String(label.first ?? "?").uppercased())
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(.white)
            .frame(width: 26, height: 26)
            .background(Circle().fill(Color(hue: Double(Self.hue(email)) / 360, saturation: 0.45, brightness: 0.7)))
            .overlay(alignment: .bottomTrailing) {
                Circle().fill(standing.color)
                    .frame(width: 9, height: 9)
                    .overlay(Circle().strokeBorder(.background, lineWidth: 1.5))
                    .opacity(standing == .unknown ? 0 : 1)
            }
            .accessibilityHidden(true)
    }

    /// The page's hue for an address (`avatarHue`), so an account keeps its colour on every screen.
    static func hue(_ text: String) -> Int {
        var hue = 0
        for unit in text.utf16 {
            hue = (hue * 31 + Int(unit)) % 360
        }
        return hue
    }
}

extension StatusWords.Standing {
    var color: Color {
        switch self {
        case .unknown: .secondary
        case .connected: .green
        case .syncing: .orange
        case .cantConnect: .red
        }
    }

    var words: String {
        switch self {
        case .unknown: ""
        case .connected: String(localized: "connected")
        case .syncing: String(localized: "syncing…")
        case .cantConnect: String(localized: "can’t connect")
        }
    }
}

extension StatusWords.Dot {
    var color: Color {
        switch self {
        case .none: .secondary
        case .ok: .green
        case .busy: .orange
        case .error: .red
        }
    }
}

/// The account chosen: how it is reached, how its syncs stand, its Inbox on the server, and what can be done with it.
struct AccountDetail: View {
    let status: AppStatus
    let row: API.AccountRow
    let live: API.AccountStats?
    let edit: () -> Void
    let remove: () -> Void
    @State private var tested: Tested?

    var body: some View {
        let email = row.email ?? ""
        Form {
            Section {
                LabeledContent("Name") {
                    Text(row.alias.flatMap { $0.isEmpty ? nil : $0 } ?? String(localized: "\(StatusWords.short(email)) · no alias"))
                        .foregroundStyle(row.alias?.isEmpty == false ? .primary : .secondary)
                }
                LabeledContent("Address") { Text(email).textSelection(.enabled) }
                LabeledContent("Type") { Text(AccountDraft.Kind(rawValue: row.type)?.label ?? row.type) }
                if row.type == "imap" {
                    LabeledContent("Server") {
                        Text("\(row.host ?? ""):\(String(row.port))\(row.starttls ? " · STARTTLS" : "")")
                    }
                }
                LabeledContent("Password") { CredentialWords(credential: row.credential) }
                if row.disabled { LabeledContent("Status") { Text("Disabled") } }
                if let live {
                    LabeledContent("Inbox") {
                        if let error = live.error, !error.isEmpty {
                            Text(error).foregroundStyle(.red).lineLimit(1)
                        } else {
                            Text(live.inInbox.formatted())
                        }
                    }
                }
            }
            Section {
                HealthRow(status: status, email: email)
                HStack {
                    Button("Test Connection") { Task { await test(email) } }
                        .disabled(tested == .running)
                    if let tested { TestedWords(tested: tested) }
                    Spacer()
                    Button("Edit…", action: edit)
                    Button("Remove…", role: .destructive, action: remove)
                }
            }
            if !row.signature.isEmpty {
                Section("Signature") {
                    Text(row.signature).font(.callout).textSelection(.enabled)
                }
            }
            if row.type == "proton" {
                Section("Proton Bridge password") { BridgePasswordView(status: status) }
            }
        }
        .formStyle(.grouped)
        .id(email)
    }

    private func test(_ email: String) async {
        guard let core = status.core else { return }
        tested = .running
        do {
            tested = .of(try await core.post(API.Post.AccountsTest.self, API.TryBody(email: email)))
        } catch {
            tested = .of(error)
        }
    }
}

/// How an account's password is kept, never the password (`Credentials`).
struct CredentialWords: View {
    let credential: API.CredentialShown

    var body: some View {
        if !credential.set {
            Text("no password set").foregroundStyle(.secondary)
        } else {
            switch credential.style {
            case "keychain": Label("In the Keychain", systemImage: "lock.fill")
            case "op": Label(reference, systemImage: "key.fill").lineLimit(1).truncationMode(.middle)
            case "env": Text("env $\(credential.var ?? "")")
            case "proton_bridge": Text("Proton Bridge")
            default: Text("—")
            }
        }
    }

    private var reference: String {
        switch credential.ref {
        case .string(let ref)?: ref
        case .strings(let refs)?: refs.first ?? ""
        case nil: ""
        }
    }
}

/// What a test came to, in the page's words.
struct TestedWords: View {
    let tested: Tested

    var body: some View {
        switch tested {
        case .running:
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("Testing… (approve Touch ID if asked)")
            }
            .foregroundStyle(.secondary)
        case .connected:
            Label("Connected", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed(let why):
            Label(why, systemImage: "xmark.circle.fill").foregroundStyle(.red).lineLimit(2)
        }
    }
}

/// How an account's syncs stand (`HealthLine`): a certificate that changed, which a person may trust; a sync that
/// failed, which they may retry; or when it last synced.
struct HealthRow: View {
    let status: AppStatus
    let email: String
    @State private var busy = false

    var body: some View {
        let line = StatusWords.healthLine(status.health.first { $0.account == email }, now: status.now)
        switch line {
        case .none:
            Text("Not synced yet").foregroundStyle(.secondary)
        case .synced(let when, let count):
            Label(count == 1 ? "Synced \(when) · 1 sync" : "Synced \(when) · \(count) syncs",
                  systemImage: "checkmark.circle")
                .foregroundStyle(.green)
        case .certificateChanged(let when):
            VStack(alignment: .leading, spacing: 6) {
                Label(when.isEmpty ? "Certificate changed" : "Certificate changed · \(when)",
                      systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                SettingHelp(String(localized: """
                    The mail server on this computer showed a different certificate, so MailMind sent no password. If \
                    you reinstalled or reset Proton Bridge, trust the new one.
                    """))
                Button(busy ? "Trusting…" : "Trust It") { act { await status.trust(email) } }
                    .disabled(busy)
            }
        case .syncError(let kind, let when):
            HStack {
                Label(when.isEmpty ? "Sync error · \(kind)" : "Sync error · \(kind) · \(when)",
                      systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                Spacer()
                Button(busy ? "Syncing…" : "Retry") { act { await status.retry(email) } }
                    .disabled(busy)
            }
        }
    }

    private func act(_ run: @escaping @MainActor () async -> Void) {
        busy = true
        Task {
            await run()
            busy = false
        }
    }
}

/// The Proton Bridge's password, which every Proton account logs in with.
struct BridgePasswordView: View {
    let status: AppStatus
    @State private var set: Bool?

    init(status: AppStatus, set: Bool? = nil) {
        self.status = status
        _set = State(initialValue: set)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            KeptSecretView(
                label: String(localized: "Bridge password"),
                prompt: String(localized: "paste the password Proton Bridge shows"),
                standing: set.map { SecretStanding.bridgePassword(set: $0, sources: .ofThisProcess) },
                keep: keep, remove: clear,
                removeWarning: String(localized: "Proton accounts can’t log in until it is kept again, unless the environment or 1Password gives it."))
            SettingHelp(String(localized: "Proton Bridge shows its password under the account’s mailbox details. It is kept in your login Keychain, never in a file."))
        }
        .task { await read() }
    }

    private func read() async {
        guard let core = status.core else { return }
        set = try? await core.get(API.Get.AccountsBridgePassword.self).set
    }

    private func keep(_ typed: String) async -> String? {
        guard let core = status.core else { return nil }
        do {
            set = try await SecretPost.post(core, API.Post.AccountsBridgePassword.self, API.PasswordBody(password: typed)).set
            status.say(String(localized: "Bridge password kept in your Keychain"))
            return nil
        } catch {
            return (error as? Problem)?.description ?? String(localized: "Couldn’t keep it")
        }
    }

    private func clear() async -> String? {
        guard let core = status.core else { return nil }
        do {
            set = try await core.post(API.Post.AccountsBridgePasswordClear.self, API.NoBody()).set
            return nil
        } catch {
            return (error as? Problem)?.description ?? String(localized: "Couldn’t remove it")
        }
    }
}
#endif
