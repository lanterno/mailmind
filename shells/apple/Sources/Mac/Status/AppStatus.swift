#if os(macOS)
import Foundation
import Observation

/// What the app knows about itself, for the status, Settings and the first run: the accounts and how each stands, the
/// digest's word on syncing and AI, the preferences the server keeps, a sync or reconcile a person started, and the
/// meeting about to start. One for the app: the window hands it the core once it is open, and Settings reads it.
///
/// It hears the event stream on a stream of its own and reads again what an event touches, as the page's realtime.ts
/// does for the digest.
@MainActor
@Observable
final class AppStatus {
    static let shared = AppStatus()

    private(set) var core: Core?
    private(set) var digest: API.Digest?
    /// The accounts as Settings shows them, and whether the first read of them has come.
    private(set) var accounts: [API.AccountRow] = []
    private(set) var accountsRead = false
    private(set) var signatureDefault = ""
    /// What each account's syncs came to (`/api/account-health`).
    private(set) var health: [API.AccountStanding] = []
    /// What a person has set, as the server holds it; nil until it has answered.
    private(set) var preferences: API.Preferences?
    private(set) var syncing = false
    private(set) var reconciling = false
    /// A few words on what just happened (a sync done, a setting refused), shown for a moment.
    private(set) var notice: Notice?
    /// The meetings coming up (`/api/upcoming`), read each minute.
    private(set) var upcoming: [API.UpcomingEvent] = []
    /// The minute it is, for the words that count down.
    private(set) var now = Date()
    /// The accounts whose alert across the top a person closed.
    var alertsClosed: Set<String> = []
    /// The meetings whose reminder a person closed.
    var soonClosed: Set<String> = []
    /// The tab Settings shows.
    var settingsTab = SettingsTab.accounts
    /// Whether the guide is showing.
    var guiding = false
    /// Whether the keyboard's legend is over the window.
    var showingKeys = false

    /// New mail's notifications and the Dock's badge.
    let newMail: NewMailAlerts
    /// What opens Curate from the status menu, when the window offers it.
    @ObservationIgnored var curate: (@MainActor () -> Void)?

    @ObservationIgnored private var listening: Task<Void, Never>?
    @ObservationIgnored private var ticking: Task<Void, Never>?
    @ObservationIgnored private var digestSoon: Task<Void, Never>?
    @ObservationIgnored private var noticeGoes: Task<Void, Never>?

    struct Notice: Equatable, Identifiable {
        let id = UUID()
        let text: String
    }

    init(newMail: NewMailAlerts = NewMailAlerts()) {
        self.newMail = newMail
        newMail.preferences = { [weak self] in self?.preferences }
        newMail.name = { [weak self] in self?.name($0) ?? StatusWords.short($0) }
        newMail.now = { [weak self] in self?.now ?? Date() }
        newMail.hasAccounts = { [weak self] in self?.accounts.isEmpty == false }
    }

    /// What a test or a rendering shows with no core: the answers the routes would give.
    func hold(digest: API.Digest? = nil, accounts: [API.AccountRow] = [], health: [API.AccountStanding] = [],
              preferences: API.Preferences? = nil, upcoming: [API.UpcomingEvent] = [], now: Date = Date())
    {
        self.digest = digest
        self.accounts = accounts
        accountsRead = true
        self.health = health
        self.preferences = preferences
        self.upcoming = upcoming
        self.now = now
    }

    /// The core is open: read what the status shows, then listen and keep the minute. Once.
    func attach(_ core: Core) async {
        guard self.core == nil else { return }
        self.core = core
        listen(to: core)
        tick()
        async let accounts: Void = readAccounts()
        async let digest: Void = readDigest()
        async let preferences: Void = readPreferences()
        _ = await (accounts, digest, preferences)
        await readUpcoming()
        newMail.meetingSoon(soon)
        newMail.askOnce()
    }

    /// Stop listening and ticking.
    func detach() {
        listening?.cancel()
        ticking?.cancel()
        digestSoon?.cancel()
        listening = nil
        ticking = nil
    }

    // MARK: Reading

    func readDigest() async {
        guard let core else { return }
        if let read = try? await core.get(API.Get.Digest.self) { digest = read }
    }

    /// The accounts, then how each stands.
    func readAccounts() async {
        guard let core else { return }
        if let listed = try? await core.get(API.Get.Accounts.self) {
            accounts = listed.accounts
            signatureDefault = listed.signatureDefault
            accountsRead = true
        }
        await readHealth()
    }

    func readHealth() async {
        guard let core else { return }
        if let read = try? await core.get(API.Get.AccountHealth.self) { health = read.accounts }
    }

