#if os(macOS)
import SwiftUI

/// What settings, the first run and the status add to the mail window, as one modifier the window applies: a moment's
/// notice at the foot, the welcome in place of everything while there is no account, the guide, offered once, and the
/// keys and palette entries for help, Settings and a sync. The status, a meeting's reminder and the alert while an
/// account can't connect stand in Meadow's top bar and under it (Mac/Meadow).
///
/// It is handed the core once the window has opened it, and hands it to `AppStatus`.
struct StatusHooks: ViewModifier {
    let core: Core?
    /// The window's mailbox, whose keys and palette reach help, Settings, a sync and a reconcile.
    var mailbox: Mailbox?
    var status = AppStatus.shared
    @Environment(\.openWindow) private var openWindow
    @Environment(\.openSettings) private var openSettings

    func body(content: Content) -> some View {
        @Bindable var status = status
        content
            .overlay {
                if status.firstRun { Welcome(status: status) }
            }
            .overlay(alignment: .bottom) { NoticeBanner(status: status) }
            .sheet(isPresented: $status.guiding) {
                GuideSheet(steps: Guide.steps(ai: status.digest?.ai.engine != "none",
                                              singleKeys: status.preferences?.singleKeys ?? true)) {
                    status.guiding = false
                }
            }
            .task(id: core.map(ObjectIdentifier.init)) {
                if let core { await status.attach(core) }
            }
            .onAppear { joinMailbox() }
            .onChange(of: offersGuide) { _, offers in
                guard offers else { return }
                // Offered, not finished: Skip or quitting halfway both count.
                status.setPreferences(.init(guideOffered: true))
                status.guiding = true
            }
    }

    private func joinMailbox() {
        let status = status, openWindow = openWindow, openSettings = openSettings
        mailbox?.join("status") { mailbox in
            mailbox.keys["?"] = { openWindow(id: HelpWindow.id) }
            mailbox.keys[","] = { openSettings() }
            mailbox.more.append { _ in
                [
                    MailCommand(id: "help", group: .commands, label: String(localized: "Keyboard help"), hint: "?") {
                        openWindow(id: HelpWindow.id)
                    },
                    MailCommand(id: "settings", group: .commands, label: String(localized: "Settings"), hint: ",") {
                        openSettings()
                    },
                    MailCommand(id: "sync", group: .commands, label: String(localized: "Sync now")) {
                        Task { await status.syncNow() }
                    },
                    MailCommand(id: "reconcile", group: .commands, label: String(localized: "Reconcile (prune ghosts)")) {
                        Task { await status.reconcile() }
                    },
                ]
            }
        }
    }

    /// The guide is offered once the first account has mail, with nothing else in the way.
    private var offersGuide: Bool {
        Guide.offers(preferences: status.preferences, accounts: status.accounts.count,
                     hasMail: (status.digest?.stats.total ?? 0) > 0, busy: status.firstRun || status.guiding)
    }
}

/// The menu bar's part of the status: Get New Mail and Reconcile in the Mailbox's place in the File menu, and the Help
/// menu: MailMind Help and the guide.
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
        Button("Reconcile") { Task { await status.reconcile() } }
            .disabled(status.core == nil || status.reconciling)
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
    }
}

/// The scenes settings and the status add to the app: Settings (⌘,) and MailMind Help.
struct StatusScenes: Scene {
    var body: some Scene {
        Settings {
            SettingsWindow(status: .shared)
                .meadowLit()
        }
        .commands { StatusCommands() }
        SwiftUI.Window("MailMind Help", id: HelpWindow.id) {
            HelpWindow(status: .shared)
                .meadowLit()
        }
        .windowResizability(.contentSize)
    }
}
#endif
