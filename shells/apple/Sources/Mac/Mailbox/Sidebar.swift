#if os(macOS)
import SwiftUI
import UniformTypeIdentifiers

/// The rail (#44), as the mockup's `.rail` draws it and with the places the page's rail offers (app/FolderRail.tsx):
/// the accounts' button, then every place `RailPlaces` lists, then Help and Settings at its foot. Open, each place has
/// its icon, its name and its count; folded to icons (the frame's `meadowRailOpen`), the counts that need you stay as
/// badges, every account's gate is one, and the views and the folders wait behind a button each. The arrow keys go
/// from place to place while the rail has the focus.
struct MailboxSidebar: View {
    @Bindable var app: MailApp
    let mailbox: Mailbox
    @Environment(\.meadow) private var meadow
    @Environment(\.meadowRailOpen) private var open
    @Environment(Desk.self) private var desk: Desk?
    @Environment(MeadowShell.self) private var shell: MeadowShell?
    @Environment(\.openSettings) private var openSettings
    @Environment(\.openWindow) private var openWindow
    /// The accounts whose folders are folded away, by address.
    @State private var closed: Set<String> = []
    /// The accounts whose labels are shown.
    @State private var labelsOpen: Set<String> = []
    @State private var menuOpen = false
    /// The place rows are being dragged over, shown as chosen while they are.
    @State private var dropOn: String?
    @FocusState private var keyed: Bool