    func readPreferences() async {
        guard let core else { return }
        if let read = try? await core.get(API.Get.Preferences.self) { preferences = read.preferences }
    }

    func readUpcoming() async {
        guard let core else { return }
        if let read = try? await core.get(API.Get.Upcoming.self) { upcoming = read.items }
    }

    /// The digest again, a moment after a burst of events, as the page reads it once for many.
    private func digestAgain() {
        digestSoon?.cancel()
        digestSoon = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(400))
            guard !Task.isCancelled else { return }
            await self?.readDigest()
        }
    }

    // MARK: Preferences

    /// Change what `changes` names: here at once, on the server right after. A value the server refuses puts back what
    /// it holds, and its words say why (`setPrefs`).
    @discardableResult
    func setPreferences(_ changes: API.ChangedPreferences) -> Task<Void, Never>? {
        if let held = preferences, let merged = Self.merged(held, changes) { preferences = merged }
        guard let core else { return nil }
        return Task {
            do {
                preferences = try await core.post(API.Post.Preferences.self, changes).preferences
                if Self.touchesAI(changes) { await aiChanged() }
            } catch {
                say((error as? Problem)?.description ?? String(localized: "Couldn’t save that setting"))
                await readPreferences()
            }
        }
    }

    /// Whether a change is one of AI's: the switch, the provider, its model or URL, or the daily limit.
    static func touchesAI(_ changes: API.ChangedPreferences) -> Bool {
        changes.aiEnabled != nil || changes.aiProvider != nil || changes.aiModel != nil || changes.aiBaseUrl != nil
            || changes.aiDailyLimitUsd != nil
    }

    /// AI's settings or a key changed: read how AI stands again, and tell the window, whose cards and composers read it
    /// on their own and hear of nothing else when Settings changes it.
    func aiChanged() async {
        await readDigest()
        NotificationCenter.default.post(name: .mailmindAIChanged, object: nil)
    }

    /// `held` with `changes` laid over it: what a change names wins, and what it leaves out stays. Another provider
    /// named without a model or URL starts from its own, as the server sets them back to "".
    static func merged(_ held: API.Preferences, _ changes: API.ChangedPreferences) -> API.Preferences? {
        let encoder = JSONEncoder()
        guard case .object(var all)? = try? JSONDecoder().decode(JSONValue.self, from: encoder.encode(held)),
              case .object(let changed)? = try? JSONDecoder().decode(JSONValue.self, from: encoder.encode(changes))
        else { return nil }
        for (name, value) in changed where value != .null {
            all[name] = value
        }
        if let provider = changes.aiProvider, provider != held.aiProvider {
            if changes.aiModel == nil { all["ai_model"] = .string("") }
            if changes.aiBaseUrl == nil { all["ai_base_url"] = .string("") }
        }
        return try? JSONDecoder().decode(API.Preferences.self, from: encoder.encode(JSONValue.object(all)))
    }

    // MARK: What a person starts

    /// Sync every account now, and say so until each has synced or failed again (`syncNow`). The Rust server's digest
    /// says nothing of a sync running yet, so the end is read off each account's record of its syncs.
    func syncNow() async {
        guard let core, !syncing else { return }
        syncing = true
        say(String(localized: "Syncing…"))
        await readHealth()
        let before = health
        _ = try? await core.post(API.Post.Refresh.self, API.NoBody())
        for _ in 0..<40 {
            try? await Task.sleep(for: .seconds(3))
            await readHealth()
            await readDigest()
            if Self.syncEnded(before: before, after: health, accounts: digest?.accounts ?? accounts.compactMap(\.email),
                              running: digest?.sync.running ?? false) { break }
        }
        syncing = false
        say(String(localized: "Sync complete"))
    }

    /// Whether a sync asked for has ended: nothing says one is running, and every account has a sync recorded since.
    static func syncEnded(before: [API.AccountStanding], after: [API.AccountStanding], accounts: [String],
                          running: Bool) -> Bool
    {
        guard !running else { return false }
        func mark(_ records: [API.AccountStanding], _ account: String) -> [String?] {
            let record = records.first { $0.account == account }
            return [record?.lastOkTs, record?.lastErrorTs, record.map { String($0.syncCount) }]
        }
        return accounts.allSatisfy { mark(before, $0) != mark(after, $0) }
    }

    /// Check the mail kept here against the servers, and let go of what was deleted elsewhere: a dry run first, then
    /// the real one only when it finds something (`reconcile`).
    func reconcile() async {
        guard let core, !reconciling else { return }
        reconciling = true
        defer { reconciling = false }
        do {
            let dry = try await core.post(API.Post.Reconcile.self, API.ReconcileBody(account: "all", dryRun: true))
            guard dry.totalGhosts > 0 else {
                say(String(localized: "Your mail here matches the servers."))
                return
            }
            say(String(localized: "Removing \(dry.totalGhosts) messages deleted elsewhere…"))
            let real = try await core.post(API.Post.Reconcile.self, API.ReconcileBody(account: "all", dryRun: false))
            await readDigest()
            let kept = digest?.stats.kept ?? 0
            say(String(localized: "Removed \(real.totalPruned.formatted()) messages deleted elsewhere · \(kept.formatted()) here"))
        } catch {
            say(String(localized: "Couldn’t check mail against the server. Try again later."))
        }
    }

    /// Sync one account again after it failed (`HealthLine`'s Retry).
    func retry(_ account: String) async {
        guard let core else { return }
        do {
            _ = try await core.post(API.Post.SyncRetryAccount.self, API.SyncRetryBody(account: account))
            say(String(localized: "Re-syncing \(StatusWords.short(account))"))
            try? await Task.sleep(for: .seconds(2.5))
        } catch {
            say(String(localized: "Couldn’t retry"))
        }
        await readHealth()
    }

    /// Trust the certificate a mail server on this computer shows now (`HealthLine`'s Trust it).
    func trust(_ account: String) async {
        guard let core else { return }
        do {
            _ = try await core.post(API.Post.AccountsTrustCert.self, API.NamesAnAccount(email: account))
            say(String(localized: "Trusted · re-syncing \(StatusWords.short(account))"))
            try? await Task.sleep(for: .seconds(2.5))
        } catch {
            say(String(localized: "Couldn’t trust it"))
        }
        await readHealth()
    }

    /// Show `text` for a moment, and tell VoiceOver.
    func say(_ text: String) {
        let notice = Notice(text: text)
        self.notice = notice
        Announce.say(text)
        noticeGoes?.cancel()
        noticeGoes = Task { [weak self] in
            try? await Task.sleep(for: .seconds(4))
            guard !Task.isCancelled, self?.notice == notice else { return }
            self?.notice = nil
        }
    }

    // MARK: What the status shows

    var pill: StatusWords.Pill {
        StatusWords.pill(digest: digest, health: health, syncing: syncing, reconciling: reconciling, now: now)
    }

    /// The addresses of the accounts, as the digest lists them, else as Settings does.
    var addresses: [String] {
        digest?.accounts ?? accounts.compactMap(\.email)
    }

    func name(_ account: String) -> String {
        if let row = accounts.first(where: { $0.email == account }), let alias = row.alias, !alias.isEmpty {
            return alias
        }
        return StatusWords.name(account, aliases: digest?.aliases ?? [:])
    }

    /// The accounts that can't connect and whose alert nobody closed.
    var alerting: [String] {
        StatusWords.bad(addresses, digest: digest, health: health).filter { !alertsClosed.contains($0) }
    }

    /// The first run: the accounts have been read, and there are none.
    var firstRun: Bool { accountsRead && accounts.isEmpty }

    /// The meeting a reminder is for now, if any.
    var soon: Soon.Meeting? {
        Soon.next(upcoming, now: now, closed: soonClosed)
    }

    // MARK: Listening

    private func listen(to core: Core) {
        listening = Task { [weak self] in
            guard let events = try? await core.events() else { return }
            for await event in events {
                guard let self else { return }
                await self.heard(event)
            }
        }
    }

    private func heard(_ event: CoreEvent) async {
        switch event.name {
        case "sync-done", "folder-synced":
            digestAgain()
            await readHealth()
        case "new-mail":
            digestAgain()
            if let said = event.decoded(NewMailAlerts.Arrived.self) { newMail.arrived(said) }
        case "snooze-wake", "mute", "reconcile", "flags-changed", "followup", "undo-done", "commands-changed", "resync":
            digestAgain()
        default:
            break
        }
    }

    /// Keep the minute, and read what is coming up each one, as the page's reminder does.
    private func tick() {
        ticking = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(60))
                guard let self, !Task.isCancelled else { return }
                self.now = Date()
                await self.readUpcoming()
                self.newMail.meetingSoon(self.soon)
            }
        }
    }
}

/// The tabs of Settings (⌘,), which the status opens at Accounts when one can't connect.
enum SettingsTab: String, Hashable, Sendable {
    case accounts, general, notifications, ai
}

extension Notification.Name {
    /// AI's settings or a key changed in Settings, and the digest was read again (`AppStatus.aiChanged`).
    static let mailmindAIChanged = Notification.Name("io.github.lanterno.mailmind.ai-changed")
}
#endif
