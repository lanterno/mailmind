#if os(macOS)
import AppKit
import SwiftUI

/// The Mac app's window: the accounts and their folders, the mail in the one chosen, and the message chosen in it.
struct MailWindow: View {
    @State private var app = MailApp()

    var body: some View {
        content
            .frame(minWidth: 900, minHeight: 560)
            .task { await app.start() }
            .task { await HomeMeasure.shared.watch(app) }
            // Settings, the first run and the status: the toolbar's status, alerts, the welcome and the guide.
            .modifier(StatusHooks(core: app.stage == .open ? app.core : nil, mailbox: app.mailbox))
            // The assistant, curation, attention and the calendar: their sheets, the mailbox's hooks (Mac/Assistant).
            .modifier(DeskSurfaces(app: app))
    }

    @ViewBuilder private var content: some View {
        switch app.stage {
        case .opening:
            ProgressView("Opening MailMind")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .failed(let why):
            ContentUnavailableView("MailMind didn't open", systemImage: "exclamationmark.triangle",
                                   description: Text(why))
        case .open:
            NavigationSplitView {
                // The mailbox's sidebar and list (Mac/Mailbox).
                MailboxSidebar(app: app, mailbox: app.mailbox)
                    .navigationSplitViewColumnWidth(min: 180, ideal: 220)
            } content: {
                MailboxColumn(app: app, mailbox: app.mailbox)
                    .navigationSplitViewColumnWidth(min: 300, ideal: 380)
            } detail: {
                // The reader and writing: Mac/Reader and Mac/Compose.
                ReaderPane(app: app)
            }
        }
    }
}
#endif