    var body: some View {
        let entries = RailPlaces.entries(mailbox, desk: desk?.rail ?? DeskRail(),
                                         scope: RailAccountButton.place(of: app)?.account, closed: closed,
                                         labelsOpen: labelsOpen)
        let shown = entries.filter { $0.shows(open: open) }
        let current = RailPlaces.current(app.place, in: entries, open: open)
        VStack(spacing: 0) {
            ScrollViewReader { scroller in
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        RailAccountButton(mailbox: mailbox, place: RailAccountButton.place(of: app), open: open,
                                          menuOpen: $menuOpen,
                                          unfold: unfold)
                        Color.clear.frame(height: 8)
                        ForEach(shown) { entry in
                            line(entry, current: current)
                                .id(entry.id)
                        }
                    }
                    .padding(.vertical, 10)
                    .padding(.horizontal, 8)
                }
                .scrollIndicators(.automatic)
                .focusable()
                .focused($keyed)
                .focusEffectDisabled()
                .onKeyPress(.downArrow) { step(1, entries: entries, scroller: scroller) }
                .onKeyPress(.upArrow) { step(-1, entries: entries, scroller: scroller) }
            }
            foot
        }
        .task(id: desk == nil) {
            // The Inbox counts what the brief says needs you, so its tallies are read once the rail shows.
            if let desk, !desk.brief.tallied { await desk.brief.keepTallied()?.value }
        }
        // VoiceOver finds it as the frame's Mailboxes, the group its panel or drawer is (`voiceOverGroup`).
    }

    /// Do what a place's menu offers (`RailPlaceAct`).
    private func run(_ act: RailPlaceAct, on place: Place) {
        switch act {
        case .open: app.place = place
        case .getMail: Task { await AppStatus.shared.syncNow() }
        case .refreshBrief:
            app.place = place
            if let desk { Task { await desk.brief.write(again: true) } }
        case .newFolder(let account): mailbox.folderAsk = .make(account: account)
        case .renameFolder(let account, let folder): mailbox.folderAsk = .rename(account: account, folder: folder)
        case .deleteFolder(let account, let folder): mailbox.folderAsk = .delete(account: account, folder: folder)
        case .removeView(let view): mailbox.removeView(view)
        }
    }

    @ViewBuilder private func line(_ entry: RailEntry, current: String?) -> some View {
        switch entry.kind {
        case .item(let item):
            RailPlace(item: item, open: open, current: current == entry.id || dropOn == entry.id,
                      keyed: keyed && current == entry.id) {
                app.place = item.place
            }
            .contextMenu {
                RailPlaceMenu(groups: RailPlaceAct.groups(for: item, mailbox: mailbox, ai: !(desk?.aiTurnedOff ?? true)),
                              syncing: AppStatus.shared.syncing) { run($0, on: item.place) }
            }
            .modifier(RailDrop(mailbox: mailbox, place: item.place, id: entry.id, on: $dropOn))
        case .head(let title):
            MeadowSectionLabel(title)
                .padding(EdgeInsets(top: 16, leading: 10, bottom: 6, trailing: 10))
        case .account(let account):
            RailAccountName(name: mailbox.name(of: account), address: account,
                            colour: meadow.account(mailbox.accounts.firstIndex(of: account) ?? 0),
                            folded: closed.contains(account)) {
                if closed.contains(account) { closed.remove(account) } else { closed.insert(account) }
            }
        case .labels(let account, let shown):
            RailFold(title: String(localized: "Labels"), icon: .tag, open: shown) {
                if shown { labelsOpen.remove(account) } else { labelsOpen.insert(account) }
            }
        case .separator:
            Rectangle().fill(meadow.line).frame(height: 1).padding(.vertical, 8).padding(.horizontal, 6)
                .accessibilityHidden(true)
        case .views:
            RailPlace(item: RailItem(place: .allInboxes, icon: .search, title: String(localized: "Views")),
                      open: false, current: current == entry.id, keyed: keyed && current == entry.id, action: unfold)
        case .folders:
            RailPlace(item: RailItem(place: .allInboxes, icon: .folder, title: String(localized: "Folders")),
                      open: false, current: current == entry.id, keyed: keyed && current == entry.id, action: unfold)
        }
    }

    /// The foot (`.rail-foot`): Help, and Settings; in a column of icons while the rail is folded.
    private var foot: some View {
        let layout = open ? AnyLayout(HStackLayout(spacing: 4)) : AnyLayout(VStackLayout(spacing: 4))
        return layout {
            Button { openWindow(id: HelpWindow.id) } label: {
                MeadowIconView(.book)
                if open {
                    Text("Help").frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .meadowButton(.quiet, icon: !open)
            .frame(width: open ? nil : 44)
            .help("MailMind Help")
            .accessibilityLabel("Help")
            Button { openSettings() } label: { MeadowIconView(.sliders) }
                .meadowButton(.quiet, icon: true)
                .frame(width: open ? nil : 44)
                .help("Settings (⌘,)")
                .accessibilityLabel("Settings")
        }
        .padding(8)
        .frame(maxWidth: .infinity)
        .overlay(alignment: .top) { Rectangle().fill(meadow.line).frame(height: 1) }
    }

    /// Show the rail's names: the folded rail's Views and Folders buttons and its accounts' button open it.
    private func unfold() {
        shell?.railOpen = true
    }

    /// The next or the previous place the rail shows, chosen, as a Mac's sidebar goes by its arrow keys.
    private func step(_ by: Int, entries: [RailEntry], scroller: ScrollViewProxy) -> KeyPress.Result {
        guard !RailPlaces.places(entries, open: open).isEmpty else { return .ignored }
        if let next = RailPlaces.step(from: app.place, by: by, in: entries, open: open) {
            app.place = next.place
            scroller.scrollTo(next.id)
        }
        return .handled
    }
}

/// A folder of the rail's that takes filing takes rows dragged from the list: they move there (`Mailbox.drop`).
private struct RailDrop: ViewModifier {
    let mailbox: Mailbox
    let place: Place
    let id: String
    @Binding var on: String?

    func body(content: Content) -> some View {
        if case .folder(let account, let name) = place,
           let folder = mailbox.folders(of: account).first(where: { $0.name == name }), folder.takesFiling
        {
            content.onDrop(of: [.plainText], isTargeted: Binding(get: { on == id }, set: { over in
                if over, mailbox.takesDrop(folder, of: account) { on = id } else if on == id { on = nil }
            })) { _ in
                on = nil
                return mailbox.drop(on: folder, of: account)
            }
        } else {
            content
        }
    }
}

/// A place in the rail (`.nav-i`): its icon, its name and its count, 36 tall, the current one in the primary's wash;
/// folded, only the icon, 44 tall, with a badge for a count that needs you.
private struct RailPlace: View {
    let item: RailItem
    let open: Bool
    let current: Bool
    /// The rail has the keyboard's focus, on this place.
    let keyed: Bool
    let action: () -> Void
    @Environment(\.meadow) private var meadow
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 11) {
                MeadowIconView(item.icon)
                if open {
                    Text(item.title)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    count
                }
            }
            .font(.meadow(.ui, 14, current ? .heavy : .semibold))
            .foregroundStyle(current ? meadow.primaryInk : hovering ? meadow.ink : meadow.ink2)
            .padding(.horizontal, open ? 10 : 0)
            .frame(maxWidth: .infinity, minHeight: open ? 36 : 44)
            .background(RoundedRectangle(cornerRadius: 10)
                .fill(current ? meadow.primaryWash : hovering ? meadow.paper3 : .clear))
            .overlay(alignment: .topTrailing) { if !open { badge } }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focusEffectDisabled()
        .meadowFocusRing(keyed, radius: 10)
        .onHover { hovering = $0 }
        .help(open ? "" : item.title)
        .accessibilityLabel(Text(item.title))
        .accessibilityValue(Text(said))
        .accessibilityAddTraits(current ? .isSelected : [])
    }

    /// The count beside the name (`.ct`): in the quietest ink, or in sunlight with its dot.
    @ViewBuilder private var count: some View {
        switch item.count {
        case .none:
            EmptyView()
        case .plain(let n), .badge(let n):
            Text(verbatim: "\(n)")
                .font(.meadow(.ui, 12, .bold))
                .monospacedDigit()
                .foregroundStyle(meadow.ink3)
        case .sun(let n):
            HStack(spacing: 5) {
                Circle().fill(meadow.sun).frame(width: 7, height: 7)
                Text(verbatim: "\(n)")
            }
            .font(.meadow(.ui, 12, .bold))
            .monospacedDigit()
            .foregroundStyle(meadow.sunInk)
        }
    }

    /// The folded rail's badge, top right: only a count that needs you, or the senders at the gate.
    @ViewBuilder private var badge: some View {
        let (n, sun): (Int?, Bool) = switch item.count {
        case .sun(let n): (n, true)
        case .badge(let n): (n, false)
        case .none, .plain: (nil, false)
        }
        if let n {
            Text(verbatim: "\(n)")
                .font(.meadow(.ui, 10, .bold))
                .monospacedDigit()
                .foregroundStyle(sun ? Color(meadow: 0x2A1A00) : meadow.ink2)
                .padding(.horizontal, 4)
                .frame(minWidth: 16, minHeight: 16)
                .background(Capsule().fill(sun ? meadow.sun : meadow.paper3))
                .background(Capsule().fill(meadow.paper).padding(-2))
                .padding(.top, 2)
                .accessibilityHidden(true)
        }
    }

    private var said: String {
        switch item.count {
        case .none: ""
        case .plain(let n), .badge(let n): "\(n)"
        case .sun(let n): String(localized: "\(n) need you")
        }
    }
}

