#if os(macOS)
import SwiftUI

/// Settings' Accounts: the email connections, each as the page's `ConnectionRow` shows it, in a card as the mockup's
/// accounts menu lists them, and under it the one chosen. Add one from the list's foot, take one out from its card.
struct AccountsPane: View {
    let status: AppStatus
    @State private var chosen: String?
    @State private var form: Editing?
    @State private var removing: API.AccountRow?
    /// What only this Mac holds of the account being removed, which deleting its mail here lets go of.
    @State private var unsent: Unsent?
    @State private var stats: [String: API.AccountStats] = [:]
    /// The connection just saved, tested as soon as its card shows.
    @State private var testNext: String?
    @Environment(\.meadow) private var meadow

    /// `form`: the form it opens on, as a test opens one.
    init(status: AppStatus, chosen: String? = nil, form: Editing? = nil) {
        self.status = status
        _chosen = State(initialValue: chosen)
        _form = State(initialValue: form)
    }

    /// Which form is open: a new account, the one an address names, or that one opened from its Sync card on what
    /// fixes its trouble (#51).
    enum Editing: Identifiable, Hashable {
        case adding
        case editing(String)
        case fixing(String, AccountFix)
        var id: Self { self }
    }

    var body: some View {
        SettingsPage(tab: .accounts) {
            DeskSideCard(title: status.accounts.count == 1 ? String(localized: "1 connection")
                         : String(localized: "\(status.accounts.count) connections"), icon: .mail) {
                VStack(alignment: .leading, spacing: 2) {
                    if status.accountsRead, status.accounts.isEmpty {
                        Text("No email connections yet.")
                            .font(.meadow(.ui, 13.5))
                            .foregroundStyle(meadow.ink3)
                            .padding(.vertical, 6)
                    }
                    ForEach(Array(status.accounts.enumerated()), id: \.element.email) { index, row in
                        AccountListRow(status: status, row: row, colour: meadow.account(index),
                                       chosen: row.email == chosen) { chosen = row.email }
                    }
                    MeadowPopRule()
                    Button { form = .adding } label: {
                        MeadowPaletteRow(String(localized: "Add an account"), icon: .plus, pop: true)
                    }
                    .buttonStyle(.plain)
                    .help("Add a connection")
                    HostedMailboxSettingsLink()
                }
                .padding(.horizontal, -8)
            }
            if let row = status.accounts.first(where: { $0.email == chosen }) {
                AccountDetail(status: status, row: row, live: stats[row.email ?? ""],
                              edit: { form = .editing(row.email ?? "") }, remove: { removing = row },
                              fix: { form = .fixing(row.email ?? "", $0) },
                              testNow: row.email == testNext, onTested: { testNext = nil })
            } else if !status.accounts.isEmpty {
                SettingHelp(String(localized: "Choose an account to see how it stands."))
                    .padding(.horizontal, 4)
            }
        }
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
            AccountForm(status: status, draft: AccountDraft(editing: editing.row(in: status.accounts),
                                                            fixing: editing.fix)) { saved in
                form = nil
                if let saved {
                    chosen = saved
                    // A fix was tested as it was saved, and its account syncs again.
                    if editing.fix == nil { testNext = saved }
                }
            }
        }
        .sheet(isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } })) {
            if let row = removing {
                RemoveAccount(row: row, unsent: unsent) { deleteMail in
                    removing = nil
                    if let deleteMail { Task { await remove(row, deleteMail: deleteMail) } }
                }
            }
        }
        .task(id: removing?.email) {
            unsent = nil
            guard let email = removing?.email, let core = status.core else { return }
            unsent = await Unsent.read(core, of: email)
        }
    }

    private func remove(_ row: API.AccountRow, deleteMail: Bool) async {
        guard let core = status.core, let email = row.email else { return }
        do {
            _ = try await core.post(API.Post.AccountsDelete.self, API.RemoveBody(deleteMail: deleteMail, email: email))
            status.say(deleteMail ? String(localized: "Removed \(StatusWords.short(email)) and its mail on this Mac")
                                  : String(localized: "Removed \(StatusWords.short(email))"))
            if chosen == email { chosen = nil }
        } catch let problem as Problem where problem.status == 404 {
            status.say(problem.detail ?? String(localized: "Couldn’t remove"), refused: true)
        } catch {
            status.say(String(localized: "Remove failed"), refused: true)
        }
        await status.readAccounts()
        await status.readDigest()
        // Writing lets go of it too: it is no longer offered to send from.
        await Composing.shared.readDigest()
        // The window's rail lets go of it now, and its list with it; not waited for.
        MailApp.shared.accountsChanged()
        if chosen == nil { chosen = status.accounts.first?.email }
    }
}

