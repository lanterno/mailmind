#if os(macOS)
import AppKit
import Observation
import SwiftUI

/// What the window says of what was just done, as the page's toasts say it: "Snoozed", "Muted", "Couldn't
/// RSVP". One at a time, gone after a few seconds, and read out by VoiceOver. One that can be taken back offers Undo
/// for longer, as the page's undo float does, and names what it takes back for Edit ▸ Undo ("Undo Snooze").
@MainActor
@Observable
final class DeskNotices {
    struct Notice: Identifiable, Equatable {
        let id = UUID()
        let text: String
        /// What Undo takes back, as Edit ▸ Undo names it ("Snooze"); nil where nothing can be taken back.
        let undo: String?

        var undoable: Bool { undo != nil }
    }

    /// How long a notice stays, and one that offers Undo (`OFFERS_MS` on the page).
    static let shows: Duration = .seconds(4)
    static let offers: Duration = .seconds(8)

    private(set) var shown: Notice?
    @ObservationIgnored private var clearing: Task<Void, Never>?
    /// Where the window says things instead, once it has one place for every notice (the mailbox's): what is said,
    /// and what Undo (`/api/undo`) takes back, if it can.
    @ObservationIgnored var relay: ((_ text: String, _ undo: String?) -> Void)?

    /// Say `text`, offering Undo where `undo` names what it takes back.
    func say(_ text: String, undo: String? = nil) {
        if let relay { return relay(text, undo) }
        let notice = Notice(text: text, undo: undo)
        shown = notice
        AccessibilityNotification.Announcement(text).post()
        clearing?.cancel()
        clearing = Task { [weak self] in
            try? await Task.sleep(for: undo != nil ? Self.offers : Self.shows)
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
