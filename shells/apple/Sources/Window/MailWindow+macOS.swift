#if os(macOS)
import AppKit
import SwiftUI

/// The Mac app's window, as Meadow draws it (Mac/Meadow): the top bar on the sky, and below it the accounts and their
/// places, the mail in the one chosen, and the message chosen in it.
struct MailWindow: View {
    /// The process's one: a window opened again from the Dock shows the core already open, never a second over the
    /// same data.
    private let app = MailApp.shared

    var body: some View {
        content
            .frame(minWidth: 900, minHeight: 560)
            .task { await app.start() }
            .task { await HomeMeasure.shared.watch(app) }
            // The palette, the dialogs and the toasts; Settings, the first run and the status; the desk's surfaces.
            .modifier(WindowAreas(app: app))
            .meadowLit()
    }

    @ViewBuilder private var content: some View {
        switch app.stage {
        case .opening:
            ProgressView("Opening MailMind")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .failed(let why):
            // Its words can be copied, and it offers a way on: the details, the folder, or a fresh start.
            LaunchFailure(why: why, folder: app.folder) { await app.startAgain() }
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