/// What only this Mac holds of an account: sends that haven't gone (waiting, scheduled or failed) and drafts that
/// haven't reached its server yet, as the outbox tray lists them. Deleting the account's mail from this Mac lets go
/// of them for good.
struct Unsent: Equatable {
    var sends = 0
    var drafts = 0

    /// What the tray lists of `account`.
    static func of(_ outbox: API.Outbox, account: String) -> Unsent {
        let mine = { (other: String) in other.caseInsensitiveCompare(account) == .orderedSame }
        let going: Set<String> = ["queued", "sending", "error"]
        return Unsent(sends: outbox.items.filter { mine($0.account) && going.contains($0.state) }.count,
                      drafts: outbox.drafts.filter { mine($0.account) && !$0.discarding }.count)
    }

    /// The tray as the core lists it now, or nil when it didn't answer.
    static func read(_ core: Core, of account: String) async -> Unsent? {
        guard let outbox = try? await core.get(API.Get.Outbox.self, ["limit": "500"]) else { return nil }
        return of(outbox, account: account)
    }

    /// What the Remove dialog warns of, or nil when nothing waits.
    var words: String? {
        let sends = switch sends {
        case 0: ""
        case 1: String(localized: "1 message that hasn’t been sent")
        default: String(localized: "\(sends) messages that haven’t been sent")
        }
        let drafts = switch drafts {
        case 0: ""
        case 1: String(localized: "1 draft that isn’t on the server yet")
        default: String(localized: "\(drafts) drafts that aren’t on the server yet")
        }
        let both = [sends, drafts].filter { !$0.isEmpty }
        guard !both.isEmpty else { return nil }
        let what = both.joined(separator: String(localized: " and "))
        return self.sends + self.drafts == 1 ? String(localized: "This also deletes \(what). It’s only on this Mac.")
                                             : String(localized: "This also deletes \(what). They’re only on this Mac.")
    }
}

/// Removing an account, as Meadow's dialog: MailMind stops reading it and lets go of its password, and, unless
/// unticked, of the mail it keeps of it on this Mac: with a warning when that holds sends or drafts only this Mac has,
/// and a word that MailMind's backups keep their copies: an upgrade's until newer ones replace them, Start Fresh's until
/// a person deletes it. Its mail on the server stays as it is.
struct RemoveAccount: View {
    let row: API.AccountRow
    /// What only this Mac holds of it, once read.
    var unsent: Unsent?
    /// Done: whether its mail here goes too, or nil when the removal was cancelled.
    let done: (Bool?) -> Void
    @State private var deleteMail = true
    @Environment(\.meadow) private var meadow

