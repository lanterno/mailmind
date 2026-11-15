#if os(macOS)
import SwiftUI

/// The list column: the place's head (its name, the Inbox's tabs, a search's scope, a lane's, the rows marked), its
/// mail, a lane or the Document Hub, and "Load more" when there is more. The brief, in the pane beside the Inbox,
/// leaves the Inbox’s list here; an answer and the Screener stand here. The search field is the top bar's
/// (Mac/Meadow); what a person was last told, the palette, Move To and Save View float over the window
/// (`WindowOverlays`).
struct MailboxColumn: View {
    let app: MailApp
    @Bindable var mailbox: Mailbox
    @Environment(\.undoManager) private var undoManager
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(spacing: 0) {
            // A place that is a page across the window (the paper trail) has a head of its own.
            if !(app.place?.listed.isDesk ?? false), !(app.place?.spansTheWindow == true && app.selection.copy == nil) {
                MeadowListHead(app: app, mailbox: mailbox)
            }
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                // Mail that landed elsewhere, and what came in since you last looked (Mac/Assistant).
                .overlay(alignment: .top) { DeskNewMailPill() }
        }
        .sheet(isPresented: carding) {
            if let address = mailbox.contact { ContactCard(mailbox: mailbox, address: address) }
        }
        .focusedSceneValue(\.mailbox, mailbox)
        .onAppear { mailbox.undoManager = undoManager }
        .onChange(of: undoManager) { _, now in mailbox.undoManager = now }
    }

    @ViewBuilder private var content: some View {
        if case .desk(let screen)? = app.place?.listed {
            DeskScreen(place: screen)
        } else if let documents = mailbox.documents {
            DocumentHub(mailbox: mailbox, model: documents)
        } else if let lane = mailbox.lane {
            LaneColumn(mailbox: mailbox, model: lane)
        } else if let list = app.list {
            VStack(spacing: 0) {
                MailboxTable(list: list, mailbox: mailbox, chosen: mailbox.marked,
                             showsAccount: app.place?.listed.showsAccount ?? true)
                    .id(ObjectIdentifier(list))
                if mailbox.facts.hasMore {
                    Rectangle().fill(meadow.line).frame(height: 1)
                    Button(app.place?.search != nil ? "Load more results" : "Load 200 more") { mailbox.readMore() }
                        .meadowButton(.quiet, small: true)
                        .padding(8)
                }
            }
        } else {
            ContentUnavailableView("Choose a mailbox", systemImage: "tray")
        }
    }

    private var carding: Binding<Bool> {
        Binding(get: { mailbox.contact != nil }, set: { if !$0 { mailbox.contact = nil } })
    }
}

extension Place {
    /// One of the desk's screens.
    var isDesk: Bool {
        if case .desk = self { return true }
        return false
    }
}
#endif
