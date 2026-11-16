#if os(macOS)
import SwiftUI

/// What the areas add around the mail window, in one modifier the window applies.
///
/// The palette and the dialogs on the scrim, the snooze chooser and the toasts (`WindowOverlays`), inside the desk's
/// surfaces, whose desk the palette asks and acts with; Settings, the first run and the status: alerts, the welcome
/// and the guide (`StatusHooks`); the assistant, curation, attention and the calendar: their sheets, the mailbox's
/// hooks (`DeskSurfaces`).
///
/// The window's desk, shell and pane go with it as it closes, and so do the hooks they hung on the process's one
/// mailbox (`Mailbox.leave`): the window opened again from the Dock hangs its own.
struct WindowAreas: ViewModifier {
    let app: MailApp
    /// What the status, Settings and the first run show: the app's, unless a test gives its own.
    var status = AppStatus.shared

    func body(content: Content) -> some View {
        content
            .modifier(WindowOverlays(mailbox: app.mailbox))
            .modifier(StatusHooks(core: app.stage == .open ? app.core : nil, mailbox: app.mailbox, status: status))
            .modifier(DeskSurfaces(app: app))
            .onDisappear { app.mailbox.leave() }
    }
}
#endif
