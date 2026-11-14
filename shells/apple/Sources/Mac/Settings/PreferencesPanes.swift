#if os(macOS)
import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// A preference as a binding: it reads what the server holds and saves a change on its own, as the page's controls do
/// (lib/prefs.ts's `setPrefs`). A value the server refuses comes back as it holds it, with its words.
@MainActor
struct Preference<Value> {
    let status: AppStatus
    let read: (API.Preferences) -> Value
    let change: (Value) -> API.ChangedPreferences
    let fallback: Value

    var binding: Binding<Value> {
        Binding(
            get: { status.preferences.map(read) ?? fallback },
            set: { status.setPreferences(change($0)) })
    }
}

extension AppStatus {
    func preference(_ key: KeyPath<API.Preferences, Bool>,
                    _ change: @escaping (Bool) -> API.ChangedPreferences) -> Binding<Bool>
    {
        Preference(status: self, read: { $0[keyPath: key] }, change: change, fallback: false).binding
    }
}

/// What has nothing to show until the server has said what it holds.
struct PreferencesRead<Content: View>: View {
    let status: AppStatus
    @ViewBuilder let content: () -> Content

    var body: some View {
        if status.preferences == nil {
            ProgressView().frame(maxWidth: .infinity, minHeight: 200)
        } else {
            content()
        }
    }
}

/// Sending, the keyboard, and the backup and export of the mail kept here.
struct GeneralPane: View {
    let status: AppStatus

    var body: some View {
        PreferencesRead(status: status) {
            Form {
                Section("Sending") {
                    LabeledContent("Undo Send window, in seconds") {
                        HStack(spacing: 4) {
                            TextField("Undo Send window, in seconds", value: undoSend, format: .number)
                                .labelsHidden()
                                .multilineTextAlignment(.trailing)
                                .frame(width: 56)
                            Stepper("Undo Send window, in seconds", value: undoSend, step: 1)
                                .labelsHidden()
                        }
                    }
                    SettingHelp(String(localized: "How long a message waits before it goes, so you can take it back."))
                }
                Section("Keyboard") {
                    Toggle("Single-key shortcuts", isOn: status.preference(\.singleKeys) { .init(singleKeys: $0) })
                    SettingHelp(String(localized: "Keys like e, r and j act on the mail without ⌘. Turn them off to use only ⌘ shortcuts."))
                }
                BackupSection(status: status)
            }
            .formStyle(.grouped)
        }
        .frame(width: 560, height: 540)
    }

    /// No bounds of its own: the server holds the window to its limits and says them when it refuses one.
    private var undoSend: Binding<Int> {
        Preference(status: status, read: \.undoSendSeconds, change: { .init(undoSendSeconds: $0) }, fallback: 10).binding
    }
}

/// Backup & Export: the database as a snapshot, or the mail as a zip any mail program reads (the page's `Backup`).
struct BackupSection: View {
    let status: AppStatus
    @State private var plan: API.ExportPlan?
    @State private var format = API.ArchiveFormat.eml
    @State private var working = false

    var body: some View {
        Section("Backup & Export") {
            Text(plan.map { "\($0.total.formatted()) messages · \($0.bodies.formatted()) with full bodies · \($0.groups.count) folders" }
                 ?? String(localized: "Counting…"))
                .foregroundStyle(.secondary)
            LabeledContent("Backup") {
                Button("Back Up…") { Task { await save(backup: true) } }
                    .disabled(working)
                    .help("A full local snapshot of your synced mail, encrypted as the database is: it opens with the key in this Mac’s Keychain")
            }
            LabeledContent("Export") {
                HStack {
                    Picker("Export format", selection: $format) {
                        Text(".eml files").tag(API.ArchiveFormat.eml)
                        Text("mbox").tag(API.ArchiveFormat.mbox)
                    }
                    .labelsHidden()
                    .pickerStyle(.segmented)
                    .fixedSize()
                    Button("Export…") { Task { await save(backup: false) } }
                        .disabled(working)
                        .help("Your synced mail as a zip any mail program can read")
                }
            }
            SettingHelp(String(localized: """
                The backup is MailMind’s own format. The zip is your mail for any other mail program: one .eml file a \
                message, or one mbox a folder. A message with no full body stored comes as its headers. All local · \
                read-only · never opens IMAP.
                """))
        }
        .task { plan = try? await status.core?.get(API.Get.ExportPlan.self) }
    }

