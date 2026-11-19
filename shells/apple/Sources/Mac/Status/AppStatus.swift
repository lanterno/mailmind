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
    /// The background jobs whose last round failed, read as the status menu opens.
    private(set) var failingJobs: [String] = []
    /// What a person has set, as the server holds it; nil until it has answered.
    private(set) var preferences: API.Preferences?
    private(set) var syncing = false
    private(set) var reconciling = false
    /// A few words on what just happened (a sync done, a setting refused), shown for a moment.
    private(set) var notice: Notice?
    /// The meetings coming up (`/api/upcoming`), read each minute.
    private(set) var upcoming: [API.UpcomingEvent] = []
    /// The minute it is, for the words that count down, and the hour Automatic's light goes by.
    private(set) var now = Date()
    /// Whether macOS is in Dark Mode, which makes Automatic Night: the app's status follows it from launch
    /// (`followSystemAppearance`); a test sets it.
    var systemDark = false
    @ObservationIgnored var appearanceChanged: (any NSObjectProtocol)?
    /// Whether macOS's Increase Contrast is on, which puts every light in high contrast (`highContrast`), followed as
    /// Dark Mode is; a test sets it.
    var systemIncreasesContrast = false
    @ObservationIgnored var contrastChanged: (any NSObjectProtocol)?
    /// The accounts whose alert across the top a person closed.
    var alertsClosed: Set<String> = []
    /// The meetings whose reminder a person closed.
    var soonClosed: Set<String> = []
    /// The tab Settings shows.
    var settingsTab = SettingsTab.accounts
    /// Whether the guide is showing.
    var guiding = false
    /// Whether the first run's account sheet is open. It is the status's, not the welcome's: the welcome goes as soon
    /// as an account is saved, and a Proton account's sheet stays open after that, on its Bridge test, until Done.
    var addingFirstAccount = false
    /// Whether the keyboard's legend is over the window.
    var showingKeys = false

    /// New mail's notifications and the Dock's badge.
    let newMail: NewMailAlerts
    /// The message a notification's click opens, until the mail window has opened it (`NotificationOpener`).
    var opening: MailCopy?
    /// How many mail windows are open, and what opens one again when none is.
    @ObservationIgnored var mailWindows = 0
    @ObservationIgnored var reopenWindow: (@MainActor () -> Void)?
    /// What opens Curate from the status menu, when the window offers it.
    @ObservationIgnored var curate: (@MainActor () -> Void)?

    /// What reads the digest when no core is open: a test's routes, which answer as the core would.
    @ObservationIgnored var wire: Wire?
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
        newMail.lookUp = { [weak self] account, folder, uid in await self?.row(account: account, folder: folder, uid: uid) }
    }

    /// The row of a message just come, by its UID, as its folder lists it (`Lists.row`), however far down its folder:
    /// a sync can bring mail dated earlier than the newest already there.
    func row(account: String, folder: String, uid: Int) async -> API.FolderRow? {
        guard let core else { return nil }
        return await Lists.row(Wire(core), account: account, folder: folder, uid: uid)
    }

    /// What a test or a rendering shows with no core: the answers the routes would give.
    func hold(digest: API.Digest? = nil, accounts: [API.AccountRow] = [], health: [API.AccountStanding] = [],
              preferences: API.Preferences? = nil, upcoming: [API.UpcomingEvent] = [], failingJobs: [String] = [],
              now: Date = Date())
    {
        self.digest = digest
        self.accounts = accounts
        accountsRead = true
        self.health = health
        self.preferences = preferences
        self.upcoming = upcoming
        self.failingJobs = failingJobs
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
        digestSoon = nil
    }

    // MARK: Reading

    func readDigest() async {
        guard let wire = wire ?? core.map(Wire.init) else { return }
        if let read = try? await wire.get(API.Get.Digest.self) { digest = read }
    }

    /// The accounts, then how each stands. Once there is an account to hear from, macOS is asked whether MailMind may
    /// notify: at launch with one already there, or as the first is added, so the first hour's new mail can say so.
    func readAccounts() async {
        guard let core else { return }
        if let listed = try? await core.get(API.Get.Accounts.self) {
            accounts = listed.accounts
            signatureDefault = listed.signatureDefault
            accountsRead = true
            if !accounts.isEmpty { newMail.askOnce() }
        }
        await readHealth()
    }

    func readHealth() async {
        guard let core else { return }
        if let read = try? await core.get(API.Get.AccountHealth.self) { health = read.accounts }
    }

    /// The background jobs whose last round failed. `/api/debug/health` says it, and asking it never counts as someone
    /// being here, so the background's rounds go on as they would.
    func readJobs() async {
        guard let core else { return }
        if let glance = try? await core.get(API.Get.DebugHealth.self) { failingJobs = StatusWords.failing(glance.jobs) }
    }

    func readPreferences() async {
        guard let core else { return }
        if let read = try? await core.get(API.Get.Preferences.self) { preferences = read.preferences }
    }

    func readUpcoming() async {
        guard let core else { return }
        if let read = try? await core.get(API.Get.Upcoming.self) { upcoming = read.items }
    }

    /// The digest again, a moment after a burst of events, as the page reads it once for many. Events that come while
    /// a read waits join it rather than put it off, as the mailbox's do (`Mailbox.digestAgain`): the older-mail lane
    /// says folder-synced at each step for minutes after a first sync, and a wait begun afresh at each would leave the
    /// pill and the brief's line as they were until the steps stopped.
    private func digestAgain() {
        guard digestSoon == nil else { return }
        digestSoon = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(400))
            guard !Task.isCancelled else { return }
            self?.digestSoon = nil
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

    /// Sync every account now, and say so until each account's sync has ended, whether or not it failed (`syncNow`).
    /// The end is read off the digest's last round, which a sync sets as it ends however it went, and off each
    /// account's record of its syncs: a failure that may pass (offline, a timeout) records nothing of its own.
    func syncNow() async {
        guard let core, !syncing else { return }
        syncing = true
        say(String(localized: "Syncing…"))
        await readHealth()
        await readDigest()
        let before = health, round = digest?.sync.last
        _ = try? await core.post(API.Post.Refresh.self, API.NoBody())
        var ended = false
        for _ in 0..<40 {
            try? await Task.sleep(for: .seconds(3))
            await readHealth()
            await readDigest()
            ended = Self.syncEnded(before: before, after: health, accounts: addresses,
                                   running: digest?.sync.running ?? false, round: round, now: digest?.sync.last)
            if ended { break }
        }
        syncing = false
        say(Self.synced(before: before, after: health, accounts: addresses, ended: ended, name: name))
    }

    /// Whether a sync asked for has ended: nothing says one is running, and either a round has ended since it was
    /// asked for (`round` was the last one before, `now` the last one now) or every account has a sync recorded since.
    static func syncEnded(before: [API.AccountStanding], after: [API.AccountStanding], accounts: [String],
                          running: Bool, round: String? = nil, now: String? = nil) -> Bool
    {
        guard !running else { return false }
        if let now, now != round { return true }
        return accounts.allSatisfy { mark(before, $0) != mark(after, $0) }
    }

    /// What a person is told as a sync they asked for stops: done; which accounts couldn't sync, and either that they
    /// are tried again on their own or, when one of them waits for the person, where to see what to do; or that it
    /// goes on in the background.
    static func synced(before: [API.AccountStanding], after: [API.AccountStanding], accounts: [String], ended: Bool,
                       name: (String) -> String) -> String
    {
        guard ended else { return String(localized: "Still syncing. New mail shows as it comes.") }
        let failed = accounts.filter { !wentWell(before, after, $0) }
        guard let first = failed.first else { return String(localized: "Sync complete") }
        if failed.contains(where: { waitsForYou(after, $0) }) {
            return failed.count == 1
                ? String(localized: "Couldn’t sync \(name(first)). Open Settings › Accounts to see what to do.")
                : String(localized: "Couldn’t sync \(failed.count) accounts. Open Settings › Accounts to see what to do.")
        }
        return failed.count == 1
            ? String(localized: "Couldn’t sync \(name(first)). MailMind tries again on its own.")
            : String(localized: "Couldn’t sync \(failed.count) accounts. MailMind tries again on its own.")
    }

    /// Whether an account's record holds an error that waits for the person: a password turned down (the login hold
    /// keeps the same one from trying again for a while), a certificate to trust, a server not reached, or one that
    /// said no. Settings › Accounts says what to do about each (`StatusWords.syncProblem`). Trouble that may pass
    /// (nothing recorded, a timeout) and a mailbox read again from the start need nothing.
    private static func waitsForYou(_ after: [API.AccountStanding], _ account: String) -> Bool {
        guard let record = after.first(where: { $0.account == account }), record.lastErrorTs != nil else { return false }
        return !passesOnItsOwn.contains(record.lastErrorKind ?? "")
    }

    /// The sync errors MailMind gets past on its own, as `StatusWords.syncProblem` says of them.
    private static let passesOnItsOwn: Set<String> = ["TIMEOUT", "UIDVALIDITY"]

    /// An account's record of its syncs, as far as telling a new one goes.
    private static func mark(_ records: [API.AccountStanding], _ account: String) -> [String?] {
        let record = records.first { $0.account == account }
        return [record?.lastOkTs, record?.lastErrorTs, record.map { String($0.syncCount) }]
    }

    /// Whether an account's record says a sync went well since `before`: one more sync, and no error standing. A sync
    /// that failed with trouble that may pass records nothing, and one that failed for good records its error.
    private static func wentWell(_ before: [API.AccountStanding], _ after: [API.AccountStanding],
                                 _ account: String) -> Bool
    {
        guard let now = after.first(where: { $0.account == account }), now.lastErrorTs == nil else { return false }
        let was = before.first { $0.account == account }
        return now.syncCount != was?.syncCount || now.lastOkTs != was?.lastOkTs
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

    /// An account's type as Settings lists it (gmail, proton, imap), or nil before the accounts are read.
    func type(_ account: String) -> String? {
        accounts.first { $0.email == account }?.type
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

    /// What an event touches, read again. The stream's own listener calls it; so does a test, with events of its own.
    /// An account's server that didn't answer (`sync-trouble`: a sync that failed, or a connection outside one) changes
    /// how its sync stands, which nothing else says: the pill reads it, trying again or can't connect, within moments.
    /// Summaries landing (`summarized`) change how many wait for one and what today cost, which Settings' AI says.
    func heard(_ event: CoreEvent) async {
        switch event.name {
        case "sync-done", "folder-synced", "sync-trouble":
            digestAgain()
            await readHealth()
        case "new-mail":
            digestAgain()
            if let said = event.decoded(NewMailAlerts.Arrived.self) { newMail.arrived(said) }
        case "snooze-wake", "mute", "reconcile", "flags-changed", "followup", "undo-done", "commands-changed", "resync",
             "summarized":
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
