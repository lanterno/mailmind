#if os(macOS)
import SwiftUI

/// What settings, the first run and the status add to the mail window, as one modifier the window applies: the
/// welcome in place of everything while there is no account, the guide, offered once, and the keys and palette entries
/// for the keyboard's legend, Settings and a sync. Its moment's notice is one of the window's toasts. The status, a
/// meeting's reminder and the alert while an account can't connect stand in Meadow's top bar and under it
/// (Mac/Meadow).
///
/// It is handed the core once the window has opened it, and hands it to `AppStatus`.
struct StatusHooks: ViewModifier {
    let core: Core?
    /// The window's mailbox, whose keys and palette reach help, Settings, a sync and a check against the server.
    var mailbox: Mailbox?
    var status = AppStatus.shared
    @Environment(\.openSettings) private var openSettings

    func body(content: Content) -> some View {
        @Bindable var status = status
        content
            .overlay {
                if status.firstRun { Welcome(status: status) }
            }
            .modifier(FirstAccountSheet(status: status))
            .sheet(isPresented: $status.guiding) {
                GuideSheet(steps: Guide.steps(ai: status.digest?.ai.isOn == true,
                                              singleKeys: status.preferences?.singleKeys ?? true)) {
                    status.guiding = false
                }
            }
            .task(id: core.map(ObjectIdentifier.init)) {
                if let core { await status.attach(core) }
            }
            .onAppear { joinMailbox() }
            // Another window let go of the mailbox's hooks as it closed: this one's hang theirs again.
            .onChange(of: mailbox?.joinRound) { joinMailbox() }
            // AI turned on or off in Settings: the mailbox reads how it stands again, for what it offers of it.
            .onReceive(NotificationCenter.default.publisher(for: .mailmindAIChanged)) { _ in
                Task { await mailbox?.readDigest() }
            }
            .onChange(of: offersGuide) { _, offers in
                guard offers else { return }
                // Offered, not finished: Skip or quitting halfway both count.
                status.setPreferences(.init(guideOffered: true))
                status.guiding = true
            }
    }

    private func joinMailbox() {
        let status = status, openSettings = openSettings
        mailbox?.join("status") { mailbox in
            mailbox.keys["?"] = { status.showingKeys = true }
            mailbox.keys[","] = { openSettings() }
            mailbox.more.append { _ in
                [
                    MailCommand(id: "help", group: .commands, label: String(localized: "Keyboard shortcuts"),
                                hint: "?") {
                        status.showingKeys = true
                    },
                    MailCommand(id: "settings", group: .commands, label: String(localized: "Settings"), hint: ",") {
                        openSettings()
                    },
                    MailCommand(id: "sync", group: .commands, label: String(localized: "Sync now")) {
                        Task { await status.syncNow() }
                    },
                    MailCommand(id: "reconcile", group: .commands,
                                label: String(localized: "Check mail against the server")) {
                        Task { await status.reconcile() }
                    },
                ]
            }
        }
    }

    /// The guide is offered once the first account has mail, with nothing else in the way.
    private var offersGuide: Bool {
        Guide.offers(preferences: status.preferences, accounts: status.accounts.count,
                     hasMail: (status.digest?.stats.total ?? 0) > 0,
                     busy: status.firstRun || status.guiding || status.addingFirstAccount)
    }
}

/// The first run's account sheet, hung on the window rather than on the welcome: saving the first account ends the
/// first run and takes the welcome away, and a Proton account's sheet stays open after that, saying "Proton Bridge
/// isn't running…" with its Test, until Done.
struct FirstAccountSheet: ViewModifier {
    let status: AppStatus

    func body(content: Content) -> some View {
        @Bindable var status = status
        content
            .sheet(isPresented: $status.addingFirstAccount) {
                AccountForm(status: status, draft: AccountDraft(), testsFirst: true) { _ in
                    status.addingFirstAccount = false
                }
            }
    }
}

/// The menu bar's part of the status: Get New Mail in the Mailbox's place in the File menu, and the Help menu: MailMind
/// Help, the guide, Copy Diagnostics and Report a Problem. Checking mail against the server is the status's own, in its
/// menu (`StatusMenu`).
struct StatusCommands: Commands {
    var body: some Commands {
        CommandGroup(after: .newItem) {
            StatusMenuItems(status: .shared)
        }
        CommandGroup(replacing: .help) {
            HelpMenuItems(status: .shared)
        }
    }
}

private struct StatusMenuItems: View {
    let status: AppStatus

    var body: some View {
        Divider()
        Button("Get New Mail") { Task { await status.syncNow() } }
            .keyboardShortcut("n", modifiers: [.command, .shift])
            .disabled(status.core == nil || status.syncing)
    }
}

private struct HelpMenuItems: View {
    let status: AppStatus
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("MailMind Help") { openWindow(id: HelpWindow.id) }
            .keyboardShortcut("?", modifiers: .command)
        Button("Show Me Around") { status.guiding = true }
            .disabled(status.core == nil || status.firstRun)
        // What went wrong, for whoever gave you MailMind: with no addresses, subjects, names or passwords in it.
        Divider()
        Button("Copy Diagnostics") { Task { await Diagnostics.copy() } }
        Button("Report a Problem…") { Task { await Diagnostics.report() } }
    }
}

/// The scenes settings and the status add to the app: Settings (⌘,) and MailMind Help.
struct StatusScenes: Scene {
    var body: some Scene {
        Settings {
            SettingsWindow(status: .shared)
                .meadowLit()
        }
        // Each window's head is Meadow's, the traffic lights on it, with no title bar of the system's over it.
        .windowStyle(.hiddenTitleBar)
        .commands { StatusCommands() }
        SwiftUI.Window("MailMind Help", id: HelpWindow.id) {
            HelpWindow(status: .shared)
                .meadowLit()
        }
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentSize)
    }
}
#endif