/// An account's name over its folders (`.folder-acct`), in its colour's dot. Pressing it folds its folders away, or
/// brings them back.
private struct RailAccountName: View {
    let name: String
    let address: String
    let colour: Color
    let folded: Bool
    let toggle: () -> Void
    @Environment(\.meadow) private var meadow
    @State private var hovering = false
    @FocusState private var focused: Bool

    var body: some View {
        Button(action: toggle) {
            HStack(spacing: 8) {
                Circle().fill(colour).frame(width: 8, height: 8)
                Text(name).lineLimit(1)
                Spacer(minLength: 0)
                if hovering || folded || focused {
                    MeadowIconView(folded ? .right : .down, size: 13)
                        .foregroundStyle(meadow.ink3)
                }
            }
            .font(.meadow(.ui, 12.5, .bold))
            .foregroundStyle(meadow.ink2)
            .frame(minHeight: 14)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused)
        .onHover { hovering = $0 }
        .padding(EdgeInsets(top: 10, leading: 10, bottom: 2, trailing: 10))
        .help(address)
        .accessibilityLabel(Text(name))
        .accessibilityValue(Text(folded ? "Folded" : "Shown"))
        .accessibilityHint(Text(folded ? "Shows its folders" : "Hides its folders"))
    }
}

/// A fold in an account's places: its labels, behind one line that opens and closes them.
private struct RailFold: View {
    let title: String
    let icon: MeadowIcon
    let open: Bool
    let toggle: () -> Void
    @Environment(\.meadow) private var meadow
    @State private var hovering = false
    @FocusState private var focused: Bool

