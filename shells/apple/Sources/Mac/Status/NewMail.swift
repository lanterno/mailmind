#if os(macOS)
import AppKit
import Foundation
import SwiftUI
@preconcurrency import UserNotifications

/// New mail and a meeting about to start, told as the page tells them (assistant/NewMail.tsx, status/Status.tsx): with
/// someone at the app, VoiceOver hears of it; with the app in the background, the Dock's badge counts what came in, and
/// a notification goes when the preferences allow one and quiet hours don't cover the moment. The count goes when the
/// app is come back to, as the page's tab title clears.
@MainActor
final class NewMailAlerts {
    /// What a `new-mail` event says: an account's Inbox kept `count` new messages, and when it was one, its UID.
    struct Arrived: Decodable, Equatable, Sendable {
        var account: String?
        var folder: String?
        var count: Int?
        var uid: Int?
    }

    /// What came in while nobody was at the app.
    private(set) var cameIn = 0
    /// What the preferences say now, an account's name, the minute it is, and whether there is an account to hear
    /// from: `AppStatus`'s, once it has the core.
    var preferences: @MainActor () -> API.Preferences? = { nil }
    var name: @MainActor (String) -> String = StatusWords.short
    var now: @MainActor () -> Date = Date.init
    var hasAccounts: @MainActor () -> Bool = { false }
    /// The row of the one message that came, by its account, folder and UID, for its sender and subject: the
    /// status reads its folder for it once it has the core.
    var lookUp: @MainActor (_ account: String, _ folder: String, _ uid: Int) async -> API.FolderRow? = { _, _, _ in nil }

    private let notifier: any Notifier
    private let isActive: @MainActor () -> Bool
    private let badge: @MainActor (String?) -> Void
    private let announce: @MainActor (String) -> Void
    private var asked = false
    private var told: Set<String> = []
    private var comingBack: NSObjectProtocol?

    init(notifier: any Notifier = SystemNotifier.forThisProcess(),
         isActive: @escaping @MainActor () -> Bool = { NSApp?.isActive ?? true },
         badge: @escaping @MainActor (String?) -> Void = { NSApp?.dockTile.badgeLabel = $0 },
         announce: @escaping @MainActor (String) -> Void = Announce.say)
    {
        self.notifier = notifier
        self.isActive = isActive
        self.badge = badge
        self.announce = announce
        comingBack = NotificationCenter.default.addObserver(
            forName: NSApplication.didBecomeActiveNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.cameBack() }
        }
    }

    /// The person is back: what came in has been seen (`cameBack`).
    func cameBack() {
        cameIn = 0
        badge(nil)
    }

    /// An account's Inbox kept new mail (`tell`).
    func arrived(_ said: Arrived) {
        let count = max(1, said.count ?? 1)
        let account = said.account ?? ""
        let name = account.isEmpty ? "" : self.name(account)
        let words = account.isEmpty
            ? String(localized: "\(Self.messages(count)) arrived")
            : String(localized: "\(Self.messages(count)) arrived in \(name)")
        if isActive() {
            announce(words)
            return
        }
        cameIn += count
        badge(String(cameIn))
        guard mayNotify(\.notifyNewMail) else { return }
        let notifier = notifier, lookUp = lookUp
        let one = count == 1 && !account.isEmpty ? said.uid : nil
        Task {
            let row: API.FolderRow? = if let one { await lookUp(account, said.folder ?? "INBOX", one) } else { nil }
            let told = Self.told(count: count, name: name, row: row)
            await notifier.post(id: "mailmind-\(account)", title: told.title, body: told.body,
                                opens: row.map(MailCopy.init))
        }
    }

    /// What a notification of new mail says: who sent the one message and its subject, as a Mac's mail app says
    /// them; or, for several or one not found, the account and how many.
    static func told(count: Int, name: String, row: API.FolderRow?) -> (title: String, body: String) {
        if count == 1, let row {
            let sender = [row.fromName, row.fromAddr].compactMap { $0?.trimmingCharacters(in: .whitespaces) }
                .first { !$0.isEmpty } ?? String(localized: "New mail")
            let subject = row.subject.trimmingCharacters(in: .whitespaces)
            return (sender, subject.isEmpty ? String(localized: "(no subject)") : subject)
        }
        return (String(localized: "New mail · \(name)"), messages(count))
    }

    /// "1 new message", "3 new messages".
    static func messages(_ count: Int) -> String {
        count == 1 ? String(localized: "1 new message") : String(localized: "\(count) new messages")
    }

    /// The meeting about to start, once for each: a notification while nobody is at the app, else VoiceOver hears it
    /// (`SoonPill`'s `tell`).
    func meetingSoon(_ meeting: Soon.Meeting?) {
        guard let meeting, !told.contains(meeting.id) else { return }
        told.insert(meeting.id)
        let words = meeting.message(now: now())
        guard !isActive(), mayNotify(\.notifyMeetings) else {
            announce(words)
            return
        }
        let notifier = notifier
        Task {
            await notifier.post(id: "mailmind-soon-\(meeting.id)", title: words, body: meeting.detail,
                                opens: meeting.invite)
        }
    }

    /// Whether a notification of this kind may go now: the preference is on and quiet hours don't cover now.
    private func mayNotify(_ kind: KeyPath<API.Preferences, Bool>) -> Bool {
        guard let preferences = preferences() else { return false }
        return preferences[keyPath: kind] && !QuietHours.cover(now(), preferences)
    }

    /// Ask the system once whether MailMind may notify, when the preferences want notifications and there is an
    /// account to hear from. The system remembers the answer; Settings shows it and leads to where it changes.
    func askOnce() {
        guard !asked, let preferences = preferences(), preferences.notifyNewMail || preferences.notifyMeetings,
              hasAccounts() else { return }
        asked = true
        let notifier = notifier
        Task { _ = await notifier.ask() }
    }

    /// What the system says now: allowed, not allowed, or not asked yet.
    func allowed() async -> Bool? {
        await notifier.allowed()
    }

    /// Ask now, as a person asked to.
    func ask() async -> Bool {
        asked = true
        return await notifier.ask()
    }
}