    var body: some View {
        let email = row.email ?? ""
        VStack(alignment: .leading, spacing: 12) {
            Text("Remove \(row.alias.flatMap { $0.isEmpty ? nil : $0 } ?? email)?")
                .font(.meadow(.display, 18, .bold))
                .foregroundStyle(meadow.ink)
                .lineLimit(1)
                .accessibilityAddTraits(.isHeader)
            Text("MailMind stops reading \(email) and lets go of its password.")
                .font(.meadow(.ui, 13.5))
                .foregroundStyle(meadow.ink2)
                .fixedSize(horizontal: false, vertical: true)
            Toggle("Also delete its mail from this Mac", isOn: $deleteMail)
                .toggleStyle(.checkbox)
                .font(.meadow(.ui, 13.5, .semibold))
                .foregroundStyle(meadow.ink)
            if deleteMail, let warning = unsent?.words {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    MeadowIconView(.alert, size: 15)
                    Text(warning).fixedSize(horizontal: false, vertical: true)
                }
                .font(.meadow(.ui, 13, .semibold))
                .foregroundStyle(meadow.rust)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(meadow.rustWash, in: RoundedRectangle(cornerRadius: 10))
                .accessibilityElement(children: .combine)
            }
            SettingHelp(String(localized: "Its mail on the server stays as it is."))
            if deleteMail {
                SettingHelp(Self.backups)
            }
            HStack(spacing: 8) {
                Spacer()
                Button("Cancel") { done(nil) }
                    .meadowButton()
                    .keyboardShortcut(.cancelAction)
                Button("Remove") { done(deleteMail) }
                    .meadowButton(.danger)
                    .keyboardShortcut(.defaultAction)
            }
            .padding(.top, 4)
        }
        .padding(20)
        .frame(width: 420)
        .background { ComposePaper() }
        .presentationBackground { ComposePaper() }
    }

    /// Said while its mail here is to go: the copies an upgrade or Start Fresh kept aren't touched. An upgrade's copy
    /// gives way to newer ones in time; a copy Start Fresh kept is never replaced, and stays until a person deletes it.
    static let backups = String(localized: """
        Copies of its mail in MailMind’s backups folder stay there: newer backups replace them in time, and a copy \
        Start Fresh kept stays until you delete it.
        """)
}

extension AccountsPane.Editing {
    func row(in accounts: [API.AccountRow]) -> API.AccountRow? {
        switch self {
        case .adding: nil
        case .editing(let email), .fixing(let email, _): accounts.first { $0.email == email }
        }
    }

    /// What the sheet is opened to fix, if anything.
    var fix: AccountFix? {
        if case .fixing(_, let fix) = self { fix } else { nil }
    }
}

/// An account in the list (`.gq`): its avatar with its colour's dot, its name and address, and how it stands; the
/// one chosen in the indigo wash.
struct AccountListRow: View {
    let status: AppStatus
    let row: API.AccountRow
    var colour: Color = .clear
    var chosen = false
    var choose: () -> Void = {}
    @Environment(\.meadow) private var meadow
    @FocusState private var focused: Bool
    @State private var hovering = false

