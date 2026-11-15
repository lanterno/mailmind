#if os(macOS)
import AppKit
import Observation
import SwiftUI

/// What the window says of what was just done, as the page's toasts say it: "Snoozed", "Muted", "Couldn't
/// RSVP". One at a time, gone after a few seconds, and read out by VoiceOver. One that can be taken back offers Undo
/// for longer, as the page's undo float does.
@MainActor
@Observable
final class DeskNotices {
    struct Notice: Identifiable, Equatable {
        let id = UUID()
        let text: String
        let undoable: Bool
    }

    /// How long a notice stays, and one that offers Undo (`OFFERS_MS` on the page).
    static let shows: Duration = .seconds(4)
    static let offers: Duration = .seconds(8)

    private(set) var shown: Notice?
    @ObservationIgnored private var clearing: Task<Void, Never>?
    /// Where the window says things instead, once it has one place for every notice (the mailbox's): what is said,
    /// and whether Undo (`/api/undo`) can take it back.
    @ObservationIgnored var relay: ((_ text: String, _ undoable: Bool) -> Void)?

    func say(_ text: String, undoable: Bool = false) {
        if let relay { return relay(text, undoable) }
        let notice = Notice(text: text, undoable: undoable)
        shown = notice
        AccessibilityNotification.Announcement(text).post()
        clearing?.cancel()
        clearing = Task { [weak self] in
            try? await Task.sleep(for: undoable ? Self.offers : Self.shows)
            guard !Task.isCancelled, self?.shown?.id == notice.id else { return }
            self?.shown = nil
        }
    }

    func clear() {
        clearing?.cancel()
        shown = nil
    }
}

/// The notice as one of the window's toasts, with Undo when what it says can be taken back. Once the window relays
/// notices to the mailbox, its toast says them instead, and this stays empty.
struct DeskNoticeBanner: View {
    let notices: DeskNotices
    let undo: () -> Void

    var body: some View {
        if let notice = notices.shown {
            MeadowToast(icon: MailboxToast.icon(notice.text), text: Text(notice.text)) {
                if notice.undoable {
                    MeadowToastButton(title: String(localized: "Undo"), icon: .undo) {
                        notices.clear()
                        undo()
                    }
                }
            }
            .id(notice.id)
        }
    }
}
#endif