/// Where a notification goes: the system's notification center, or a test's stand-in.
protocol Notifier: Sendable {
    /// Whether notifications are allowed: nil when nobody has been asked.
    func allowed() async -> Bool?
    /// Ask, once: the system shows its question the first time only and answers from then on.
    func ask() async -> Bool
    func post(id: String, title: String, body: String) async
    /// The same, and the message a click on it opens.
    func post(id: String, title: String, body: String, opens: MailCopy?) async
}

extension Notifier {
    /// A notifier that opens nothing posts as it always has.
    func post(id: String, title: String, body: String, opens: MailCopy?) async {
        await post(id: id, title: title, body: body)
    }
}

/// The system's notification center, in a person's run only. A run that isn't a person's (`CoreHome.isAPersonsRun`:
/// a demo, a test, a measure run) gets one that never asks and never posts: it has no person to answer, every build
/// is `io.github.lanterno.mailmind`, so it would ask and post as the person's own MailMind, and a test bundle has no
/// app to post as.
struct SystemNotifier: Notifier {
    static func forThisProcess(_ environment: [String: String] = ProcessInfo.processInfo.environment) -> any Notifier {
        if !CoreHome.isAPersonsRun(environment) || Bundle.main.bundleIdentifier == nil {
            return Silent()
        }
        return SystemNotifier()
    }

    func allowed() async -> Bool? {
        switch await UNUserNotificationCenter.current().notificationSettings().authorizationStatus {
        case .notDetermined: nil
        case .authorized, .provisional, .ephemeral: true
        default: false
        }
    }

    func ask() async -> Bool {
        (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])) ?? false
    }

    func post(id: String, title: String, body: String) async {
        await post(id: id, title: title, body: body, opens: nil)
    }

    func post(id: String, title: String, body: String, opens: MailCopy?) async {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        // What a click opens (`NotificationOpener`): the message's name, never its words.
        if let opens { content.userInfo = NotificationOpener.userInfo(opens) }
        // The same id replaces the one before, as the page's tag does: one notification an account.
        try? await UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: id, content: content,
                                                                                trigger: nil))
    }

    /// Never asks, never posts: it only keeps the latest it would have posted, in memory, for a test to read.
    final class Silent: Notifier, @unchecked Sendable {
        struct Post: Equatable, Sendable {
            let id: String
            let title: String
            let body: String
            let opens: MailCopy?
        }

        /// How many it keeps: a demo left running a day keeps no more.
        static let keeps = 20
        private let lock = NSLock()
        private var kept: [Post] = []

        /// What it would have posted, oldest first.
        var wouldHavePosted: [Post] { lock.withLock { kept } }

        func allowed() async -> Bool? { false }
        func ask() async -> Bool { false }

        func post(id: String, title: String, body: String) async {
            await post(id: id, title: title, body: body, opens: nil)
        }

        func post(id: String, title: String, body: String, opens: MailCopy?) async {
            lock.withLock {
                kept.append(Post(id: id, title: title, body: body, opens: opens))
                kept.removeFirst(max(0, kept.count - Self.keeps))
            }
        }
    }
}

