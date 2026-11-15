#if os(macOS)
import SwiftUI

/// The list column: the place's head (the Inbox's tabs, a search's scope, a lane's), its mail, a lane or the Document
/// Hub, "Load more" when there is more, and what a person was last told, with its Undo. The search field and the
/// verbs are in the toolbar above it, as on a Mac (app/App.tsx, app/TopBar.tsx).
struct MailboxColumn: View {
    let app: MailApp
    @Bindable var mailbox: Mailbox
    @Environment(\.undoManager) private var undoManager
    @State private var text = ""
    @State private var offers: [API.SearchSuggestion] = []
    @FocusState private var searching: Bool

    var body: some View {
        VStack(spacing: 0) {
            ListHead(app: app, mailbox: mailbox)
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .overlay(alignment: .bottom) { ToastView(mailbox: mailbox) }
        .navigationTitle(mailbox.title)
        .navigationSubtitle(countText)
        .toolbar { MailboxToolbar(mailbox: mailbox) }
        .searchable(text: $text, placement: .toolbar, prompt: Text("Search, or ask with ?"))
        .searchFocused($searching)
        .searchSuggestions {
            ForEach(offers, id: \.value) { offer in
                HStack {
                    Text(offer.label)
                    if !offer.detail.isEmpty {
                        Text(offer.detail).foregroundStyle(.secondary)
                    }
                }
                .searchCompletion(offer.value)
            }
        }
        .onSubmit(of: .search) { mailbox.submit(text) }
        .task(id: text) { await lookUp(text) }
        .onChange(of: text) { _, now in
            // Clearing the field leaves the search, as Esc does in the page.
            if now.isEmpty, app.place?.search != nil { mailbox.leaveSearch() }
        }
        .onChange(of: app.place, initial: true) { _, place in
            let search = place?.search
            text = search.map { $0.asked ?? $0.q } ?? ""
        }
        .onChange(of: searching) { _, now in mailbox.typing = now }
        .onChange(of: mailbox.searchFocus) { _, wanted in
            if wanted { searching = true }
            mailbox.searchFocus = false
        }
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
        if case .desk(let screen)? = app.place {
            DeskScreen(place: screen)
        } else if let documents = mailbox.documents {
            DocumentHub(mailbox: mailbox, model: documents)
        } else if let lane = mailbox.lane {
            LaneColumn(mailbox: mailbox, model: lane)
        } else if let list = app.list {
            VStack(spacing: 0) {
                MailboxTable(list: list, mailbox: mailbox, chosen: mailbox.marked,
                             showsAccount: app.place?.showsAccount ?? true)
                    .id(ObjectIdentifier(list))
                    .overlay { EmptyList(app: app, mailbox: mailbox, list: list) }
                if mailbox.facts.hasMore {
                    Divider()
                    Button(app.place?.search != nil ? "Load more results" : "Load 200 more") { mailbox.readMore() }
                        .buttonStyle(.link)
                        .padding(8)
                }
            }
        } else {
            ContentUnavailableView("Choose a mailbox", systemImage: "tray")
        }
    }

    /// What the list holds, as its head counts it (`count` in App.tsx).
    private var countText: String {
        if mailbox.documents != nil { return mailbox.documents?.countText ?? "" }
        if let lane = mailbox.lane {
            guard lane.loaded else { return "" }
            return Words.count(lane.hits.count, more: lane.hasMore, noun: lane.search.lane == .feed ? "newsletter" : "receipt")
        }
        guard let list = app.list, list.firstRowsAt != nil else { return "" }
        let facts = mailbox.facts
        let n = facts.total ?? list.total
        if app.place?.search != nil { return Words.count(n, more: facts.hasMore, noun: "result") }
        let noun = mailbox.grouped ? "conversation" : "message"
        return Words.count(n, more: facts.hasMore, noun: noun)
            + (facts.syncing || facts.catchingUp ? " · reading the rest…" : "")
    }

    private var saving: Binding<Bool> {
        Binding(get: { mailbox.savingView != nil }, set: { if !$0 { mailbox.savingView = nil } })
    }

    private var carding: Binding<Bool> {
        Binding(get: { mailbox.contact != nil }, set: { if !$0 { mailbox.contact = nil } })
    }

    /// What the server finishes the word being typed with, a moment after the typing stops (`WAIT`).
    private func lookUp(_ typed: String) async {
        offers = []
        guard searching, !typed.trimmingCharacters(in: .whitespaces).isEmpty, !typed.hasPrefix("?"), !typed.hasPrefix("!"),
              typed != app.place?.search?.q else { return }
        try? await Task.sleep(for: .milliseconds(140))
        guard !Task.isCancelled else { return }
        let found = await mailbox.suggestions(for: typed)
        guard !Task.isCancelled else { return }
        var seen = Set<String>()
        offers = found.filter { seen.insert($0.value).inserted }
    }
}

/// The verbs on what is chosen or open, as the toolbar holds them on a Mac.
struct MailboxToolbar: ToolbarContent {
    let mailbox: Mailbox