    var body: some View {
        Button(action: toggle) {
            HStack(spacing: 11) {
                MeadowIconView(icon)
                Text(title).frame(maxWidth: .infinity, alignment: .leading)
                MeadowIconView(open ? .down : .right, size: 15).foregroundStyle(meadow.ink3)
            }
            .font(.meadow(.ui, 14, .semibold))
            .foregroundStyle(hovering ? meadow.ink : meadow.ink2)
            .padding(.horizontal, 10)
            .frame(maxWidth: .infinity, minHeight: 36)
            .background(RoundedRectangle(cornerRadius: 10).fill(hovering ? meadow.paper3 : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .focused($focused)
        .focusEffectDisabled()
        .meadowFocusRing(focused, radius: 10)
        .onHover { hovering = $0 }
        .accessibilityValue(Text(open ? "Shown" : "Hidden"))
    }
}

/// What a rail place's menu offers on a right-click, each a thing a route does: open it; get new mail where mail
/// shows; write the brief again, with AI on; a new folder in an account's folders; renaming or deleting one the
/// server says a person may change; removing a view of their own. Nothing the core has no route for, such as
/// marking a whole folder read.
enum RailPlaceAct: Equatable {
    case open
    case getMail
    case refreshBrief
    case newFolder(account: String)
    case renameFolder(account: String, folder: API.RailFolder)
    case deleteFolder(account: String, folder: API.RailFolder)
    case removeView(API.ListedView)

    var title: String {
        switch self {
        case .open: String(localized: "Open")
        case .getMail: String(localized: "Get New Mail")
        case .refreshBrief: String(localized: "Write the Brief Again")
        case .newFolder: String(localized: "New Folder…")
        case .renameFolder: String(localized: "Rename Folder…")
        case .deleteFolder: String(localized: "Delete Folder…")
        case .removeView: String(localized: "Remove View")
        }
    }

    var destructive: Bool {
        switch self {
        case .deleteFolder, .removeView: true
        default: false
        }
    }

    /// The menu of `item`'s place, in groups a divider parts: opening it, then what reaches the server or the AI, then
    /// what changes the place itself.
    @MainActor
    static func groups(for item: RailItem, mailbox: Mailbox, ai: Bool) -> [[RailPlaceAct]] {
        var groups: [[RailPlaceAct]] = [[.open]]
        switch item.place {
        case .desk(.brief): if ai { groups.append([.refreshBrief]) }
        case .desk(.ask): break
        default: groups.append([.getMail])
        }
        var changes: [RailPlaceAct] = []
        if case .folder(let account, _) = item.place {
            changes.append(.newFolder(account: account))
            if let (account, folder) = mailbox.changeableFolder(item.place) {
                changes += [.renameFolder(account: account, folder: folder), .deleteFolder(account: account, folder: folder)]
            }
        }
        if let view = item.removable { changes.append(.removeView(view)) }
        if !changes.isEmpty { groups.append(changes) }
        return groups
    }
}

/// A rail place's menu (`RailPlaceAct`).
struct RailPlaceMenu: View {
    let groups: [[RailPlaceAct]]
    /// A sync already running: Get New Mail waits for it.
    let syncing: Bool
    let run: (RailPlaceAct) -> Void

    var body: some View {
        ForEach(Array(groups.enumerated()), id: \.offset) { index, group in
            if index > 0 { Divider() }
            ForEach(Array(group.enumerated()), id: \.offset) { _, act in
                Button(act.title, role: act.destructive ? .destructive : nil) { run(act) }
                    .disabled(act == .getMail && syncing)
            }
        }
    }
}
#endif