    /// Ask where, then write what the route answers there.
    private func save(backup: Bool) async {
        guard let core = status.core else { return }
        let panel = NSSavePanel()
        panel.canCreateDirectories = true
        panel.allowedContentTypes = backup ? [UTType(filenameExtension: "db") ?? .data] : [.zip]
        panel.nameFieldStringValue = backup ? "mailmind-backup.db" : "mailmind-export.zip"
        guard await panel.begin() == .OK, let url = panel.url else { return }
        working = true
        defer { working = false }
        status.say(String(localized: "Writing \(url.lastPathComponent)…"))
        let answer = backup
            ? await core.send("GET", API.Get.Export.path, Data())
            : await core.send("GET", API.Get.ExportArchive.path + Core.query(["format": format.rawValue]), Data())
        guard (200..<300).contains(answer.status) else {
            status.say(Problem(answer).description)
            return
        }
        do {
            try answer.body.write(to: url, options: .atomic)
            status.say(String(localized: "Saved \(url.lastPathComponent)"))
        } catch {
            status.say(String(localized: "Couldn’t save \(url.lastPathComponent)"))
        }
    }
}

/// New mail, a meeting about to start, and quiet hours; and whether this Mac lets MailMind notify at all.
struct NotificationsPane: View {
    let status: AppStatus
    @State private var allowed: Bool??

    init(status: AppStatus, allowed: Bool?? = nil) {
        self.status = status
        _allowed = State(initialValue: allowed)
    }

    var body: some View {
        PreferencesRead(status: status) {
            Form {
                Section {
                    Toggle("New mail", isOn: status.preference(\.notifyNewMail) { .init(notifyNewMail: $0) })
                    Toggle("A meeting about to start", isOn: status.preference(\.notifyMeetings) { .init(notifyMeetings: $0) })
                    Toggle("Quiet hours", isOn: status.preference(\.quietHours) { .init(quietHours: $0) })
                    if status.preferences?.quietHours == true {
                        DatePicker("Quiet from", selection: time(\.quietFrom) { .init(quietFrom: $0) },
                                   displayedComponents: .hourAndMinute)
                        DatePicker("Quiet until", selection: time(\.quietUntil) { .init(quietUntil: $0) },
                                   displayedComponents: .hourAndMinute)
                    }
                } footer: {
                    SettingHelp(String(localized: """
                        A notification goes only while MailMind is in the background, and the Dock icon counts the new \
                        mail that came in. In quiet hours, by this Mac’s clock, none goes.
                        """))
                }
                Section("This Mac") {
                    permission
                }
            }
            .formStyle(.grouped)
        }
        .frame(width: 560, height: 400)
        .task { if status.core != nil { allowed = .some(await status.newMail.allowed()) } }
    }

    @ViewBuilder private var permission: some View {
        switch allowed {
        case nil:
            ProgressView().controlSize(.small)
        case .some(nil):
            HStack {
                Text("MailMind hasn’t asked to notify you yet.")
                Spacer()
                Button("Turn On Notifications") {
                    Task {
                        let yes = await status.newMail.ask()
                        allowed = .some(yes)
                        status.say(yes ? String(localized: "Notifications on") : String(localized: "Notifications blocked"))
                    }
                }
            }
        case .some(true?):
            Label("This Mac lets MailMind notify you.", systemImage: "checkmark.circle")
        case .some(false?):
            HStack {
                Label("Notifications are off for MailMind in System Settings.", systemImage: "bell.slash")
                Spacer()
                Button("Open System Settings") {
                    if let url = URL(string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension") {
                        NSWorkspace.shared.open(url)
                    }
                }
            }
        }
    }

    /// A time the server keeps as "HH:MM", as a time of today for the picker.
    private func time(_ key: KeyPath<API.Preferences, String>,
                      _ change: @escaping (String) -> API.ChangedPreferences) -> Binding<Date>
    {
        Binding(
            get: { Clock.date(status.preferences?[keyPath: key] ?? "00:00") },
            set: { status.setPreferences(change(Clock.text($0))) })
    }

    /// "HH:MM" and a time of today, by this Mac's clock.
    enum Clock {
        static func date(_ text: String, calendar: Calendar = .current, today: Date = Date()) -> Date {
            let minutes = QuietHours.minutes(text) ?? 0
            return calendar.date(bySettingHour: minutes / 60, minute: minutes % 60, second: 0, of: today) ?? today
        }