    var body: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            let none = !mailbox.hasTarget
            Button("Archive", systemImage: "archivebox") { mailbox.file(.archive) }
                .help("Archive (⌃⌘A)")
                .disabled(none)
            Button("Delete", systemImage: "trash") { mailbox.file(.trash) }
                .help("Delete (⌘⌫)")
                .disabled(none)
            Button("Junk", systemImage: "xmark.bin") { mailbox.file(.junk) }
                .help("Mark as Junk (⇧⌘J)")
                .disabled(none)
            Button("Move To…", systemImage: "folder") { mailbox.askMove() }
                .help("Move To… (⇧⌘M)")
                .disabled(none)
            let unread = mailbox.chosenRows.contains { !$0.seen }
            Button(unread ? "Mark as Read" : "Mark as Unread", systemImage: unread ? "envelope.open" : "envelope.badge") {
                mailbox.setSeen(mailbox.chosenRows.count > 1 ? unread : nil)
            }
            .help("Mark as Read or Unread (⇧⌘U)")
            .disabled(none)
            Button("Star", systemImage: "star") { mailbox.star() }
                .help("Star (⇧⌘L)")
                .disabled(none)
        }
    }
}

/// The place's head: the Inbox's tabs, a search's scope and how to save it, a lane's, and the rows marked.
private struct ListHead: View {
    let app: MailApp
    @Bindable var mailbox: Mailbox

