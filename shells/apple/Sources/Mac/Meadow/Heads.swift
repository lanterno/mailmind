#if os(macOS)
import AppKit
import SwiftUI

// The heads of Meadow's panels (#44): the list's (`.lhead`: the place's name, select and density, the Inbox's tabs or
// a sentence on what the place is) and the reading pane's (`.pbar`: file, delete, snooze and later; reply, all and
// forward; reading settings and focus; where the message stands in the list). The pane's bar folds its words, then
// its keys, then its second buttons as the pane narrows. The bar over rows chosen (`.bulk`) is the rows' own, under
// the list's head.

// MARK: The list's head

struct MeadowListHead: View {
    let app: MailApp
    @Bindable var mailbox: Mailbox
    @Environment(\.meadow) private var meadow

    var body: some View {
        let place = app.listed
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 6) {
                Text(verbatim: place?.title(folders: mailbox.folders(of: place?.account)) ?? "")
                    .font(.meadow(.display, 21, .bold))
                    .foregroundStyle(meadow.ink)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityAddTraits(.isHeader)
                if app.list != nil {
                    MeadowSelectButton(mailbox: mailbox)
                    MeadowDensityButton(mailbox: mailbox)
                }
            }
            .frame(minHeight: 28)
            under(place)
        }
        .padding(.top, 12)
        .padding(.leading, 14)
        .padding(.trailing, 12)
    }

    @ViewBuilder private func under(_ place: Place?) -> some View {
        if let search = place?.search {
            if let lane = search.lane {
                MeadowLaneHead(mailbox: mailbox, search: search, lane: lane)
            } else {
                MeadowSearchHead(app: app, mailbox: mailbox, search: search)
            }
        } else if place?.isInbox == true {
            MeadowTabs(tabs: InboxTab.allCases.map(tab), selection: $mailbox.tab, label: "Inbox lanes")
                // The end-to-end framework's anchor (tests/framework/anchors.toml): the list's tabs, named for the place.
                .accessibilityIdentifier("tabs")
                .padding(.top, 10)
        } else if let words = sentence(place) {
            Group {
                if let about = words.about, let count = words.count {
                    MeadowHeadLine(sentence: about) { Text(count) }
                } else {
                    Text(words.about ?? words.count ?? "")
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .font(.meadow(.ui, 12.5))
            .foregroundStyle(meadow.ink3)
            .padding(.top, 8)
            .padding(.horizontal, 2)
        }
    }

    /// A tab with what it counts (`counts` in App.tsx): Needs you's in sunlight, Everything's only once it is read.
    private func tab(_ tab: InboxTab) -> MeadowTabs<InboxTab>.Tab {
        let facts = mailbox.facts
        let n: Int? = switch tab {
        case .needs: facts.needs
        case .all: mailbox.tab == .all ? (facts.total ?? app.list?.total) : nil
        case .unread: facts.unread
        }
        let count = n.map { "\($0)\(tab == .all && facts.hasMore ? "+" : "")" }
        return .init(value: tab, title: tab.title, count: count, sun: tab == .needs)
    }

    /// What the place is, in a sentence (`.lsub`), and how much it holds: either, both, or nil for neither.
    private func sentence(_ place: Place?) -> (about: String?, count: String?)? {
        guard let place else { return nil }
        let about = MeadowListHead.about(place, role: role(of: place)).flatMap { $0.isEmpty ? nil : $0 }
        let count = MeadowListHead.count(app: app, mailbox: mailbox).flatMap { $0.isEmpty ? nil : $0 }
        return about == nil && count == nil ? nil : (about, count)
    }

    private func role(of place: Place) -> String? {
        guard case .folder(let account, let folder) = place else { return nil }
        return mailbox.folders(of: account).first { $0.name == folder }?.role
    }

    /// The mockup's sentence for a place, by its folder's role or what it is.
    static func about(_ place: Place, role: String?) -> String? {
        switch place {
        case .virtual(.waiting, _):
            return String(localized: "What you’re owed, and what you owe. Rows clear themselves when a reply lands.")
        case .virtual(.snoozed, _): return String(localized: "These come back to the inbox at the time you chose.")
        case .virtual(.replyLater, _): return String(localized: "Your reply-later pile.")
        case .virtual(.muted, _):
            return String(localized: "Conversations you muted. New replies stay out of your inbox.")
        case .documents: return nil
        default: break
        }
        switch role {
        case "trash": return String(localized: "Everything you delete stays here until your server empties it.")
        case "archive": return String(localized: "Filed mail stays searchable forever.")
        case "sent": return String(localized: "Mail you’ve sent.")
        case "drafts": return String(localized: "Unsent drafts.")
        case "junk": return String(localized: "What your server or you called junk.")
        default: return nil
        }
    }

    /// How much the list holds (`count` in App.tsx), or nothing while it is first read.
    static func count(app: MailApp, mailbox: Mailbox) -> String? {
        if let documents = mailbox.documents { return documents.countText }
        if let lane = mailbox.lane {
            guard lane.loaded else { return nil }
            let noun = lane.search.lane == .feed ? "newsletter" : "receipt"
            return Words.count(lane.hits.count, more: lane.hasMore, noun: noun)
        }
        guard let list = app.list, list.firstRowsAt != nil else { return nil }
        let facts = mailbox.facts
        let n = facts.total ?? list.total
        if app.place?.search != nil { return Words.count(n, more: facts.hasMore, noun: "result") }
        let noun = mailbox.grouped ? "conversation" : "message"
        return Words.count(n, more: facts.hasMore, noun: noun)
            + (facts.syncing || facts.catchingUp ? " · reading the rest…" : "")
    }
}

/// Select every message in the list, or with several chosen, choose none of them (`#selAllB`).
private struct MeadowSelectButton: View {
    let mailbox: Mailbox

    var body: some View {
        let marked = mailbox.marking
        Button {
            if marked { mailbox.clearMarks() } else { ListTable.selectAll(in: NSApp.keyWindow) }
        } label: {
            MeadowIconView(.check, size: 15)
        }
        .meadowButton(.quiet, small: true, pressed: marked)
        .help(marked ? "Clear the selection (Esc)" : "Select all messages (⌘A)")
        .accessibilityLabel(marked ? "Clear the selection" : "Select all messages")
    }
}

/// How much each row shows, and whether the list groups conversations (`#densB`), kept as preferences.
private struct MeadowDensityButton: View {
    let mailbox: Mailbox
    var status = AppStatus.shared
    @State private var shown = false

    var body: some View {
        Button { shown.toggle() } label: { MeadowIconView(.sliders, size: 15) }
            .meadowButton(.quiet, small: true, icon: true)
            .help("Show more or fewer lines per message")
            .accessibilityLabel("Show more or fewer lines per message")
            .popover(isPresented: $shown, arrowEdge: .bottom) {
                Form {
                    Picker("Rows", selection: Binding(
                        get: { status.preferences?.density ?? .comfortable },
                        set: { status.setPreferences(API.ChangedPreferences(density: $0)) })) {
                        Text("Comfortable").tag(API.Density.comfortable)
                        Text("Cozy").tag(API.Density.cozy)
                        Text("Compact").tag(API.Density.compact)
                    }
                    .pickerStyle(.radioGroup)
                    Toggle("Group into conversations", isOn: Binding(get: { mailbox.grouped },
                                                                     set: { mailbox.setGrouped($0) }))
                }
                .padding(16)
                .frame(width: 260)
            }
    }
}

/// The table in a window, which Select all and focus reading's keys reach.
@MainActor
enum ListTable {
    static func selectAll(in window: NSWindow?) {
        guard let window, let table = find(in: window.contentView) else { return }
        window.makeFirstResponder(table)
        table.selectAll(nil)
    }

    static func find(in view: NSView?) -> MailboxTableView? {
        guard let view else { return nil }
        if let table = view as? MailboxTableView { return table }
        for child in view.subviews {
            if let table = find(in: child) { return table }
        }
        return nil
    }
}

/// A search's head: what it searched, where, whether it is held to one conversation, and saving it as a view.
private struct MeadowSearchHead: View {
    let app: MailApp
    let mailbox: Mailbox
    let search: Search
    @Environment(\.meadow) private var meadow

    var body: some View {
        HStack(spacing: 6) {
            MeadowIconView(.search, size: 15).foregroundStyle(meadow.ink3)
            Text(search.q).fontWeight(.semibold).foregroundStyle(meadow.ink).lineLimit(1)
            if let thread = search.thread {
                Button {
                    app.place = .search(Search(q: search.q, account: search.account))
                } label: {
                    Text("This conversation only")
                    MeadowIconView(.x, size: 13)
                }
                .meadowButton(.quiet, small: true)
                .help("Search this account instead of only this conversation")
                .accessibilityIdentifier(thread)
            } else {
                MeadowScopeMenu(mailbox: mailbox, account: search.account)
                if let open = conversation {
                    Button("This conversation only") {
                        app.place = .search(Search(q: search.q, account: open.account, thread: open.threadId))
                    }
                    .meadowButton(.quiet, small: true)
                }
            }
            if let going = mailbox.translating?(), going == search.q {
                // The sentence is searched as typed while the assistant works out the search it means.
                HStack(spacing: 4) {
                    ProgressView().controlSize(.mini)
                    Text("Translating…")
                }
                .foregroundStyle(meadow.ink3)
                .accessibilityElement(children: .combine)
                .accessibilityAddTraits(.updatesFrequently)
            } else if let asked = search.asked {
                Button {
                    mailbox.keepsLiteral?(asked)
                    app.place = .search(Search(q: asked, account: search.account))
                } label: {
                    Text("Translated from “\(asked)”").lineLimit(1)
                    MeadowIconView(.x, size: 13)
                }
                .meadowButton(.quiet, small: true)
                .help("Search for “\(asked)” as typed instead")
            }
            Spacer(minLength: 0)
            if !mailbox.onSavedView {
                Button("Save as View…") { mailbox.savingView = search.q }
                    .meadowButton(.quiet, small: true)
                    .help("Save as a view (v)")
            }
        }
        .font(.meadow(.ui, 12.5))
        .foregroundStyle(meadow.ink3)
        .padding(.top, 8)
        .padding(.bottom, 2)
        if let count = MeadowListHead.count(app: app, mailbox: mailbox) {
            Text(count)
                .font(.meadow(.ui, 12.5))
                .foregroundStyle(meadow.ink3)
                .padding(.horizontal, 2)
        }
    }

    /// The conversation open in the reader, which a search can be held to (`m.conversation`).
    private var conversation: API.FolderRow? {
        guard !mailbox.marking, mailbox.chosenRows.count == 1, let row = mailbox.chosenRows.first,
              row.isConversation(grouped: mailbox.grouped) else { return nil }
        return row
    }
}

/// A lane's head (`client-ctx`): what it is, and where it searches.
private struct MeadowLaneHead: View {
    let mailbox: Mailbox
    let search: Search
    let lane: Lane
    @Environment(\.meadow) private var meadow

    var body: some View {
        MeadowHeadLine(sentence: lane == .feed ? String(localized: "Newsletters and digests. Nothing here asks anything of you.")
                           : String(localized: "Receipts and invoices, with what each one cost.")) {
            MeadowScopeMenu(mailbox: mailbox, account: search.account)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .font(.meadow(.ui, 12.5))
        .foregroundStyle(meadow.ink3)
        .padding(.top, 8)
        .padding(.horizontal, 2)
    }
}

/// A head's sentence and what follows it (`.lsub`, `client-ctx`): on one line with a "·" between where they fit, as
/// the page's line runs on; else the sentence wrapped as it needs and what follows on a line of its own, so no line
/// starts with a "·" and nothing stands squeezed beside a sentence.
struct MeadowHeadLine<Trailing: View>: View {
    let sentence: String
    @ViewBuilder let trailing: () -> Trailing

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 4) {
                Text(sentence)
                Text(verbatim: "·")
                trailing()
            }
            .fixedSize()
            VStack(alignment: .leading, spacing: 2) {
                Text(sentence)
                    .fixedSize(horizontal: false, vertical: true)
                trailing()
            }
        }
    }
}

