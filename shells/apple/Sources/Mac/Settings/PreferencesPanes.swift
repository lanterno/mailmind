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
            ProgressView().controlSize(.small).frame(maxWidth: .infinity, minHeight: 200)
        } else {
            content()
        }
    }
}

/// How MailMind looks, sending, the keyboard, and the backup and export of the mail kept here.
struct GeneralPane: View {
    let status: AppStatus

    var body: some View {
        SettingsPage(tab: .general) {
            PreferencesRead(status: status) {
                DeskSideCard(title: String(localized: "Appearance"), icon: .sun) {
                    SettingLine(String(localized: "Light"), help: String(localized: """
                        The sky and the paper MailMind is drawn on. The top bar’s button goes round them too.
                        """)) {
                        MeadowSegmentedChoice(label: String(localized: "Light"), selection: light,
                                              options: MeadowLight.allCases.map { ($0, $0.name) }, fills: false)
                    }
                    SettingLine(String(localized: "Rows")) {
                        MeadowSegmentedChoice(label: String(localized: "Rows"), selection: density, options: [
                            (API.Density.comfortable, String(localized: "Comfortable")),
                            (.cozy, String(localized: "Cozy")), (.compact, String(localized: "Compact")),
                        ], fills: false)
                    }
                    SettingSwitch(String(localized: "High contrast"),
                                  help: String(localized: "Darker ink and firmer lines, in every light."),
                                  isOn: status.preference(\.highContrast) { .init(highContrast: $0) })
                }
                DeskSideCard(title: String(localized: "Sending"), icon: .send) {
                    SettingLine(String(localized: "Undo Send window, in seconds"), help: String(localized: """
                        How long a message waits before it goes, so you can take it back.
                        """)) {
                        HStack(spacing: 4) {
                            TextField("Undo Send window, in seconds", value: undoSend, format: .number)
                                .labelsHidden()
                                .multilineTextAlignment(.trailing)
                                .meadowComposeField()
                                .frame(width: 64)
                            Stepper("Undo Send window, in seconds", value: undoSend, step: 1)
                                .labelsHidden()
                        }
                    }
                }
                DeskSideCard(title: String(localized: "Keyboard"), icon: .keyboard) {
                    SettingSwitch(String(localized: "Single-key shortcuts"), help: String(localized: """
                        Keys like e, r and j act on the mail without ⌘. Turn them off to use only ⌘ shortcuts.
                        """), isOn: status.preference(\.singleKeys) { .init(singleKeys: $0) })
                }
                BackupSection(status: status)
            }
        }
    }

    /// The light the window is in, kept as the `theme` preference the top bar's button keeps.
    private var light: Binding<MeadowLight> {
        Binding(get: { status.light }, set: { status.setPreferences(API.ChangedPreferences(theme: $0.theme)) })
    }

    private var density: Binding<API.Density> {
        Preference(status: status, read: \.density, change: { .init(density: $0) }, fallback: .comfortable).binding
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
    @Environment(\.meadow) private var meadow

    var body: some View {
        DeskSideCard(title: String(localized: "Backup & Export"), icon: .archive) {
            Text(plan.map { "\($0.total.formatted()) messages · \($0.bodies.formatted()) with full bodies · \($0.groups.count) folders" }
                 ?? String(localized: "Counting…"))
                .font(.meadow(.ui, 13))
                .foregroundStyle(meadow.ink2)
            SettingLine(String(localized: "Backup")) {
                Button("Back Up…") { Task { await save(backup: true) } }
                    .meadowButton(small: true)
                    .disabled(working)
                    .help("A full local snapshot of your synced mail, encrypted as the database is: it opens with the key in this Mac’s Keychain")
            }
            SettingLine(String(localized: "Export")) {
                HStack(spacing: 8) {
                    MeadowSegmentedChoice(label: String(localized: "Export format"), selection: $format, options: [
                        (API.ArchiveFormat.eml, String(localized: ".eml files")), (.mbox, String(localized: "mbox")),
                    ], fills: false)
                    Button("Export…") { Task { await save(backup: false) } }
                        .meadowButton(small: true)
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
    @Environment(\.meadow) private var meadow

    init(status: AppStatus, allowed: Bool?? = nil) {
        self.status = status
        _allowed = State(initialValue: allowed)
    }

    var body: some View {
        SettingsPage(tab: .notifications) {
            PreferencesRead(status: status) {
                DeskSideCard(title: String(localized: "Notifications"), icon: .bell) {
                    SettingSwitch(String(localized: "New mail"),
                                  isOn: status.preference(\.notifyNewMail) { .init(notifyNewMail: $0) })
                    SettingSwitch(String(localized: "A meeting about to start"),
                                  isOn: status.preference(\.notifyMeetings) { .init(notifyMeetings: $0) })
                    SettingSwitch(String(localized: "Quiet hours"),
                                  isOn: status.preference(\.quietHours) { .init(quietHours: $0) })
                    if status.preferences?.quietHours == true {
                        SettingLine(String(localized: "Quiet from")) {
                            DatePicker("Quiet from", selection: time(\.quietFrom) { .init(quietFrom: $0) },
                                       displayedComponents: .hourAndMinute)
                                .labelsHidden()
                        }
                        SettingLine(String(localized: "Quiet until")) {
                            DatePicker("Quiet until", selection: time(\.quietUntil) { .init(quietUntil: $0) },
                                       displayedComponents: .hourAndMinute)
                                .labelsHidden()
                        }
                    }
                    SettingHelp(String(localized: """
                        A notification goes only while MailMind is in the background, and the Dock icon counts the new \
                        mail that came in. In quiet hours, by this Mac’s clock, none goes.
                        """))
                }
                DeskSideCard(title: String(localized: "This Mac"), icon: .info) {
                    permission
                }
            }
        }
        .task { if status.core != nil { allowed = .some(await status.newMail.allowed()) } }
    }

    @ViewBuilder private var permission: some View {
        switch allowed {
        case nil:
            ProgressView().controlSize(.small)
        case .some(nil):
            SettingLine(String(localized: "MailMind hasn’t asked to notify you yet.")) {
                Button("Turn On Notifications") {
                    Task {
                        let yes = await status.newMail.ask()
                        allowed = .some(yes)
                        status.say(yes ? String(localized: "Notifications on") : String(localized: "Notifications blocked"))
                    }
                }
                .meadowButton(.primary, small: true)
            }
        case .some(true?):
            HStack(spacing: 8) {
                MeadowIconView(.checkCircle, size: 15).foregroundStyle(meadow.moss)
                Text("This Mac lets MailMind notify you.")
            }
            .font(.meadow(.ui, 13.5, .semibold))
            .foregroundStyle(meadow.ink2)
        case .some(false?):
            SettingLine(String(localized: "Notifications are off for MailMind in System Settings.")) {
                Button("Open System Settings") {
                    if let url = URL(string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension") {
                        NSWorkspace.shared.open(url)
                    }
                }
                .meadowButton(small: true)
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
#endif