/// Whether quiet hours cover a moment, by this computer's clock (`quietNow`): a span that crosses midnight is that
/// evening and the morning after, and one that starts where it ends covers nothing.
enum QuietHours {
    static func cover(_ at: Date, _ preferences: API.Preferences, calendar: Calendar = .current) -> Bool {
        cover(at, on: preferences.quietHours, from: preferences.quietFrom, until: preferences.quietUntil,
              calendar: calendar)
    }

    static func cover(_ at: Date, on: Bool, from: String, until: String, calendar: Calendar = .current) -> Bool {
        guard on, from != until, let start = minutes(from), let end = minutes(until) else { return false }
        let parts = calendar.dateComponents([.hour, .minute], from: at)
        let now = (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
        return start < end ? now >= start && now < end : now >= start || now < end
    }

    /// "HH:MM" as minutes past midnight.
    static func minutes(_ time: String) -> Int? {
        let parts = time.split(separator: ":")
        guard parts.count == 2, let hours = Int(parts[0]), let minutes = Int(parts[1]) else { return nil }
        return hours * 60 + minutes
    }
}

/// The meeting about to start (`SoonPill`): the first that isn't all day, starts within ten minutes or began less than
/// five ago, and whose reminder nobody closed.
enum Soon {
    static let ahead: TimeInterval = 10 * 60
    static let after: TimeInterval = 5 * 60

    struct Meeting: Equatable, Identifiable, Sendable {
        /// The copy and its start, so a series reminds once for each meeting.
        let id: String
        let title: String
        let start: Date
        let end: Date?
        let location: String
        let organizer: String
        /// The message the invite came in, which a click on the reminder opens.
        var invite: MailCopy?

        /// How long until it starts, in the page's words: "in 4 min", or "now".
        func when(now: Date) -> String {
            let minutes = Int((start.timeIntervalSince(now) / 60).rounded(.up))
            return minutes > 0 ? String(localized: "in \(minutes) min") : String(localized: "now")
        }

        func message(now: Date) -> String {
            String(localized: "\(title) starts \(when(now: now))")
        }

        /// When it is and where, for a notification's body.
        var detail: String {
            let time = end.map { (start..<max($0, start)).formatted(date: .omitted, time: .shortened) }
                ?? start.formatted(date: .omitted, time: .shortened)
            return [time, location].filter { !$0.isEmpty }.joined(separator: " · ")
        }
    }

    /// The core writes a meeting's start and end on the person's wall clock (`EventWhen`), read in `calendar`'s zone.
    static func next(_ upcoming: [API.UpcomingEvent], now: Date, closed: Set<String>,
                     calendar: Calendar = .current) -> Meeting? {
        for event in upcoming where !event.allDay {
            guard let start = EventWhen.moment(event.start, calendar: calendar) else { continue }
            let id = "\(event.key)|\(event.folder)|\(event.uid)|\(event.start)"
            let left = start.timeIntervalSince(now)
            guard !closed.contains(id), left <= ahead, left > -after else { continue }
            let title = event.summary.isEmpty ? String(localized: "(no title)") : event.summary
            return Meeting(id: id, title: title, start: start, end: EventWhen.moment(event.end, calendar: calendar),
                           location: event.location, organizer: event.organizer,
                           invite: MailCopy(key: event.key, folder: event.folder, uid: event.uid, account: event.account))
        }
        return nil
    }

    /// How long until a reminder's words next change, a minute at most: a meeting's "in N min" falls a minute at each
    /// whole minute before its start (where one ten minutes off also comes into reach), says "now" from its start, and
    /// goes five minutes after it.
    static func wake(_ upcoming: [API.UpcomingEvent], now: Date, calendar: Calendar = .current) -> TimeInterval {
        var wait: TimeInterval = 60
        for event in upcoming where !event.allDay {
            guard let start = EventWhen.moment(event.start, calendar: calendar) else { continue }
            let left = start.timeIntervalSince(now)
            if left > 0 {
                let step = left.truncatingRemainder(dividingBy: 60)
                wait = min(wait, step > 0 ? step : 60)
            } else if left > -after {
                wait = min(wait, left + after)
            }
        }
        // A hair past the change, so the minute read then is the new one.
        return max(wait, 0) + 0.05
    }
}

/// What VoiceOver hears of what happened, as the page's `announce` tells a screen reader.
enum Announce {
    @MainActor static func say(_ text: String) {
        guard let app = NSApp else { return }
        NSAccessibility.post(element: app, notification: .announcementRequested,
                             userInfo: [.announcement: text, .priority: NSAccessibilityPriorityLevel.medium.rawValue])
    }
}
#endif