    var body: some View {
        let email = row.email ?? ""
        let standing = StatusWords.standing(of: email, digest: status.digest, health: status.health)
        Button(action: choose) {
            HStack(spacing: 10) {
                MeadowAvatar(name: label, account: colour, chosen: chosen)
                VStack(alignment: .leading, spacing: 1) {
                    Text(label).font(.meadow(.ui, 13.5, .bold)).foregroundStyle(meadow.ink)
                    Text(email).font(.meadow(.ui, 12.5)).foregroundStyle(meadow.ink3)
                }
                .lineLimit(1)
                Spacer(minLength: 8)
                if row.disabled {
                    MeadowChip(String(localized: "Disabled"), tone: .line)
                } else if standing != .unknown {
                    HStack(spacing: 6) {
                        SettingDot(colour: standing.colour(meadow))
                        Text(standing.words)
                    }
                    .font(.meadow(.ui, 12))
                    .foregroundStyle(meadow.ink3)
                }
            }
            .padding(8)
            .background(RoundedRectangle(cornerRadius: 10)
                .fill(chosen ? meadow.primaryWash : hovering ? meadow.paper2 : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 10)
        .onHover { hovering = $0 }
        .opacity(row.disabled ? 0.6 : 1)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(chosen ? .isSelected : [])
    }

    private var label: String {
        if let alias = row.alias, !alias.isEmpty { return alias }
        return StatusWords.short(row.email ?? "")
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

    /// Its dot in Meadow's colours: moss when well, sunlight while busy, rust when it can't connect.
    func colour(_ meadow: MeadowPalette) -> Color {
        switch self {
        case .unknown: meadow.ink3
        case .connected: meadow.moss
        case .syncing: meadow.sun
        case .cantConnect: meadow.rust
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

/// The account chosen: how it is reached, how its syncs stand, its Inbox on the server, and what can be done with it,
/// each in a card of its own.
struct AccountDetail: View {
    let status: AppStatus
    let row: API.AccountRow
    let live: API.AccountStats?
    let edit: () -> Void
    let remove: () -> Void
    /// Open the Edit sheet on what fixes the sync's trouble (#51).
    var fix: (AccountFix) -> Void = { _ in }
    /// Whether to test it as it shows, as one just saved is; `onTested` says it has been.
    var testNow = false
    var onTested: () -> Void = {}
    @State private var tested: Tested?
    /// The kind of sync trouble the test came to (`Tested.kind(of:)`).
    @State private var testedKind: String?
    /// What the server says of the address, for an Other IMAP account whose password was turned down or isn't kept: its
    /// provider's page for an app password, where it has one.
    @State private var found: API.PresetFound?
    @Environment(\.meadow) private var meadow

    /// The kind of the sync's trouble the card shows, or nil while it shows none.
    private var standing: String? {
        let record = status.health.first { $0.account == row.email }
        return switch StatusWords.healthLine(record, now: status.now) {
        case .syncError(let kind, _): kind
        case .certificateChanged: "TLS_PIN"
        default: nil
        }
    }

    private var providerPage: API.Link? { AccountFix.providerPage(found, host: row.host) }

    var body: some View {
        let email = row.email ?? ""
        VStack(alignment: .leading, spacing: 14) {
            DeskSideCard(title: String(localized: "Connection"), icon: .at) {
                VStack(spacing: 0) {
                    MeadowFact(String(localized: "Name")) {
                        Text(row.alias.flatMap { $0.isEmpty ? nil : $0 }
                             ?? String(localized: "\(StatusWords.short(email)) · no alias"))
                            .foregroundStyle(row.alias?.isEmpty == false ? meadow.ink : meadow.ink3)
                    }
                    MeadowFact(String(localized: "Address")) { Text(email).textSelection(.enabled) }
                    MeadowFact(String(localized: "Type")) {
                        Text(AccountDraft.Kind(rawValue: row.type)?.label ?? row.type)
                    }
                    if row.type == "imap" {
                        MeadowFact(String(localized: "Server")) {
                            Text(verbatim: "\(row.host ?? ""):\(String(row.port))\(row.starttls ? " · STARTTLS" : "")")
                        }
                    }
                    MeadowFact(String(localized: "Password"), last: !row.disabled && live == nil) {
                        CredentialWords(credential: row.credential)
                    }
                    if row.disabled {
                        MeadowFact(String(localized: "Status"), last: live == nil) { Text("Disabled") }
                    }
                    if let live {
                        MeadowFact(String(localized: "Inbox"), last: true) {
                            if let error = live.error, !error.isEmpty {
                                Text(error).foregroundStyle(meadow.rust).lineLimit(1)
                            } else {
                                Text(live.inInbox.formatted()).monospacedDigit()
                            }
                        }
                    }
                }
                HStack(spacing: 6) {
                    Button { edit() } label: {
                        MeadowIconView(.pen, size: 15)
                        Text("Edit…")
                    }
                    .meadowButton(small: true)
                    Button { remove() } label: {
                        MeadowIconView(.trash, size: 15)
                        Text("Remove…")
                    }
                    .meadowButton(del: true, small: true)
                }
                .padding(.top, 2)
            }
            DeskSideCard(title: String(localized: "Sync"), icon: .refresh) {
                HealthRow(status: status, email: email, providerPage: providerPage, fix: fix)
                HStack(spacing: 10) {
                    Button("Test Connection") { Task { await test(email) } }
                        .meadowButton(small: true)
                        .disabled(tested == .running)
                    if let tested {
                        let record = status.health.first { $0.account == email }
                        let page = standing.flatMap {
                            StatusWords.syncHelp($0, type: row.type, error: record?.lastError, provider: providerPage)
                        }
                        CardTestedWords(shown: .of(tested, kind: testedKind, standing: standing, type: row.type,
                                                   page: page))
                    }
                    Spacer(minLength: 0)
                }
            }
            if !row.signature.isEmpty {
                DeskSideCard(title: String(localized: "Signature"), icon: .pen) {
                    Text(row.signature)
                        .font(.meadow(.read, 14))
                        .foregroundStyle(meadow.ink2)
                        .textSelection(.enabled)
                }
            }
            if row.type == "proton" {
                DeskSideCard(title: String(localized: "Proton Bridge password"), icon: .key) {
                    BridgePasswordView(status: status)
                }
            }
        }
        .id(email)
        .task(id: testNow) {
            guard testNow else { return }
            await test(email)
            onTested()
        }
        .onChange(of: standing) {
            // A test spoke of the trouble the card showed then: once the sync's trouble is fixed or another, it goes.
            if tested != .running {
                tested = nil
                testedKind = nil
            }
        }
        .task(id: row.type == "imap" && Self.wantsPassword(standing) ? row.email : nil) {
            // Only for a password turned down or none kept: the server may ask the address's domain which provider
            // takes its mail. Asked again for each account the card shows, which never shows another's provider.
            found = nil
            guard row.type == "imap", Self.wantsPassword(standing), let core = status.core else { return }
            found = try? await core.get(API.Get.AccountsPreset.self, ["email": email])
        }
    }

    /// Whether a sync's trouble of `kind` is one a password typed again fixes: turned down, or none kept.
    static func wantsPassword(_ kind: String?) -> Bool {
        kind == "AUTH" || kind == "NO_PASSWORD"
    }

    private func test(_ email: String) async {
        guard let core = status.core else { return }
        tested = .running
        testedKind = nil
        do {
            let answer = try await core.post(API.Post.AccountsTest.self, API.TryBody(email: email))
            testedKind = Tested.kind(of: answer)
            tested = .of(answer)
        } catch {
            tested = .of(error)
        }
    }
}

/// What a test says on the Sync card (`TestedOnCard`): only that it failed the same way, when the card already says
/// how; else as `TestedWords` says it, without the trouble's class.
struct CardTestedWords: View {
    let shown: TestedOnCard
    @Environment(\.meadow) private var meadow

    /// What a test that failed as the sync did says.
    static let same = String(localized: "Still failing the same way.")

    var body: some View {
        switch shown {
        case .running: TestedWords(tested: .running)
        case .connected: TestedWords(tested: .connected)
        case .same:
            Text(Self.same)
                .font(.meadow(.ui, 13, .semibold))
                .foregroundStyle(meadow.ink3)
        case .other(let why, let link): TestedWords(tested: .failed(why, link: link))
        }
    }
}

/// How an account's password is kept, never the password (`Credentials`).
struct CredentialWords: View {
    let credential: API.CredentialShown
    @Environment(\.meadow) private var meadow

    var body: some View {
        if !credential.set {
            Text("no password set").foregroundStyle(meadow.ink3)
        } else {
            switch credential.style {
            case "keychain": labelled(.lock, String(localized: "In the Keychain"))
            case "op": labelled(.key, reference).truncationMode(.middle)
            case "env": Text(verbatim: "env $\(credential.var ?? "")")
            case "proton_bridge": Text("Proton Bridge")
            default: Text(verbatim: "—")
            }
        }
    }

    private func labelled(_ icon: MeadowIcon, _ words: String) -> some View {
        HStack(spacing: 6) {
            MeadowIconView(icon, size: 14).foregroundStyle(meadow.ink3)
            Text(words).lineLimit(1)
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

/// What a test came to: connected, or what to do next, with the page that helps and the kind of trouble in plain words
/// in small type.
struct TestedWords: View {
    let tested: Tested
    @Environment(\.meadow) private var meadow

    /// While a test runs. A new account's password is in the Keychain, which MailMind reads with no prompt, so there
    /// is nothing to approve.
    static let testing = String(localized: "Testing…")

    var body: some View {
        Group {
            switch tested {
            case .running:
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text(Self.testing)
                }
                .foregroundStyle(meadow.ink3)
            case .connected:
                HStack(spacing: 6) {
                    MeadowIconView(.checkCircle, size: 15)
                    Text("Connected")
                }
                .foregroundStyle(meadow.moss)
            case .failed(let why, let link, let detail):
                VStack(alignment: .leading, spacing: 6) {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        MeadowIconView(.alert, size: 15)
                        Text(why).lineLimit(4).fixedSize(horizontal: false, vertical: true)
                    }
                    .foregroundStyle(meadow.rust)
                    if let link { LinkButton(link: link) }
                    // What kind of trouble it was, in the Sync card's plain words: never a server's code.
                    if let kind = Tested.plainWords(of: detail) {
                        Text(kind)
                            .font(.meadow(.ui, 11))
                            .foregroundStyle(meadow.ink3)
                            .textSelection(.enabled)
                    }
                }
            }
        }
        .font(.meadow(.ui, 13, .semibold))
    }
}

/// How an account's syncs stand (`HealthLine`): a certificate that changed, which a person may trust; a sync that
/// failed, which they may retry or fix; or when it last synced.
struct HealthRow: View {
    let status: AppStatus
    let email: String
    /// Where an Other IMAP account's provider makes app passwords, where it does (`AccountFix.providerPage`).
    var providerPage: API.Link?
    /// Open the account's Edit sheet on what fixes its trouble.
    var fix: (AccountFix) -> Void = { _ in }
    @State private var busy = false
    @Environment(\.meadow) private var meadow

    var body: some View {
        let record = status.health.first { $0.account == email }
        let line = StatusWords.healthLine(record, now: status.now)
        Group {
            switch line {
            case .none:
                Text("Not synced yet").foregroundStyle(meadow.ink3)
            case .synced(let when, let count):
                HStack(spacing: 8) {
                    SettingDot(colour: meadow.moss)
                    Text(count == 1 ? "Synced \(when) · 1 sync" : "Synced \(when) · \(count) syncs")
                }
                .foregroundStyle(meadow.ink2)
            case .certificateChanged(let when):
                VStack(alignment: .leading, spacing: 8) {
                    warning(when.isEmpty ? String(localized: "Certificate changed")
                            : String(localized: "Certificate changed · \(when)"))
                    SettingHelp(String(localized: """
                        The mail server on this computer showed a different certificate, so MailMind sent no \
                        password. If you reinstalled or reset Proton Bridge, trust the new one.
                        """))
                    Button(busy ? "Trusting…" : "Trust It") { act { await status.trust(email) } }
                        .meadowButton(.danger, small: true)
                        .disabled(busy)
                }
            case .syncError(let kind, let when):
                // What went wrong, said once (#51): a sentence with what to do, the page that helps, and the button
                // that opens the sheet on what fixes it. The trouble's kind and class are Copy Diagnostics' to carry.
                let account = status.accounts.first { $0.email == email }
                let words = Self.words(kind: kind, error: record?.lastError, type: account?.type,
                                       credential: account?.credential.style, providerPage: providerPage)
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        warning(when.isEmpty ? String(localized: "Couldn’t sync")
                                : String(localized: "Couldn’t sync · \(when)"))
                        Spacer()
                        Button(busy ? "Syncing…" : "Retry") { act { await status.retry(email) } }
                            .meadowButton(small: true)
                            .disabled(busy)
                    }
                    SettingHelp(words.text)
                    if words.link != nil || words.fix != nil {
                        HStack(spacing: 8) {
                            if let link = words.link { LinkButton(link: link) }
                            if let fixing = words.fix {
                                Button(fixing.button) { fix(fixing) }
                                    .meadowButton(.primary, small: true)
                            }
                        }
                    }
                }
            }
        }
        .font(.meadow(.ui, 13.5, .semibold))
    }

    /// What a sync's trouble of `kind` and class `error` says on the card of an account of `type` whose password is
    /// kept as `credential`: its sentence, the page that helps, and the fix its button opens the sheet on.
    static func words(kind: String, error: String?, type: String?, credential: String?, providerPage: API.Link?)
        -> (text: String, link: API.Link?, fix: AccountFix?)
    {
        let fix = AccountFix.of(kind: kind, type: type, error: error, credential: credential,
                                appPasswords: providerPage != nil)
        return (StatusWords.syncProblem(kind, type: type, error: error, fix: fix),
                StatusWords.syncHelp(kind, type: type, error: error, provider: providerPage), fix)
    }

    /// A line on what went wrong, as the mockup's warning (`.trust.warn`): rust on its wash.
    private func warning(_ words: String) -> some View {
        HStack(spacing: 8) {
            MeadowIconView(.alert, size: 15)
            Text(words)
        }
        .foregroundStyle(meadow.rust)
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(meadow.rustWash, in: RoundedRectangle(cornerRadius: 10))
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
