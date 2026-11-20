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
                        The sky and the paper MailMind is drawn on. Automatic is Night while your Mac is in Dark Mode, \
                        and else follows the time of day. The top bar’s button goes round them too.
                        """)) {
                        MeadowSegmentedChoice(label: String(localized: "Light"), selection: theme,
                                              options: API.Theme.round.map { ($0, $0.name) }, fills: false)
                    }
                    SettingLine(String(localized: "Rows")) {
                        MeadowSegmentedChoice(label: String(localized: "Rows"), selection: density, options: [
                            (API.Density.comfortable, String(localized: "Comfortable")),
                            (.cozy, String(localized: "Cozy")), (.compact, String(localized: "Compact")),
                        ], fills: false)
                    }
                    SettingSwitch(String(localized: "High contrast"),
                                  help: String(localized: """
                                      Darker ink, firmer lines and solid panels, in every light. macOS’s Increase \
                                      Contrast turns it on too.
                                      """),
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
                OpenAtLoginCard()
                ReadOnlySection(status: status)
                BackupSection(status: status)
            }
        }
    }

    /// The light chosen, Automatic or one of the three, kept as the `theme` preference the top bar's button keeps.
    private var theme: Binding<API.Theme> {
        Binding(get: { status.theme }, set: { status.setPreferences(API.ChangedPreferences(theme: $0)) })
    }

    private var density: Binding<API.Density> {
        Preference(status: status, read: \.density, change: { .init(density: $0) }, fallback: .comfortable).binding
    }

    /// No bounds of its own: the server holds the window to its limits and says them when it refuses one.
    private var undoSend: Binding<Int> {
        Preference(status: status, read: \.undoSendSeconds, change: { .init(undoSendSeconds: $0) }, fallback: 10).binding
    }
}

/// The read-only switch: MailMind changes nothing on the mail servers, kept by the app and handed to the core as it
/// opens (`CoreHome.ofThisProcess`), so a change asks to open MailMind again. Only a person's own run has it: a demo
/// or test run runs only as it was started, since opening it again wouldn't carry its home.
struct ReadOnlySection: View {
    let status: AppStatus
    @AppStorage(CoreHome.readOnlyKey) private var readOnly = false
    @State private var asking = false
    @Environment(\.meadow) private var meadow

    var body: some View {
        DeskSideCard(title: String(localized: "Mail servers"), icon: .shield) {
            if ReadOnlyRun.switchable {
                SettingSwitch(String(localized: "Read only"), help: ReadOnlyRun.help,
                              isOn: Binding(get: { readOnly }, set: { readOnly = $0; asking = true }))
                if let pending = ReadOnlyRun.pending(chosen: readOnly, running: status.digest?.profile) {
                    HStack(spacing: 8) {
                        SettingHelp(pending)
                        Spacer(minLength: 0)
                        Button("Relaunch Now") { ReadOnlyRun.relaunch() }
                            .meadowButton(small: true)
                    }
                }
            } else {
                SettingHelp(ReadOnlyRun.notSwitchable(running: status.digest?.profile))
            }
        }
        .alert(readOnly ? "Open MailMind again to turn read only on?" : "Open MailMind again to turn read only off?",
               isPresented: $asking) {
            Button("Relaunch Now") { ReadOnlyRun.relaunch() }
            Button("Later", role: .cancel) {}
        } message: {
            Text("MailMind applies this as it opens.")
        }
    }
}

/// What the read-only switch says, and the relaunch it asks for.
enum ReadOnlyRun {
    static let help = String(localized: """
        MailMind changes nothing on your mail servers: nothing is moved, deleted, marked or sent, and new mail comes \
        in only when you choose Get New Mail.
        """)

    /// What to say while the switch and the run disagree: the switch changed, and MailMind hasn't opened since.
    /// `running` is the profile the core says it runs (`digest.profile`); nil before it has said.
    static func pending(chosen: Bool, running: String?) -> String? {
        guard let running, chosen != (running == "read-only") else { return nil }
        return chosen ? String(localized: "Read only starts the next time MailMind opens.")
                      : String(localized: "MailMind changes mail on your servers again the next time it opens.")
    }

    /// Whether this run has the switch: only a person's own, which is also the only one that opens itself again (a
    /// demo's or a test's home is in an environment a relaunch wouldn't carry).
    static var switchable: Bool { CoreHome.isAPersonsRun() }

    /// What a demo or test run says in the switch's place: how it runs, which only how it was started decides.
    /// `running` is the profile the core says it runs; nil before it has said.
    static func notSwitchable(running: String?) -> String {
        let how = running == "read-only" ? String(localized: "This demo or test run is read only, as it was started.")
            : String(localized: "This demo or test run changes mail on its servers, as it was started.")
        return how + " " + String(localized: "Read only is a switch here when you open MailMind yourself.")
    }

    /// Quit, and open MailMind again once this run has ended, so two runs never hold its data at once. The quit is
    /// asked as `Quitting.quit` asks it, so it finishes from wherever Relaunch Now is chosen.
    @MainActor static func relaunch() {
        let run = ProcessInfo.processInfo.processIdentifier, app = Bundle.main.bundlePath
        Quitting.quit { quitThenOpen(quit: { NSApp.terminate(nil) }, openAgain: { openOnceEnded(run, app) }) }
    }

    /// `quit`, with `openAgain` only as the app really ends (`willTerminate`), never before: a quit called off (a
    /// question at quit answered no) leaves nothing waiting to open MailMind again when the person next quits.
    /// `terminate` comes back only from a quit called off, since one that goes on ends the process inside it.
    @MainActor static func quitThenOpen(center: NotificationCenter = .default, quit: () -> Void,
                                        openAgain: @escaping @Sendable () -> Void)
    {
        let ending = center.addObserver(forName: NSApplication.willTerminateNotification, object: nil, queue: nil) { _ in
            openAgain()
        }
        quit()
        center.removeObserver(ending)
    }

    /// A shell that waits for the run `pid` to end, then opens the app at `path`.
    private static func openOnceEnded(_ pid: Int32, _ path: String) {
        let wait = Process()
        wait.executableURL = URL(filePath: "/bin/sh")
        wait.arguments = ["-c", "while /bin/kill -0 \"$0\" 2>/dev/null; do /bin/sleep 0.2; done; /usr/bin/open \"$1\"",
                          String(pid), path]
        try? wait.run()
    }
}

/// Backup & Export: what is kept here and the room it takes on this Mac, then the database as a snapshot, or the mail
/// as a zip any mail program reads (the page's `Backup`).
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
            SettingLine(String(localized: "Space on this Mac")) {
                Text(plan.map { Self.size($0.databaseBytes) } ?? "…")
                    .font(.meadow(.ui, 13))
                    .foregroundStyle(meadow.ink2)
            }
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

    /// The room the mail kept here takes on this Mac, said as Finder says a file's size.
    static func size(_ bytes: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
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
            status.say(Problem(answer).description, refused: true)
            return
        }
        do {
            try answer.body.write(to: url, options: .atomic)
            status.say(String(localized: "Saved \(url.lastPathComponent)"))
        } catch {
            status.say(String(localized: "Couldn’t save \(url.lastPathComponent)"), refused: true)
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
