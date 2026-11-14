#if os(macOS)
import SwiftUI

/// What settings, the first run and the status add to the mail window, as one modifier the window applies: the status
/// and a meeting's reminder in the toolbar, an alert across the top while an account can't connect, a moment's notice
/// at the foot, the welcome in place of everything while there is no account, and the guide, offered once.
///
/// It is handed the core once the window has opened it, and hands it to `AppStatus`.
struct StatusHooks: ViewModifier {
    let core: Core?
    var status = AppStatus.shared

    func body(content: Content) -> some View {
        @Bindable var status = status
        content
            .safeAreaInset(edge: .top, spacing: 0) { AccountAlert(status: status) }
            .overlay {
                if status.firstRun { Welcome(status: status) }
            }
            .overlay(alignment: .bottom) { NoticeBanner(status: status) }
            .toolbar {
                ToolbarItem(placement: .status) {
                    HStack(spacing: 10) {
                        StatusPill(status: status)
                        SoonPill(status: status)
                    }
                }
            }
            .sheet(isPresented: $status.guiding) {
                GuideSheet(steps: Guide.steps(ai: status.digest?.ai.engine != "none",
                                              singleKeys: status.preferences?.singleKeys ?? true)) {
                    status.guiding = false
                }
            }
            .task(id: core.map(ObjectIdentifier.init)) {
                if let core { await status.attach(core) }
            }
            .onChange(of: offersGuide) { _, offers in
                guard offers else { return }
                // Offered, not finished: Skip or quitting halfway both count.
                status.setPreferences(.init(guideOffered: true))
                status.guiding = true
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
        }
        .commands { StatusCommands() }
        SwiftUI.Window("MailMind Help", id: HelpWindow.id) {
            HelpWindow(status: .shared)
        }
        .windowResizability(.contentSize)
    }
}
#endif
