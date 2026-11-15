#if os(macOS)
import SwiftUI

/// The list column: the place's head (its name, the Inbox's tabs, a search's scope, a lane's, the rows marked), its
/// mail, a lane or the Document Hub, "Load more" when there is more, and what a person was last told, with its Undo.
/// The brief, in the pane beside the Inbox, leaves the Inbox’s list here; an answer and the Screener stand here.
/// The search field is the top bar's (Mac/Meadow).
struct MailboxColumn: View {
    let app: MailApp
    @Bindable var mailbox: Mailbox
    @Environment(\.undoManager) private var undoManager
    @Environment(\.meadow) private var meadow

    var body: some View {
        VStack(spacing: 0) {
            if !(app.place?.listed.isDesk ?? false) {
                MeadowListHead(app: app, mailbox: mailbox)
            }
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                // Mail that landed elsewhere, and what came in since you last looked (Mac/Assistant).
                .overlay(alignment: .top) { DeskNewMailPill() }
        }
        .overlay(alignment: .bottom) { ToastView(mailbox: mailbox) }
        .sheet(item: $mailbox.moving) { ask in MoveToSheet(mailbox: mailbox, ask: ask) }
        .sheet(isPresented: $mailbox.paletteOpen) { PaletteSheet(mailbox: mailbox) }
        .sheet(isPresented: saving) { SaveViewSheet(mailbox: mailbox, query: mailbox.savingView ?? "") }
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
                    .overlay { EmptyList(app: app, mailbox: mailbox, list: list) }
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

    private var saving: Binding<Bool> {
        Binding(get: { mailbox.savingView != nil }, set: { if !$0 { mailbox.savingView = nil } })
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

/// What the list says when it has no rows to show, in the page's words.
private struct EmptyList: View {
    let app: MailApp
    let mailbox: Mailbox
    let list: MailListModel

    var body: some View {
        let facts = mailbox.facts
        let place = app.place?.listed
        let searching = place?.search != nil
        if list.firstRowsAt == nil {
            if let problem = list.problem {
                failed(searching ? "Search failed." : "Couldn’t load messages.", problem)
            } else {
                ProgressView().controlSize(.small)
            }
        } else if (facts.total ?? list.total) == 0 {
            if let problem = list.problem {
                failed(searching ? "Search failed." : "Couldn’t load messages.", problem)
            } else if place?.isInbox == true, mailbox.tab != .all, (facts.read ?? 0) > 0 {
                ContentUnavailableView {
                    Text(mailbox.tab == .needs ? Words.needsYou : Words.unread)
                } description: {
                    Text("Everything else is in Everything.")
                } actions: {
                    Button("Show Everything") { mailbox.tab = .all }
                }
            } else if facts.syncing {
                ContentUnavailableView {
                    ProgressView().controlSize(.small)
                    Text("Reading this folder for the first time…")
                }
            } else if facts.syncFailed {
                ContentUnavailableView {
                    Text("Couldn’t sync this folder.")
                } actions: {
                    Button("Retry") { list.refresh() }
                }
            } else {
                ContentUnavailableView(searching ? Words.matches : Words.list,
                                       systemImage: searching ? "magnifyingglass" : "tray")
            }
        }
    }

    private func failed(_ title: String, _ why: String) -> some View {
        ContentUnavailableView {
            Label(title, systemImage: "exclamationmark.triangle")
        } description: {
            Text(why)
        } actions: {
            Button("Retry") { list.refresh() }
        }
    }
}

/// What a person was last told, with its Undo, at the foot of the list for a moment (lib/toast.tsx). VoiceOver says it
/// as it comes.
struct ToastView: View {
    let mailbox: Mailbox

    var body: some View {
        ZStack {
            if let toast = mailbox.toast {
                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(toast.text).font(.callout.weight(.medium))
                        if let detail = toast.detail, !detail.isEmpty {
                            Text(detail).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if toast.undo != nil {
                        Button("Undo") { mailbox.undo() }
                            .buttonStyle(.borderless)
                            .fontWeight(.semibold)
                            .help("Undo (⌘Z)")
                    }
                    Button {
                        mailbox.dismissToast()
                    } label: {
                        Image(systemName: "xmark").font(.caption)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel("Dismiss")
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(.regularMaterial, in: .capsule)
                .overlay(Capsule().strokeBorder(.separator))
                .shadow(radius: 6, y: 2)
                .padding(14)
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .id(toast.id)
                .onAppear {
                    AccessibilityNotification.Announcement(toast.text).post()
                }
            }
        }
        .animation(.snappy(duration: 0.2), value: mailbox.toast)
    }
}
#endif