/// Every account, or the one the search was asked from (`ScopeToggle`).
private struct MeadowScopeMenu: View {
    let mailbox: Mailbox
    let account: String

    var body: some View {
        if account.isEmpty {
            Text("all accounts")
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

// MARK: The pane's bar

/// What the pane's writing acts on, as the reader works it out: the message a reply answers, or a draft to go on with.
struct PaneWriting: Equatable {
    var replyTo: MailCopy?
    var draft: MailCopy?
}

/// The reading pane's bar (`.pbar`, `paneBar()`): over a message, its triage and its writing with their keys (in
/// Trash, Restore in their place; in Archive, Move to Inbox in File's), then reading settings and focus, and where it
/// stands in the list with the next and previous; over anything else only the last three.
struct MeadowPaneBar: View {
    let app: MailApp
    let mailbox: Mailbox
    /// A message is open, which the bar's verbs act on.
    var message = true
    var writing = PaneWriting()
    /// One fold alone, in place of those the pane's width gives: a test measures it.
    var only: PaneBarFold?
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowPaneWidth) private var width
    @Environment(MeadowShell.self) private var shell: MeadowShell?
    @Environment(Desk.self) private var desk: Desk?
    @State private var reading = false
    @State private var readingKeys = ReadingKeys()

    /// The bar's height: its groups' padding (the mockup's `.grp`, which the list's groups share) makes it 70 over a
    /// message and leaves it 52 over anything else.
    static func height(message: Bool) -> CGFloat { message ? 70 : 52 }

    var body: some View {
        // The fold the pane's width gives, else the first further one whose buttons all fit: a label never runs out
        // of room mid-word, and in the narrowest pane the bar folds to icons that stay inside it.
        ViewThatFits(in: .horizontal) {
            ForEach(only.map { [$0] } ?? PaneBarFold(width: width).further, id: \.self) { row($0) }
        }
        .padding(.horizontal, 10)
        .frame(height: Self.height(message: message))
        .overlay(alignment: .bottom) { Rectangle().fill(meadow.line).frame(height: 1) }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(message ? "Message actions" : "Reading pane")
    }

    /// The bar's row as `fold` folds it.
    private func row(_ fold: PaneBarFold) -> some View {
        HStack(spacing: 2) {
            if message, inTrash {
                group { restore(fold) }
            } else if message {
                group { triage(fold) }
                if fold.secondary { separator }
                group { write(fold) }
            }
            Spacer(minLength: 0)
            tools
            separator
            nav(fold)
        }
    }

    /// A group (`.grp`): its buttons set a little lower than the bar's middle, as the mockup's are.
    private func group<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        HStack(spacing: 2) { content() }
            .padding(.horizontal, 10)
            .padding(.top, 14 - 6)
    }

    private var separator: some View {
        Rectangle().fill(meadow.line).frame(width: 1, height: 22).padding(.horizontal, 6)
    }

    // MARK: Triage

    /// The bar's verbs act on the message in the pane alone: rows marked in the list beside it are the bulk bar's,
    /// the list's keys' and the Message menu's.
    @ViewBuilder private func triage(_ fold: PaneBarFold) -> some View {
        let none = app.selection.copy == nil
        if filed {
            verb(.inbox, "Move to Inbox", help: RowAct.toInbox.title, fold: fold) { mailbox.restoreOpen() }
                .disabled(none)
        } else {
            verb(.archive, "File", key: "E", help: "File to Archive · E", fold: fold) { mailbox.fileOpen(.archive) }
                .disabled(none)
        }
        verb(.trash, "Delete", key: "#", help: "Delete · # or Del", del: true, fold: fold) { mailbox.fileOpen(.trash) }
            .disabled(none)
        // The desk's verbs on the message in the pane.
        let commands = desk?.paneCommands ?? []
        let snooze = commands.first { $0.id == "snooze" }
        verb(.clock, "Snooze", key: "H", help: "Snooze · H", fold: fold) { snooze?.run() }
            .disabled(snooze == nil)
            // Its chooser opens under it, from here or from H.
            .meadowAnchor("snooze")
        if fold.secondary {
            let later = commands.first { $0.id == "later" }
            let done = desk?.stack == .replyLater
            verb(done ? .check : .bookmark, done ? "Done" : "Later",
                 help: done ? "Reply later — Done · L" : "Reply later · L", second: true, fold: fold) { later?.run() }
                .disabled(later == nil)
        }
    }

    /// The message open is filed already (Archive, or Gmail's All Mail), where File gives way to taking it back to
    /// the Inbox, as the mockup's Archive and a filed row's own button do.
    private var filed: Bool {
        guard let copy = app.selection.copy else { return false }
        let role = mailbox.folders(of: copy.account).first { $0.name == copy.folder }?.role
        return role == "archive" || role == "all"
    }

    // MARK: Trash

    /// The message open is in Trash, where the bar only takes it back out (the mockup's `m.loc === 'trash'`).
    private var inTrash: Bool {
        app.selection.copy.map(mailbox.inTrash) ?? false
    }

    /// Restore, to the account's Inbox as a row's own button does. The mockup's Delete forever has no place here:
    /// nothing in MailMind removes mail for good.
    private func restore(_ fold: PaneBarFold) -> some View {
        verb(.restore, "Restore", help: RowAct.restore.title, fold: fold) { mailbox.restoreOpen() }
            .disabled(app.selection.copy == nil)
    }

    // MARK: Writing

    @ViewBuilder private func write(_ fold: PaneBarFold) -> some View {
        let composing = Composing.shared
        if let draft = writing.draft {
            verb(.pen, "Edit Draft", help: "Edit draft", fold: fold) { Task { await composing.editDraft(draft) } }
        } else {
            let to = writing.replyTo
            switch to.flatMap({ composing.chips[$0.key] }) {
            case .ready?:
                verb(.wind, "Draft Ready", key: "R",
                     help: "AI pre-drafted a reply — opens in the composer for review; "
                         + "nothing sends until you do (⌘R)",
                     fold: fold) { if let to { Task { await composing.answer(to) } } }
                    .disabled(to == nil)
            case .drafting?:
                verb(.wind, "Drafting…",
                     help: "AI is drafting a reply in the background — click for a plain reply now", fold: fold) { if let to { Task { await composing.replyNow(to) } } }
                    .disabled(to == nil)
            case nil:
                verb(.reply, "Reply", key: "R", help: "Reply · R", fold: fold) {
                    if let to { Task { await composing.answer(to) } }
                }
                .disabled(to == nil)
            }
            if fold.secondary {
                verb(.replyAll, "All", help: "Reply all · A", second: true, fold: fold) {
                    if let to { Task { await composing.reply(to, all: true) } }
                }
                .disabled(to == nil)
                .accessibilityLabel("Reply all")
                verb(.forward, "Forward", help: "Forward · F", second: true, fold: fold) {
                    if let to { Task { await composing.forward(to) } }
                }
                .disabled(to == nil)
            }
        }
    }

    // MARK: Reading

    private var tools: some View {
        HStack(spacing: 2) {
            Button { reading.toggle() } label: { MeadowIconView(.aa) }
                .meadowButton(.quiet, icon: true)
                .help("Reading settings")
                .accessibilityLabel("Reading settings")
                .meadowPopover(isPresented: $reading, keys: { readingKeys.key($0) }) {
                    ReadingSettings(keys: readingKeys)
                        .task { await Composing.shared.readPreferences() }
                }
            let focus = shell?.focus ?? false
            Button { shell?.toggleFocus() } label: { MeadowIconView(focus ? .shrink : .expand) }
                .meadowButton(.quiet, icon: true, pressed: focus)
                .help(focus ? "Leave focus · Esc" : "Focus reading · Enter")
                .accessibilityLabel(focus ? "Leave focus reading (Esc)" : "Focus reading (Enter)")
                .disabled(shell == nil)
        }
    }

    @ViewBuilder private func nav(_ fold: PaneBarFold) -> some View {
        if fold.secondary {
            Text(verbatim: position ?? "")
                .font(.meadow(.ui, 12.5))
                .monospacedDigit()
                .foregroundStyle(meadow.ink3)
                .lineLimit(1)
                .fixedSize()
                .padding(.horizontal, 4)
        }
        Button { mailbox.move(by: -1) } label: { MeadowIconView(.up) }
            .meadowButton(.quiet, icon: true)
            .help("Previous · K")
            .accessibilityLabel("Previous message (K)")
            .disabled(app.list == nil)
        Button { mailbox.move(by: 1) } label: { MeadowIconView(.down) }
            .meadowButton(.quiet, icon: true)
            .help("Next · J")
            .accessibilityLabel("Next message (J)")
            .disabled(app.list == nil)
    }

    /// "3 of 120": the open message's place in the list, over a message.
    private var position: String? {
        guard message, let list = app.list, let at = list.chosenPosition else { return nil }
        let total = mailbox.facts.total ?? list.total
        return String(localized: "\(at + 1) of \(total)")
    }

    /// One of the bar's buttons: its icon, its words while there is room, and its key while there is more.
    private func verb(_ icon: MeadowIcon, _ title: String, key: String? = nil, help: String, del: Bool = false,
                      second: Bool = false, fold: PaneBarFold, run: @escaping () -> Void) -> some View {
        let words = fold.labels && (!second || fold.secondLabels)
        return Button(action: run) {
            MeadowIconView(icon)
            // Its words whole or not at all: the bar folds further rather than cut one short.
            if words { Text(title).fixedSize() }
            if words, fold.keys, let key { MeadowKey(key) }
        }
        .buttonStyle(MeadowButtonStyle(kind: .quiet, del: del, icon: !fold.labels))
        .focusEffectDisabled()
        .help(help)
        .accessibilityLabel(title)
    }
}
#endif