        static func text(_ date: Date, calendar: Calendar = .current) -> String {
            let parts = calendar.dateComponents([.hour, .minute], from: date)
            return String(format: "%02d:%02d", parts.hour ?? 0, parts.minute ?? 0)
        }
    }
}

/// The Claude API key, what AI may spend in a day, and what it writes ahead.
struct AIPane: View {
    let status: AppStatus
    @State private var keySet: Bool?

    init(status: AppStatus, keySet: Bool? = nil) {
        self.status = status
        _keySet = State(initialValue: keySet)
    }

    var body: some View {
        PreferencesRead(status: status) {
            Form {
                Section("Claude API key") {
                    ClaudeKeyView(status: status, keySet: $keySet)
                }
                Section {
                    LabeledContent("Daily limit, in US dollars") {
                        TextField("Daily limit, in US dollars", value: limit, format: .number.precision(.fractionLength(0...2)))
                            .labelsHidden()
                            .multilineTextAlignment(.trailing)
                            .frame(width: 80)
                    }
                    if let ai = status.digest?.ai, ai.engine != "none", let spent = ai.spentTodayMicros {
                        LabeledContent("Today") {
                            Text("\(StatusWords.dollars(spent)) of \(StatusWords.dollars(ai.limitMicros))")
                        }
                    }
                } footer: {
                    SettingHelp(String(localized: "Once a day’s AI has cost this much, background work stops and MailMind asks before spending more."))
                }
                Section {
                    Toggle("Have today’s brief ready each morning", isOn: status.preference(\.morningBrief) { .init(morningBrief: $0) })
                    SettingHelp(String(localized: "Written before you open your Inbox, within the daily limit, and only if you’ve used MailMind in the last two days."))
                    Toggle("Have last week’s review ready each Monday", isOn: status.preference(\.weeklyReview) { .init(weeklyReview: $0) })
                    SettingHelp(String(localized: "Written on Monday morning, within the daily limit, and only if you’ve used MailMind in the last two days. Your week shows it until the next Monday, and ↻ there writes a new one."))
                    Toggle("File low-priority mail by itself, once a day", isOn: status.preference(\.autoTidy) { .init(autoTidy: $0) })
                    SettingHelp(String(localized: "Only what today’s brief offers to tidy. The brief says what went, and one click takes it all back."))
                } footer: {
                    SettingHelp(StatusWords.aiPrivacy)
                }
            }
            .formStyle(.grouped)
        }
        .frame(width: 560, height: 560)
    }

    /// No bounds of its own: the server holds the limit to more than nothing and its most, and says them when it
    /// refuses one.
    private var limit: Binding<Double> {
        Preference(status: status, read: \.aiDailyLimitUsd, change: { .init(aiDailyLimitUsd: $0) }, fallback: 0).binding
    }
}

/// The Claude API key: how it stands and where it comes from, a field to keep one, and a way to take it out.
struct ClaudeKeyView: View {
    let status: AppStatus
    @Binding var keySet: Bool?

    var body: some View {
        KeptSecretView(
            label: String(localized: "Claude API key"),
            prompt: String(localized: "paste a key from console.anthropic.com"),
            standing: keySet.map { SecretStanding.apiKey(set: $0, ai: status.digest?.ai, sources: .ofThisProcess) },
            keep: keep, remove: clear,
            removeWarning: String(localized: "AI is off unless the environment gives a key."))
            .task { await read() }
    }

    private func read() async {
        guard let core = status.core else { return }
        keySet = try? await core.get(API.Get.AiKey.self).set
    }

    private func keep(_ typed: String) async -> String? {
        guard let core = status.core else { return nil }
        do {
            keySet = try await SecretPost.post(core, API.Post.AiKey.self, API.KeyBody(key: typed)).set
            await status.readDigest()
            status.say(String(localized: "Claude API key kept in your Keychain"))
            return nil
        } catch {
            return (error as? Problem)?.description ?? String(localized: "Couldn’t keep it")
        }
    }

    private func clear() async -> String? {
        guard let core = status.core else { return nil }
        do {
            keySet = try await core.post(API.Post.AiKeyClear.self, API.NoBody()).set
            await status.readDigest()
            return nil
        } catch {
            return (error as? Problem)?.description ?? String(localized: "Couldn’t remove it")
        }
    }
}
#endif
