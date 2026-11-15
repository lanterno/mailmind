#if os(macOS)
import AppKit
import SwiftUI

/// The Mac app's window, as Meadow draws it (Mac/Meadow): the top bar on the sky, and below it the accounts and their
/// places, the mail in the one chosen, and the message chosen in it.
struct MailWindow: View {
    @State private var app = MailApp()

    var body: some View {
        content
            .frame(minWidth: 900, minHeight: 560)
            .task { await app.start() }
            .task { await HomeMeasure.shared.watch(app) }
            // The palette and the dialogs on the scrim, the snooze chooser and the toasts; inside the desk's surfaces,
            // whose desk the palette asks and acts with.
            .modifier(WindowOverlays(mailbox: app.mailbox))
            // Settings, the first run and the status: alerts, the welcome and the guide.
            .modifier(StatusHooks(core: app.stage == .open ? app.core : nil, mailbox: app.mailbox))
            // The assistant, curation, attention and the calendar: their sheets, the mailbox's hooks (Mac/Assistant).
            .modifier(DeskSurfaces(app: app))
            .meadowLit()
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
            // The rail, the list and the top bar are the mailbox's (Mac/Mailbox); the reader and writing are
            // Mac/Reader's and Mac/Compose's.
            MeadowFrame(app: app, mailbox: app.mailbox) {
                ReaderPane(app: app)
            }
        }
    }
}
#endif