    var body: some View {
        let place = app.place
        VStack(alignment: .leading, spacing: 6) {
            if let search = place?.search {
                if let lane = search.lane {
                    LaneHead(mailbox: mailbox, search: search, lane: lane)
                } else {
                    SearchHead(app: app, mailbox: mailbox, search: search)
                }
            } else if place?.isInbox == true {
                HStack {
                    Picker("Inbox", selection: $mailbox.tab) {
                        ForEach(InboxTab.allCases, id: \.self) { tab in
                            Text(tabTitle(tab)).tag(tab)
                        }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .fixedSize()
                    Spacer()
                    GroupingButton(mailbox: mailbox)
                }
            } else if place != nil, place != .documents {
                HStack {
                    Spacer()
                    GroupingButton(mailbox: mailbox)
                }
            }
            let marked = mailbox.chosenRows.count + mailbox.unheld.count
            if marked > 1 {
                HStack {
                    Text("\(marked) selected").font(.callout.weight(.medium))
                    Spacer()
                    Button("Clear") { mailbox.clearMarks() }
                        .buttonStyle(.link)
                        .help("Clear the selection (Esc)")
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(.tint.opacity(0.12), in: .rect(cornerRadius: 6))
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, place == .documents ? 0 : 8)
    }

    /// A tab's words with how many rows it holds (`counts` in App.tsx).
    private func tabTitle(_ tab: InboxTab) -> String {
        let facts = mailbox.facts
        let n: Int? = switch tab {
        case .needs: facts.needs
        case .all: mailbox.tab == .all ? (facts.total ?? app.list?.total) : nil
        case .unread: facts.unread
        }
        return n.map { "\(tab.title) \($0)\(tab == .all && facts.hasMore ? "+" : "")" } ?? tab.title
    }
}

/// Conversations or single messages, as the server keeps the preference.
private struct GroupingButton: View {
    let mailbox: Mailbox

    var body: some View {
        Toggle(isOn: Binding(get: { mailbox.grouped }, set: { mailbox.setGrouped($0) })) {
            Label("Conversations", systemImage: "bubble.left.and.bubble.right")
        }
        .toggleStyle(.button)
        .labelStyle(.iconOnly)
        .help(mailbox.grouped ? "Grouped into conversations: show single messages" : "Group into conversations")
    }
}

/// A search's head: what it searched, where, whether it is held to one conversation, and saving it as a view.
private struct SearchHead: View {
    let app: MailApp
    let mailbox: Mailbox
    let search: Search

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            Text(search.q).fontWeight(.semibold).lineLimit(1)
            if let thread = search.thread {
                Button {
                    app.place = .search(Search(q: search.q, account: search.account))
                } label: {
                    Label("This conversation only", systemImage: "xmark.circle.fill")
                        .labelStyle(TrailingIcon())
                }
                .buttonStyle(.borderless)
                .help("Search this account instead of only this conversation")
                .accessibilityIdentifier(thread)
            } else {
                ScopeMenu(mailbox: mailbox, account: search.account)
                if let open = conversation {
                    Button("This conversation only") {
                        app.place = .search(Search(q: search.q, account: open.account, thread: open.threadId))
                    }
                    .buttonStyle(.link)
                }
            }
            if let asked = search.asked {
                Button {
                    mailbox.keepsLiteral?(asked)
                    app.place = .search(Search(q: asked, account: search.account))
                } label: {
                    Label("Translated from “\(asked)”", systemImage: "xmark.circle.fill").labelStyle(TrailingIcon())
                }
                .buttonStyle(.borderless)
                .help("Search for “\(asked)” as typed instead")
            }
            Spacer()
            if !mailbox.onSavedView {
                Button("Save as View…") { mailbox.savingView = search.q }
                    .buttonStyle(.link)
                    .help("Save as a view (v)")
            }
        }
        .font(.callout)
    }

    /// The conversation open in the reader, which a search can be held to (`m.conversation`).
    private var conversation: API.FolderRow? {
        guard mailbox.chosenRows.count == 1, let row = mailbox.chosenRows.first,
              row.isConversation(grouped: mailbox.grouped) else { return nil }
        return row
    }
}

/// A lane's head (`client-ctx`): what it is, and where it searches.
private struct LaneHead: View {
    let mailbox: Mailbox
    let search: Search
    let lane: Lane

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: lane == .feed ? "book" : "receipt").foregroundStyle(.secondary)
            Text(lane.title).fontWeight(.semibold)
            Text(lane == .feed ? "· a calm reading river ·" : "· receipts & invoices ·").foregroundStyle(.secondary)
            ScopeMenu(mailbox: mailbox, account: search.account)
            Spacer()
        }
        .font(.callout)
    }
}

/// Every account, or the one the search was asked from (`ScopeToggle`).
private struct ScopeMenu: View {
    let mailbox: Mailbox
    let account: String

    var body: some View {
        if account.isEmpty {
            Text("all accounts").foregroundStyle(.secondary)
        } else {
            Menu(mailbox.scope == .all ? "all accounts" : "this account") {
                Button("All Accounts") { mailbox.setScope(.all) }
                Button("This Account") { mailbox.setScope(.account) }
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
        }
    }
}

/// A label with its icon after its words.
private struct TrailingIcon: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 3) {
            configuration.title
            configuration.icon.foregroundStyle(.secondary)
        }
    }
}

/// What the list says when it has no rows to show, in the page's words.
private struct EmptyList: View {
    let app: MailApp
    let mailbox: Mailbox
    let list: MailListModel

    var body: some View {
        let facts = mailbox.facts
        let searching = app.place?.search != nil
        if list.firstRowsAt == nil {
            if let problem = list.problem {
                failed(searching ? "Search failed." : "Couldn’t load messages.", problem)
            } else {
                ProgressView().controlSize(.small)
            }
        } else if (facts.total ?? list.total) == 0 {
            if let problem = list.problem {
                failed(searching ? "Search failed." : "Couldn’t load messages.", problem)
            } else if app.place?.isInbox == true, mailbox.tab != .all, (facts.read ?? 0) > 0 {
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
